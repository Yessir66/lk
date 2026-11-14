#!/usr/bin/env python3
"""Read-only Solana trade alerts. No wallet keys, orders, pump.fun, or AI calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import ssl
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, quote

from discord_token_analysis import WATCHED, QUOTES, TRADE_LOG, ReadRPC, parse_trade, valid_address, paris
from discord_token_telegram import TelegramSender, amounts
from discord_solana_watch import StateLock

ROOT=Path(__file__).resolve().parents[1]
VERSION='2026.11.14.5'
BACKFILL_PAGE_SIZE=1000
BACKFILL_MAX_PAGES=10
RPC_PHASES=('rpc_lock_wait_ms','cooldown_wait_ms','scheduler_wait_ms','rate_limit_wait_ms','http_json_ms')


class MeasuredRPC(ReadRPC):
    """Same read-only transport and rate limit, with the local sleep measured separately."""
    def call(self,method,params):
        self.last_timing=None
        start=time.monotonic()
        time.sleep(max(0,self.next_at-start))
        request_start=time.monotonic()
        try:return super().call(method,params)
        finally:
            end=time.monotonic()
            self.last_timing={'rate_limit_wait_ms':max(0,request_start-start)*1000,
                              'http_json_ms':max(0,end-request_start)*1000}


def failure_label(exc):
    """Keep status codes, never exception text or provider response bodies."""
    values=(getattr(exc,'status',None),getattr(exc,'status_code',None),
            getattr(getattr(exc,'response',None),'status_code',None))
    status=next((v for v in values if isinstance(v,int) and not isinstance(v,bool)),None)
    return type(exc).__name__+(f' ; HTTP {status}' if status is not None else '')


async def diagnose(http,ws_url,wallet):
    from websockets.asyncio.client import connect
    rpc=ReadRPC(http,None,rps=2)
    try:
        await asyncio.to_thread(rpc.call,'getSignaturesForAddress',[wallet,{'limit':1,'commitment':'confirmed'}])
        print('RPC HTTPS : OK (lecture seule).',flush=True)
    except Exception as exc:print('RPC HTTPS : '+failure_label(exc),flush=True)
    try:
        async with connect(ws_url,ssl=ssl.create_default_context(),open_timeout=15,
                           ping_interval=20,ping_timeout=20,max_size=2**20) as ws:
            await ws.send(json.dumps({'jsonrpc':'2.0','id':1,'method':'logsSubscribe',
                'params':[{'mentions':[wallet]},{'commitment':'confirmed'}]}))
            reply=json.loads(await asyncio.wait_for(ws.recv(),15))
            if isinstance(reply.get('result'),int):print('WebSocket : OK ; abonnement confirmed accepté.',flush=True)
            else:
                error=reply.get('error') or {};code=error.get('code') if isinstance(error,dict) else None
                print('WebSocket : abonnement refusé'+(f' (code RPC {code})' if isinstance(code,int) else '')+'.',flush=True)
    except Exception as exc:print('WebSocket : '+failure_label(exc),flush=True)
    print('Diagnostic terminé ; aucune transaction envoyée, aucun message Telegram.',flush=True)


def settings(path):
    value=json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    if not isinstance(value,dict):raise ValueError('Configuration attendue : objet JSON')
    wallets=value.get('wallets',WATCHED)
    if (not isinstance(wallets,dict) or not 1<=len(wallets)<=20 or
        any(not isinstance(n,str) or not 1<=len(n)<=40 or not valid_address(w) for n,w in wallets.items()) or
        len(set(wallets.values()))!=len(wallets)):
        raise ValueError('Wallets : 1 à 20 noms distincts et adresses Solana uniques')
    key=os.getenv('HELIUS_API_KEY') or value.get('helius_api_key')
    if key is not None and (not isinstance(key,str) or not key.strip()):raise ValueError('Clé Helius invalide')
    http=os.getenv('SOLANA_RPC_URL') or value.get('rpc_http_url')
    if not http and key:http='https://mainnet.helius-rpc.com/?api-key='+quote(key.strip(),safe='')
    ws=os.getenv('SOLANA_WS_URL') or value.get('rpc_ws_url')
    if not ws and isinstance(http,str):ws='wss://'+http[len('https://'):] if http.startswith('https://') else None
    for url,scheme in ((http,'https'),(ws,'wss')):
        if not isinstance(url,str):raise ValueError('Renseigner Helius ou les URL HTTPS et WSS du RPC')
        parsed=urlsplit(url)
        if parsed.scheme!=scheme or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('RPC : URL HTTPS/WSS sans identifiant intégré requise')
    return value,dict(wallets),http,ws


def trades_in(tx,wallet,signature):
    meta=tx.get('meta') or {}
    mints={b.get('mint') for side in ('preTokenBalances','postTokenBalances') for b in meta.get(side,[])
           if b.get('owner')==wallet and valid_address(b.get('mint')) and b.get('mint') not in QUOTES}
    trades=[]
    for mint in sorted(mints):
        trade=parse_trade(tx,wallet,mint,signature)
        if trade:trades.append(trade)
    return trades


class Journal:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS events (
            wallet TEXT,signature TEXT,first_seen REAL,confirmed_seen REAL,slot INTEGER,source TEXT,
            state TEXT,attempts INTEGER DEFAULT 0,retry_at REAL DEFAULT 0,trades TEXT,error TEXT,
            PRIMARY KEY(wallet,signature));
          CREATE TABLE IF NOT EXISTS cursors (wallet TEXT PRIMARY KEY,signature TEXT);
          CREATE TABLE IF NOT EXISTS gaps (wallet TEXT,at REAL,reason TEXT);
          CREATE TABLE IF NOT EXISTS alerts (wallet TEXT,signature TEXT,mint TEXT,state TEXT,text TEXT,
            expires_at REAL,message_id INTEGER,error TEXT,PRIMARY KEY(wallet,signature,mint));
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT);
        ''')
        columns={row[1] for row in self.db.execute('PRAGMA table_info(events)')}
        for name in ('processed_seen','confirmed_ws_seen','processed_to_confirmed_ms','confirmed_to_decode_ms'):
            if name not in columns:self.db.execute('ALTER TABLE events ADD COLUMN '+name+' REAL')
        if 'rpc_profile' not in columns:self.db.execute('ALTER TABLE events ADD COLUMN rpc_profile TEXT')
        self.timings={}
        self.db.execute("UPDATE alerts SET state='uncertain',error='Interruption pendant envoi ; aucun renvoi automatique' WHERE state='sending'")
        self.db.commit()

    def begin_connection(self):
        # Monotonic timestamps from different connections/process lifetimes are never compared.
        self.timings.clear()

    def observe(self,wallet,signature,at,slot,source,confirmed,*,commit=True,stage=None,monotonic=None):
        old=self.db.execute('SELECT processed_seen,confirmed_ws_seen,confirmed_seen FROM events WHERE wallet=? AND signature=?',
                            (wallet,signature)).fetchone() if stage else None
        fresh=stage in ('processed','confirmed') and (old is None or old[0 if stage=='processed' else 1] is None)
        self.db.execute('''INSERT INTO events (wallet,signature,first_seen,confirmed_seen,slot,source,state)
          VALUES (?,?,?,?,?,?,?) ON CONFLICT(wallet,signature) DO UPDATE SET
          first_seen=MIN(events.first_seen,excluded.first_seen),
          source=CASE WHEN events.confirmed_seen IS NULL AND excluded.confirmed_seen IS NOT NULL
                      THEN excluded.source ELSE events.source END,
          confirmed_seen=COALESCE(events.confirmed_seen,excluded.confirmed_seen),
          state=CASE WHEN events.state='provisional' AND excluded.confirmed_seen IS NOT NULL
                     THEN 'pending' ELSE events.state END''',
          (wallet,signature,at,at if confirmed else None,slot,source,'pending' if confirmed else 'provisional'))
        if fresh:
            column='processed_seen' if stage=='processed' else 'confirmed_ws_seen'
            self.db.execute('UPDATE events SET '+column+'=? WHERE wallet=? AND signature=?',(at,wallet,signature))
            if monotonic is not None:
                times=self.timings.setdefault((wallet,signature),{})
                times[stage]=monotonic
                if 'processed' in times and 'confirmed' in times:
                    delta=(times['confirmed']-times['processed'])*1000
                    self.db.execute('UPDATE events SET processed_to_confirmed_ms=? WHERE wallet=? AND signature=?',
                                    (delta,wallet,signature))
                while len(self.timings)>20000:self.timings.pop(next(iter(self.timings)))
        if commit:self.db.commit()
        return bool(fresh and stage=='processed' and (old is None or old[2] is None))

    def timing(self,wallet,signature,decoded_mono):
        row=self.db.execute('SELECT processed_seen,confirmed_ws_seen,processed_to_confirmed_ms FROM events WHERE wallet=? AND signature=?',
                            (wallet,signature)).fetchone()
        times=self.timings.get((wallet,signature),{})
        return dict(processed_seen_at=row[0],confirmed_ws_seen_at=row[1],processed_to_confirmed_ms=row[2],
                    confirmed_to_decode_ms=(decoded_mono-times['confirmed'])*1000 if 'confirmed' in times else None)

    def finish(self,wallet,signature,trades,alerts=(),profile=None):
        self.db.execute("UPDATE events SET state='done',trades=?,error=NULL,rpc_profile=? WHERE wallet=? AND signature=?",
                        (json.dumps(trades),json.dumps(profile) if profile is not None else None,wallet,signature))
        if trades:self.db.execute('UPDATE events SET confirmed_to_decode_ms=? WHERE wallet=? AND signature=?',
                                  (trades[0].get('confirmed_to_decode_ms'),wallet,signature))
        for mint,text,expiry in alerts:
            self.db.execute("INSERT OR IGNORE INTO alerts (wallet,signature,mint,state,text,expires_at) VALUES (?,?,?,'pending',?,?)",
                            (wallet,signature,mint,text,expiry))
        self.db.commit()

    def due(self,now):
        return self.db.execute("SELECT wallet,signature,first_seen,confirmed_seen,source,attempts FROM events WHERE state='pending' AND retry_at<=? ORDER BY CASE source WHEN 'websocket' THEN 0 ELSE 1 END,confirmed_seen LIMIT 1",(now,)).fetchone()

    def retry(self,wallet,signature,attempts,reason,now,profile=None):
        self.db.execute('UPDATE events SET state=?,attempts=?,retry_at=?,error=?,rpc_profile=? WHERE wallet=? AND signature=?',
          ('unavailable' if attempts>=12 else 'pending',attempts,now+min(30,2**min(attempts,5)),reason,
           json.dumps(profile) if profile is not None else None,wallet,signature))
        self.db.commit()


def notification(journal,message,subscriptions,now,*,monotonic=None,names=None):
    if message.get('method')!='logsNotification':return
    params=message.get('params') or {};target=subscriptions.get(params.get('subscription'))
    if target is None:return
    wallet,commitment=target;result=params.get('result') or {};value=result.get('value') or {}
    signature=value.get('signature')
    if value.get('err') is not None or not isinstance(signature,str) or not 64<=len(signature)<=100:return
    # Logs identify candidates only. The transaction decoder must prove the actual swap.
    if not any(TRADE_LOG.search(line) for line in value.get('logs',[]) if isinstance(line,str)):return
    provisional=journal.observe(wallet,signature,now,(result.get('context') or {}).get('slot'),'websocket',commitment=='confirmed',
                                stage=commitment,monotonic=time.monotonic() if monotonic is None else monotonic)
    if provisional:
        print('TRANSACTION PROVISOIRE mentionnant '+(names or {}).get(wallet,wallet[:8])+
              ' — '+precise_time(now)+' ; sens et montants à vérifier.\nhttps://solscan.io/tx/'+signature,flush=True)


class Service:
    def __init__(self,journal,wallets,rpc,ws_url,*,sender=None,early=False,max_age=120):
        self.journal,self.wallets,self.rpc,self.ws_url=journal,wallets,rpc,ws_url
        self.names={w:n for n,w in wallets.items()};self.sender=sender;self.early=early;self.max_age=max_age
        self.rpc_lock=asyncio.Lock();self.recover=asyncio.Event();self.pause_until=0
        self.profiles={}
        if sender:
            previous=journal.db.execute("SELECT value FROM meta WHERE key='telegram_destination'").fetchone()
            if previous and previous[0]!=sender.destination:raise ValueError('Destination Telegram modifiée : utiliser un autre journal')
            journal.db.execute("INSERT OR IGNORE INTO meta VALUES ('telegram_destination',?)",(sender.destination,));journal.db.commit()

    async def call(self,method,params,*,metrics=None):
        started=time.monotonic()
        async with self.rpc_lock:
            acquired=time.monotonic()
            delay=self.pause_until-time.monotonic()
            if delay>0:await asyncio.sleep(delay)
            dispatched=time.monotonic();thread_times={}
            def invoke():
                thread_times['start']=time.monotonic()
                try:return self.rpc.call(method,params)
                finally:
                    thread_times['end']=time.monotonic()
                    if metrics is not None and isinstance(getattr(self.rpc,'last_timing',None),dict):
                        metrics.update(self.rpc.last_timing)
            try:return await asyncio.to_thread(invoke)
            except Exception as exc:
                if getattr(exc,'status',None)==429:self.pause_until=time.monotonic()+60
                raise
            finally:
                if metrics is not None:
                    resumed=time.monotonic()
                    metrics['rpc_lock_wait_ms']=max(0,acquired-started)*1000
                    metrics['cooldown_wait_ms']=max(0,dispatched-acquired)*1000
                    if 'start' in thread_times and 'end' in thread_times:
                        metrics['scheduler_wait_ms']=max(0,(resumed-dispatched)-(thread_times['end']-thread_times['start']))*1000

    async def websocket(self):
        from websockets.asyncio.client import connect
        failures=0
        while True:
            try:
                async with connect(self.ws_url,ssl=ssl.create_default_context(),open_timeout=20,
                                   ping_interval=20,ping_timeout=20,max_size=2**22) as ws:
                    self.journal.begin_connection()
                    requests={};subscriptions={};identity=0
                    for wallet in self.names:
                        for commitment in (('processed','confirmed') if self.early else ('confirmed',)):
                            identity+=1;requests[identity]=(wallet,commitment)
                            await ws.send(json.dumps({'jsonrpc':'2.0','id':identity,'method':'logsSubscribe',
                              'params':[{'mentions':[wallet]},{'commitment':commitment}]}))
                    deadline=time.monotonic()+20
                    while requests:
                        message=json.loads(await asyncio.wait_for(ws.recv(),max(.01,deadline-time.monotonic())))
                        if 'id' in message:
                            target=requests.pop(message['id'],None)
                            if target is None or not isinstance(message.get('result'),int):
                                raise ValueError('Abonnement RPC refusé')
                            subscriptions[message['result']]=target
                        else:notification(self.journal,message,subscriptions,time.time(),names=self.names)
                    failures=0;print(f'WEBSOCKET CONNECTÉ : {len(self.names)} wallets ; {len(subscriptions)} abonnements.',flush=True)
                    self.recover.set()
                    async for raw in ws:notification(self.journal,json.loads(raw),subscriptions,time.time(),names=self.names)
                    raise ConnectionError('WebSocket fermé')
            except asyncio.CancelledError:raise
            except Exception as exc:
                failures+=1
                # Never log connection exception text: it may contain the API key in the URL.
                print(f'WebSocket : reconnexion ({failure_label(exc)}) dans {min(30,2**min(failures,5))} s.',flush=True)
                await asyncio.sleep(min(30,2**min(failures,5)))

    async def backfill_wallet(self,wallet):
        db=self.journal.db;cursor=db.execute('SELECT signature FROM cursors WHERE wallet=?',(wallet,)).fetchone()
        anchor=cursor[0] if cursor else None;before=None;newest=None;found=False
        for page in range(BACKFILL_MAX_PAGES):
            options={'limit':BACKFILL_PAGE_SIZE if cursor else 1,'commitment':'confirmed'}
            if before:options['before']=before
            rows=await self.call('getSignaturesForAddress',[wallet,options])
            if not rows:found=True;break
            newest=newest or rows[0]['signature']
            if not cursor:found=True;break  # First boot starts now, without replaying old alerts.
            for row in rows:
                if row['signature']==anchor:found=True;break
                if row.get('err') is None:
                    self.journal.observe(wallet,row['signature'],time.time(),row.get('slot'),'rattrapage',True,commit=False)
            db.commit()  # One disk commit per page; keep the WebSocket event loop responsive.
            if found or len(rows)<BACKFILL_PAGE_SIZE:found=True;break
            before=rows[-1]['signature']
        if not found:
            maximum=BACKFILL_PAGE_SIZE*BACKFILL_MAX_PAGES
            db.execute('INSERT INTO gaps VALUES (?,?,?)',(wallet,time.time(),f'Rattrapage limité à {maximum} signatures'))
            print('HISTORIQUE PARTIEL : '+self.names[wallet]+f' ; rattrapage limité à {maximum} signatures.',flush=True)
        if newest or not cursor:db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?)',(wallet,newest or ''))
        db.commit()

    async def backfill(self):
        while True:
            # Periodic reconciliation also protects against a missed notification without disconnect.
            try:await asyncio.wait_for(self.recover.wait(),60)
            except asyncio.TimeoutError:pass
            self.recover.clear()
            for wallet in self.names:
                try:await self.backfill_wallet(wallet)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    print('Rattrapage différé : '+self.names[wallet]+' ('+type(exc).__name__+').',flush=True)

    async def decode_one(self,now):
        row=self.journal.due(now)
        if row is None:return False
        wallet,signature,first,confirmed,source,attempts=row
        key=(wallet,signature);started=time.monotonic()
        confirmation_mono=self.journal.timings.get(key,{}).get('confirmed')
        profile=self.profiles.get(key)
        if profile is None:
            profile={field:0.0 for field in RPC_PHASES}
            profile.update(queue_wait_ms=max(0,started-confirmation_mono)*1000 if confirmation_mono is not None and attempts==0 else None,
                           retry_wait_ms=0.0,attempts_profiled=0,prior_attempts=attempts,complete=attempts==0,
                           _confirmed=confirmation_mono,_last_end=None)
            self.profiles[key]=profile
        elif profile['_last_end'] is not None:
            profile['retry_wait_ms']+=max(0,started-profile['_last_end'])*1000
        metrics={};recorded=False
        def record_attempt():
            nonlocal recorded
            if recorded:return
            recorded=True;profile['attempts_profiled']+=1
            for field in RPC_PHASES:
                value=metrics.get(field)
                if isinstance(value,(int,float)) and value>=0:profile[field]+=value
                else:profile['complete']=False
        def public_profile():return {k:v for k,v in profile.items() if not k.startswith('_')}
        try:
            tx=await self.call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'confirmed',
                                                            'maxSupportedTransactionVersion':1}],metrics=metrics)
            record_attempt()
            if not tx:
                profile['_last_end']=time.monotonic()
                self.journal.retry(wallet,signature,attempts+1,'Transaction pas encore disponible',time.time(),public_profile())
                if attempts+1>=12:self.profiles.pop(key,None)
                return True
            decode_start=time.monotonic()
            trades=trades_in(tx,wallet,signature);decode_end=time.monotonic();decoded=time.time();block=tx.get('blockTime')
            timing=self.journal.timing(wallet,signature,decode_end)
            profile['decode_ms']=max(0,decode_end-decode_start)*1000
            same_clock=profile['_confirmed'] is not None and self.journal.timings.get(key,{}).get('confirmed')==profile['_confirmed']
            profile['complete']=profile['complete'] and same_clock and profile['queue_wait_ms'] is not None
            profile['total_ms']=(decode_end-profile['_confirmed'])*1000 if same_clock else None
            parts=[profile.get(k) for k in (*RPC_PHASES,'queue_wait_ms','retry_wait_ms','decode_ms')]
            profile['other_ms']=max(0,profile['total_ms']-sum(parts)) if profile['complete'] else None
            details=public_profile()
            for trade in trades:
                trade.update(first_seen_at=first,confirmed_seen_at=confirmed,decoded_at=decoded,source=source,
                             notification_to_decode_ms=round(max(0,decoded-first)*1000),
                             block_to_decode_ms=round((decoded-block)*1000) if isinstance(block,(int,float)) else None,
                             rpc_profile=details,**timing)
            alerts=[]
            for trade in trades:
                if trade['kind'] not in ('buy','sell') or trade.get('swap_proof_version')!=1:continue
                text=render(trade,self.names[wallet]);print(text,flush=True)
                if self.sender and isinstance(block,(int,float)) and 0<=decoded-block<=self.max_age:
                    alerts.append((trade['mint'],text,block+self.max_age))
            self.journal.finish(wallet,signature,trades,alerts,details);self.profiles.pop(key,None)
        except asyncio.CancelledError:raise
        except Exception as exc:
            record_attempt();profile['_last_end']=time.monotonic()
            self.journal.retry(wallet,signature,attempts+1,type(exc).__name__,time.time(),public_profile())
            if attempts+1>=12:self.profiles.pop(key,None)
            return True
        return True

    async def deliver_one(self,now):
        if not self.sender:return False
        db=self.journal.db
        row=db.execute("SELECT wallet,signature,mint,text,expires_at FROM alerts WHERE state='pending' ORDER BY rowid LIMIT 1").fetchone()
        if not row:return False
        wallet,signature,mint,text,expires=row;key=(wallet,signature,mint)
        if now>expires:
            db.execute("UPDATE alerts SET state='expired' WHERE wallet=? AND signature=? AND mint=?",key);db.commit();return True
        # Independent from decoding: a slow Telegram request never holds up new transactions.
        db.execute("UPDATE alerts SET state='sending' WHERE wallet=? AND signature=? AND mint=?",key);db.commit()
        try:
            message_id=await asyncio.to_thread(self.sender.send,text);state,error='sent',None
        except Exception as exc:state,error,message_id='uncertain',type(exc).__name__,None
        db.execute('UPDATE alerts SET state=?,error=?,message_id=? WHERE wallet=? AND signature=? AND mint=?',
                   (state,error,message_id,*key));db.commit()
        print('Telegram : '+state+' ; '+self.names.get(wallet,wallet[:8]),flush=True)
        return True

    async def notifier(self):
        while True:
            if not await self.deliver_one(time.time()):await asyncio.sleep(.1)

    async def worker(self):
        while True:
            if not await self.decode_one(time.time()):await asyncio.sleep(.1)

    async def run(self):
        tasks=[asyncio.create_task(fn()) for fn in (self.websocket,self.backfill,self.worker,self.notifier)]
        try:await asyncio.gather(*tasks)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)


def precise_time(value):
    return datetime.fromtimestamp(value,ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M:%S.%f')[:-3]+' (Paris)'


def timing_report(path):
    """Read-only report: do not recover/reset an active sender when inspecting its database."""
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        columns={row[1] for row in db.execute('PRAGMA table_info(events)')}
        if 'processed_to_confirmed_ms' not in columns:
            print('Journal sans mesures processed/confirmed ; lancer la nouvelle version avec --early.');return
        values=[];decode=[];verified=0
        for trades,gap,delay in db.execute('SELECT trades,processed_to_confirmed_ms,confirmed_to_decode_ms FROM events WHERE state=\'done\''):
            if not any(t.get('kind') in ('buy','sell') and t.get('swap_proof_version')==1 for t in json.loads(trades or '[]')):continue
            verified+=1
            if isinstance(gap,(int,float)):values.append(gap)
            if isinstance(delay,(int,float)):decode.append(delay)
        print(f'Transactions vérifiées : {verified} ; paires processed/confirmed du même branchement : {len(values)}.')
        for name,items in (('Processed → confirmed',values),('Confirmed → décodage',decode)):
            if not items:print(name+' : pas encore de mesure.');continue
            items.sort()
            def percentile(p):
                index=(len(items)-1)*p;lo=int(index);hi=min(lo+1,len(items)-1)
                return items[lo]+(items[hi]-items[lo])*(index-lo)
            print(f'{name} : médiane {seconds(percentile(.5))} ; P95 {seconds(percentile(.95))} ; n={len(items)}.')
        pending=db.execute("SELECT count(*) FROM events WHERE state='provisional'").fetchone()[0]
        gaps=db.execute('SELECT count(*) FROM gaps').fetchone()[0]
        print(f'Provisoires sans confirmation enregistrée : {pending} ; fenêtres de rattrapage incomplètes : {gaps}.')
        if 'rpc_profile' in columns:
            profiles=[]
            for trades,raw in db.execute("SELECT trades,rpc_profile FROM events WHERE state='done' AND rpc_profile IS NOT NULL"):
                if not any(t.get('kind') in ('buy','sell') and t.get('swap_proof_version')==1 for t in json.loads(trades or '[]')):continue
                profile=json.loads(raw)
                if profile.get('complete'):profiles.append(profile)
            print(f'Profils détaillés complets : {len(profiles)} (mesures depuis cette mise à jour).')
            labels=(('queue_wait_ms','File avant première lecture'),('rpc_lock_wait_ms','Attente accès RPC partagé'),
                    ('rate_limit_wait_ms','Régulateur local 2 requêtes/s'),('cooldown_wait_ms','Pause après HTTP 429'),
                    ('scheduler_wait_ms','Ordonnancement des lectures'),('http_json_ms','RPC HTTP + JSON (cumul essais)'),
                    ('retry_wait_ms','Attente entre essais'),('decode_ms','Décodage des swaps'),
                    ('other_ms','Autres opérations locales'),('total_ms','Total confirmed → décodage'))
            for field,label in labels:
                items=sorted(p[field] for p in profiles if isinstance(p.get(field),(int,float)))
                if not items:continue
                def pct(p):
                    i=(len(items)-1)*p;lo=int(i);hi=min(lo+1,len(items)-1)
                    return items[lo]+(items[hi]-items[lo])*(i-lo)
                print(f'{label} : médiane {seconds(pct(.5))} ; P95 {seconds(pct(.95))}.')
            if profiles:
                print('Profils avec réessai : '+str(sum(p['attempts_profiled']>1 for p in profiles))+'. Les médianes des étapes ne s’additionnent pas.')
        print('Mesures locales, hors livraison Telegram ; ni heure exacte d’exécution ni garantie de latence.')
    finally:db.close()


def seconds(milliseconds):
    return f'{milliseconds/1000:.2f}'.replace('.',',')+' s'


def render(trade,name):
    verb='ACHAT' if trade['kind']=='buy' else 'VENTE'
    at=paris(trade['at'])+' (Paris)' if isinstance(trade.get('at'),(int,float)) else 'indisponible'
    lines=[f'{verb} VÉRIFIÉ — {name}',trade['mint'],'Transaction : '+at,
           'Quantité : '+trade['quantity']+' tokens',
           ('Payé : ' if verb=='ACHAT' else 'Reçu : ')+amounts(trade['quotes']),
           'Réception notification → décodage : '+str(trade['notification_to_decode_ms'])+' ms',
           'Source : '+trade['source'],'https://solscan.io/tx/'+trade['signature']]
    delay=trade.get('block_to_decode_ms')
    if isinstance(delay,(int,float)):
        lines.insert(-2,'Block Solana → décodage (approximatif) : '+str(delay)+' ms')
    for field,label in (('processed_seen_at','Réception processed'),('confirmed_ws_seen_at','Réception confirmed')):
        if trade.get(field) is not None:lines.insert(-2,label+' : '+precise_time(trade[field]))
    gap=trade.get('processed_to_confirmed_ms')
    if gap is not None:
        lines.insert(-2,f'Écart local processed → confirmed : {seconds(gap)} (même connexion).')
        if gap<0:lines.insert(-2,'La notification processed est arrivée après confirmed pour cette transaction.')
    elif trade.get('processed_seen_at') is not None:
        lines.insert(-2,'Comparaison processed/confirmed indisponible : paire du même branchement absente.')
    delay=trade.get('confirmed_to_decode_ms')
    if delay is not None:lines.insert(-2,f'Réception confirmed → décodage : {seconds(delay)} (horloge monotone).')
    profile=trade.get('rpc_profile')
    if profile and profile.get('complete'):
        lines.insert(-2,'Détail : file '+seconds(profile['queue_wait_ms'])+' ; accès RPC '+seconds(profile['rpc_lock_wait_ms'])+
                     ' ; quota local '+seconds(profile['rate_limit_wait_ms'])+' ; RPC HTTP+JSON '+seconds(profile['http_json_ms'])+
                     ' ; attente réessais '+seconds(profile['retry_wait_ms'])+' ; décodage '+seconds(profile['decode_ms'])+
                     f' ; lectures {profile["attempts_profiled"]}.')
    if trade['source']=='rattrapage':lines.append('Événement retrouvé par rattrapage, pas par notification directe.')
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'wallet_alerts_config.json')
    parser.add_argument('--state',type=Path,default=ROOT/'data'/'wallet_alerts_ws'/'state.sqlite3')
    parser.add_argument('--check',action='store_true',help='Valider la configuration sans requête réseau')
    parser.add_argument('--diagnose',action='store_true',help='Tester HTTPS et un abonnement WSS, sans surveillance ni Telegram')
    parser.add_argument('--telegram',action='store_true',help='Activer explicitement les alertes Bot API')
    parser.add_argument('--early',action='store_true',help='Mesurer aussi les notifications processed ; jamais alertées avant confirmation')
    parser.add_argument('--timing-report',action='store_true',help='Afficher les mesures enregistrées sans réseau ni modification du journal')
    args=parser.parse_args()
    try:
        if args.timing_report:timing_report(args.state);return
        config,wallets,http,ws=settings(args.config)
        print('Wallet alerts WebSocket '+VERSION+' — lecture seule, aucun achat ni publication.',flush=True)
        print('Wallets : '+', '.join(wallets),flush=True)
        sender=None
        if args.telegram:
            token=os.getenv('TELEGRAM_BOT_TOKEN') or config.get('telegram_bot_token')
            chat=os.getenv('TELEGRAM_CHAT_ID') or config.get('telegram_chat_id')
            if not isinstance(token,str) or not token or not isinstance(chat,(str,int)) or not str(chat):
                raise ValueError('Telegram : token du bot et chat_id requis dans la configuration locale')
            sender=TelegramSender(token,chat)
        if args.check:
            print('Configuration valide ; aucune connexion effectuée. URL et secrets masqués.');return
        if args.diagnose:
            key=config.get('helius_api_key')
            if isinstance(key,str) and key.startswith(('https://','wss://')):
                print('ATTENTION : helius_api_key contient une URL ; ce champ attend uniquement la clé API.',flush=True)
            asyncio.run(diagnose(http,ws,next(iter(wallets.values()))));return
        args.state.parent.mkdir(parents=True,exist_ok=True)
        with StateLock(args.state.with_suffix('.lock')):
            journal=Journal(args.state)
            try:asyncio.run(Service(journal,wallets,MeasuredRPC(http,None,rps=2),ws,sender=sender,early=args.early).run())
            finally:journal.db.close()
    except KeyboardInterrupt:print('Surveillance arrêtée.',flush=True)
    except Exception as exc:
        # Parser/provider errors can include URLs and keys; output only a fixed diagnostic.
        raise SystemExit('Démarrage impossible ('+type(exc).__name__+') : vérifier configuration, dépendances et état du service.') from None


if __name__=='__main__':main()
