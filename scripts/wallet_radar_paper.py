"""Experimental Pump.fun volume observer. Simulation only: no signing or sending API."""
import asyncio
import base64
from dataclasses import dataclass, field
from decimal import Decimal
import json
import re
import ssl
import struct
import time
import urllib.request
import uuid
from collections import Counter

BBIG='BLw4fXAc9P7NYhnRH9GyccXKUmvpUSiqc67BGbanxcDp'
BWAM='bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa'
PUMP='6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
SOL='So11111111111111111111111111111111111111112'
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
ZERO='11111111111111111111111111111111'
DISCRIMINATOR=bytes([189,219,127,211,78,230,97,238])
ALPHABET='123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
# Official ABI: pump-public-docs 639b87f + @pump-fun/pump-sdk 4.3.1, src/idl/pump.json.
# The published SDK includes quote_transfer_fee, absent from the public-docs snapshot.
FIELDS=[('mint','pubkey'),('sol_amount','u64'),('token_amount','u64'),('is_buy','bool'),
 ('user','pubkey'),('timestamp','i64'),('virtual_sol_reserves','u64'),('virtual_token_reserves','u64'),
 ('real_sol_reserves','u64'),('real_token_reserves','u64'),('fee_recipient','pubkey'),('fee_basis_points','u64'),
 ('fee','u64'),('creator','pubkey'),('creator_fee_basis_points','u64'),('creator_fee','u64'),('track_volume','bool'),
 ('total_unclaimed_tokens','u64'),('total_claimed_tokens','u64'),('current_sol_volume','u64'),
 ('last_update_timestamp','i64'),('ix_name','string'),('mayhem_mode','bool'),('cashback_fee_basis_points','u64'),
 ('cashback','u64'),('buyback_fee_basis_points','u64'),('buyback_fee','u64'),('shareholders','shareholders'),
 ('quote_mint','pubkey'),('quote_amount','u64'),('virtual_quote_reserves','u64'),('real_quote_reserves','u64'),
 ('holder_rewards_bps','u64'),('holder_rewards','u64'),('creator_fee_unclaimed','u64'),('quote_transfer_fee','u64')]
LEGACY_ENDS={'track_volume','mayhem_mode','cashback_fee_basis_points','shareholders','quote_mint',
             'holder_rewards_bps','creator_fee_unclaimed','quote_transfer_fee'}


def public_key(raw):
    n=int.from_bytes(raw,'big');result=''
    while n:n,remainder=divmod(n,58);result=ALPHABET[remainder]+result
    return '1'*(len(raw)-len(raw.lstrip(b'\0')))+result


def decode_event(raw):
    if not raw.startswith(DISCRIMINATOR):return None
    offset=8;values={}
    def take(size):
        nonlocal offset
        if size<0 or offset+size>len(raw):raise ValueError('Truncated event')
        value=raw[offset:offset+size];offset+=size;return value
    for name,kind in FIELDS:
        if offset==len(raw) and name in LEGACY_ENDS:break
        if kind=='pubkey':value=public_key(take(32))
        elif kind=='bool':
            value=take(1)[0]
            if value not in (0,1):raise ValueError('Invalid boolean')
            value=bool(value)
        elif kind=='string':
            count=int.from_bytes(take(4),'little')
            if count>128:raise ValueError('String too long')
            value=take(count).decode('utf-8')
        elif kind=='shareholders':
            count=int.from_bytes(take(4),'little')
            if count>64:raise ValueError('Too many shareholders')
            take(count*34);value=count
        else:value=int.from_bytes(take(8),'little',signed=kind=='i64')
        values[name]=value
    if 'creator_fee' not in values:raise ValueError('Unknown event layout')
    values['unparsed_tail_bytes']=len(raw)-offset
    return values


def native_trades(value,slot,wall,mono):
    """Attribute Program data to the actual executing program, not arbitrary log text."""
    if value.get('err') is not None:return []
    signature=value.get('signature','')
    if not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,100}',signature):return []
    stack=[];events=[]
    for line in value.get('logs',[]):
        if not isinstance(line,str):continue
        call=re.fullmatch(r'Program (\w+) invoke \[\d+\]',line)
        if call:stack.append(call[1]);continue
        end=re.match(r'Program (\w+) (?:success|failed:)',line)
        if end:
            if stack and stack[-1]==end[1]:stack.pop()
            else:stack.clear()
            continue
        if stack and stack[-1]==PUMP and line.startswith('Program data: '):
            try:event=decode_event(base64.b64decode(line[14:],validate=True))
            except (ValueError,UnicodeError,struct.error):continue
            if event:
                event.update(signature=signature,slot=slot,wall=wall,mono=mono,index=len(events))
                events.append(event)
    return events


class Prices:
    def __init__(self):self.usd=None;self.published=0;self.received=0

    def update(self,data,now):
        row=data.get('solana',{});price=Decimal(str(row.get('usd','0')));published=row.get('last_updated_at')
        if not price.is_finite() or price<=0 or not isinstance(published,(int,float)) or not 0<=now-published<=90:
            raise ValueError('Stale SOL/USD price')
        self.usd=price;self.published=published;self.received=now

    def dollars(self,event,now):
        mint=event.get('quote_mint',ZERO)
        if mint==USDC:return Decimal(event.get('quote_amount',0))/10**6
        if mint not in (ZERO,SOL):return None
        amount=event.get('quote_amount') if mint==SOL else event.get('sol_amount')
        if not amount or self.usd is None or not 0<=now-self.published<=90:return None
        return Decimal(amount)/10**9*self.usd

    async def run(self):
        url='https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd&include_last_updated_at=true'
        def fetch():
            with urllib.request.urlopen(url,timeout=5) as response:return json.load(response)
        while True:
            try:self.update(await asyncio.to_thread(fetch),time.time())
            except Exception:pass  # No credentials, provider bodies or URLs in console errors.
            await asyncio.sleep(15)


@dataclass
class Candidate:
    mint:str
    signature:str
    creator:str
    t0:float
    wall:float
    slot:int
    baseline:int|None=None
    volume:Decimal=Decimal(0)
    buys:int=0
    movements:list=field(default_factory=list)
    seen:set=field(default_factory=set)
    gate:bool=False
    unknown_volume:bool=False
    unknown_position:bool=False
    terminal:str|None=None
    subscribed_at:float|None=None
    reasons:set=field(default_factory=set)


class Decisions:
    def __init__(self,prices,emit,limit=30):
        self.prices=prices;self.emit=emit;self.candidates={};self.sampled=set();self.limit=limit

    def notify(self,c,code,message,now,terminal=False):
        if c.terminal:return
        if terminal:c.terminal=code
        if code=='DONNEES_INSUFFISANTES' and c.reasons:message+=' Causes : '+', '.join(sorted(c.reasons))+'.'
        self.emit(dict(v=1,type='paper_decision',mode='SIMULATION',code=code,mint=c.mint,
            message=message,initial_signature=c.signature,usd_total=str(c.volume),buys=c.buys,
            amount_usd='25',elapsed_s=round(max(0,now-c.t0),3),
            subscription_gap_ms=round((c.subscribed_at-c.t0)*1000) if c.subscribed_at is not None else None,
            ordering='processed_receipt',coverage='observed_lower_bound',missing_reasons=sorted(c.reasons),at=time.time()))

    def observe(self,event):
        now=event['mono'];mint=event['mint'];c=self.candidates.get(mint)
        if c is None:
            if event['user']!=BBIG or not event['is_buy']:return
            if mint in self.sampled or len(self.sampled)>=self.limit:return
            self.sampled.add(mint)
            c=Candidate(mint,event['signature'],event['creator'],now,event['wall'],event['slot'])
            if len(self.candidates)>=2048:
                for old in list(self.candidates):
                    if self.candidates[old].terminal:del self.candidates[old];break
            self.candidates[mint]=c
            self.notify(c,'SUIVI','Achat BBIG BOSS observe ; analyse 1 s / 3 s en simulation.',now)
            if c.creator==BWAM:self.notify(c,'DEV_EXCLU','Createur declare bwam : token exclu.',now,True)
            elif event.get('unparsed_tail_bytes'):
                c.reasons.add('FORMAT_EVENEMENT_INCONNU')
                self.notify(c,'DONNEES_INSUFFISANTES','Extension du format Pump.fun non reconnue ; aucune decision.',now,True)
            elif len([x for x in self.candidates.values() if not x.terminal])>8:
                c.reasons.add('CAPACITE_SIMULTANEE_DEPASSEE')
                self.notify(c,'DONNEES_INSUFFISANTES','Trop de signaux simultanes pour cet essai.',now,True)
            return
        if c.terminal or event['signature']==c.signature:return
        identity=(event['signature'],event['index'])
        if identity in c.seen:return
        c.seen.add(identity)
        if event['slot']<c.slot or not 0<=now-c.t0<=3:return
        if event.get('unparsed_tail_bytes'):
            if event['is_buy']:
                c.unknown_volume=True;c.reasons.add('FORMAT_EVENEMENT_INCONNU')
            if event['user']==BBIG and now-c.t0<=1:
                c.unknown_position=True;c.reasons.add('FORMAT_EVENEMENT_INCONNU')
            return
        if event['creator']!=c.creator:
            c.reasons.add('CREATEUR_MODIFIE')
            self.notify(c,'DONNEES_INSUFFISANTES','Le createur declare a change pendant la fenetre.',now,True);return
        if event['user']==BBIG:
            c.movements.append((now,event['token_amount'] if event['is_buy'] else -event['token_amount']))
        if event['is_buy']:
            dollars=self.prices.dollars(event,event['wall'])
            if dollars is None or dollars<=0:
                c.unknown_volume=True
                quote=event.get('quote_mint',ZERO)
                if quote not in (ZERO,SOL,USDC):c.reasons.add('DEVISE_COTATION_NON_SUPPORTEE')
                elif quote in (ZERO,SOL) and (self.prices.usd is None or not 0<=event['wall']-self.prices.published<=90):c.reasons.add('COURS_SOL_USD_ABSENT_OU_PERIME')
                else:c.reasons.add('MONTANT_COTATION_INVALIDE')
            else:c.volume+=dollars;c.buys+=1
        self.tick(now)

    def confirmed(self,trade,tx,mono,account_snapshot=None):
        if (trade.get('wallet')!=BBIG or trade.get('kind')!='buy' or trade.get('source')!='websocket'
            or trade.get('swap_proof_version')!=1):return
        c=self.candidates.get(trade['mint'])
        if not c or c.terminal or trade['signature']!=c.signature:return
        meta=tx.get('meta') or {}
        if meta.get('err') is not None:return
        owned={b['accountIndex'] for side in ('preTokenBalances','postTokenBalances') for b in meta.get(side,[])
               if b.get('mint')==c.mint and b.get('owner')==BBIG}
        balances=[b for b in meta.get('postTokenBalances',[]) if b.get('mint')==c.mint and b.get('accountIndex') in owned]
        if not owned:c.unknown_position=True;c.reasons.add('POSITION_INITIALE_ABSENTE');return
        # Transaction balances alone omit other token accounts owned by the wallet.
        keys=tx.get('transaction',{}).get('message',{}).get('accountKeys',[])
        addresses={keys[i].get('pubkey') if isinstance(keys[i],dict) else keys[i] for i in owned if i<len(keys)}
        context=(account_snapshot or {}).get('context',{}).get('slot')
        accounts=(account_snapshot or {}).get('value')
        if not isinstance(context,int) or context<c.slot or not isinstance(accounts,list):
            c.unknown_position=True;c.reasons.add('SNAPSHOT_POSITION_INVALIDE');self.tick(mono);return
        for account in accounts:
            info=account.get('account',{}).get('data',{}).get('parsed',{}).get('info',{})
            if info.get('owner')!=BBIG or info.get('mint')!=c.mint:
                c.unknown_position=True;c.reasons.add('COMPTE_POSITION_NON_PARSE');self.tick(mono);return
            if int(info.get('tokenAmount',{}).get('amount','-1'))<0 or (int(info['tokenAmount']['amount'])>0 and account.get('pubkey') not in addresses):
                c.unknown_position=True;c.reasons.add('AUTRE_POSITION_TOKEN_NON_RECONSTITUEE');self.tick(mono);return
        if mono-c.t0>3:c.unknown_position=True;c.reasons.add('POSITION_VALIDEE_TROP_TARD')
        c.baseline=sum(int(b['uiTokenAmount']['amount']) for b in balances)
        self.tick(mono)

    def tick(self,now):
        for c in self.candidates.values():
            if c.terminal:continue
            elapsed=now-c.t0
            balance=c.baseline
            if balance is not None:
                if balance==0:
                    self.notify(c,'SORTIE_TOTALE_1S','Position nulle dans la transaction initiale ; abandon.',now,True);continue
                for received,delta in sorted(c.movements):
                    if received-c.t0>1:continue
                    balance+=delta
                    if balance==0:
                        self.notify(c,'SORTIE_TOTALE_1S','BBIG BOSS a liquide sa position pendant la premiere seconde.',now,True);break
                    if balance<0:c.unknown_position=True;c.reasons.add('MOUVEMENTS_POSITION_INCOHERENTS')
                if c.terminal:continue
            if elapsed>3:
                if balance is None:c.reasons.add('POSITION_INITIALE_NON_VALIDEE')
                code='DONNEES_INSUFFISANTES' if balance is None or c.unknown_position or c.unknown_volume else 'VOLUME_INSUFFISANT'
                self.notify(c,code,'Fin des 3 s : '+('donnees incompletes.' if code=='DONNEES_INSUFFISANTES' else '400 USD non observes.'),now,True);continue
            if elapsed>=1 and balance is not None and not c.unknown_position:
                if not c.gate:
                    c.gate=True
                    self.notify(c,'ATTENTE_VOLUME','Premiere seconde validee ; attente de 400 USD observes.',now)
                if c.volume>=400:
                    self.notify(c,'ACHAT_SIMULE','Conditions observees : achat THEORIQUE de 25 USD. Aucun ordre envoye.',now,True)

    def interrupted(self,now):
        for c in self.candidates.values():
            if not c.terminal:
                c.reasons.add('FLUX_INTERROMPU')
                self.notify(c,'DONNEES_INSUFFISANTES','Flux interrompu pendant la decision.',now,True)

    def unparsed(self,target,value,now):
        if value.get('err') is not None:return
        logs=[line for line in value.get('logs',[]) if isinstance(line,str)]
        truncated=any(re.search(r'logs? truncated',line,re.I) for line in logs)
        could_buy=any(re.search(r'Instruction: (?:Buy\w*|\w*Swap\w*|\w*Route\w*)',line,re.I) for line in logs)
        could_move=any(re.search(r'Instruction: (?:Buy\w*|Sell\w*|\w*Swap\w*|\w*Route\w*|Transfer\w*|Burn\w*|MintTo\w*)',line,re.I) for line in logs)
        if target[0]=='wallet' and (could_move or truncated):
            for c in self.candidates.values():
                if not c.terminal and 0<=now-c.t0<=1:
                    c.unknown_position=True
                    c.reasons.add('LOGS_TRONQUES' if truncated else 'MOUVEMENT_POSITION_NON_DECODE')
        elif target[0]=='token' and (could_buy or truncated):
            c=self.candidates.get(target[1])
            if c and not c.terminal and 0<=now-c.t0<=3:
                c.unknown_volume=True
                c.reasons.add('LOGS_TRONQUES' if truncated else 'ACHAT_OU_SWAP_NON_DECODE')


class PaperObserver:
    def __init__(self,ws_url,journal_path):
        self.ws_url=ws_url;self.path=journal_path;self.prices=Prices()
        self.decisions=Decisions(self.prices,self.emit);self.connected=False;self.ready_wall=float('inf')
        self.unsupported=set();self.session=uuid.uuid4().hex;self.results={}

    def emit(self,event):
        event=dict(event,session_id=self.session)
        if event.get('code') not in ('SUIVI','ATTENTE_VOLUME'):self.results[event['mint']]=event['code']
        print('RADAR_DECISION '+json.dumps(event,separators=(',',':')),flush=True)
        self.write_record(event)

    def write_record(self,event):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('a',encoding='utf-8') as handle:handle.write(json.dumps(event)+'\n')
        except OSError:print('SIMULATION : journal local indisponible.',flush=True)

    def confirmed(self,trade,tx,mono,processed_mono=None,processed_wall=None,account_snapshot=None):
        c=self.decisions.candidates.get(trade.get('mint'))
        if (c and not c.terminal and trade.get('signature')==c.signature
            and processed_mono is not None and processed_mono<c.t0):
            c.t0=processed_mono
            if processed_wall is not None:c.wall=processed_wall
        self.decisions.confirmed(trade,tx,mono,account_snapshot)
        if (trade.get('wallet')==BBIG and trade.get('kind')=='buy' and trade.get('source')=='websocket'
            and trade['mint'] not in self.decisions.candidates and trade['mint'] not in self.unsupported
            and len(self.decisions.sampled)<self.decisions.limit):
            self.unsupported.add(trade['mint'])
            self.decisions.sampled.add(trade['mint'])
            covered=self.connected and (trade.get('processed_seen_at') or 0)>=self.ready_wall
            self.emit(dict(v=1,type='paper_decision',mode='SIMULATION',code='NON_SUPPORTE' if covered else 'DONNEES_INSUFFISANTES',mint=trade['mint'],
                           message='Pas de TradeEvent Pump.fun exploitable ou flux non pret au signal ; aucune decision.',at=time.time()))

    async def run(self):
        from websockets.asyncio.client import connect
        self.write_record(dict(type='paper_session',session_id=self.session,limit=self.decisions.limit,at=time.time()))
        price_task=asyncio.create_task(self.prices.run())
        try:
            while True:
                try:
                    async with connect(self.ws_url,ssl=ssl.create_default_context(),open_timeout=20,
                                       ping_interval=20,ping_timeout=20,max_size=2**22) as ws:
                        requests={1:('wallet',BBIG)};pending_at={1:time.monotonic()}
                        subscriptions={};mint_subs={};requested=set();identity=1
                        await ws.send(json.dumps({'jsonrpc':'2.0','id':1,'method':'logsSubscribe',
                            'params':[{'mentions':[BBIG]},{'commitment':'processed'}]}))
                        self.connected=False
                        print('SIMULATION : connexion gratuite, logs Pump.fun ; aucun achat reel.',flush=True)
                        while True:
                            try:raw=await asyncio.wait_for(ws.recv(),.02)
                            except asyncio.TimeoutError:
                                raw=None
                            wall=time.time();mono=time.monotonic()
                            if pending_at and mono-min(pending_at.values())>20:raise TimeoutError('Subscription acknowledgement missing')
                            if raw is not None:
                                message=json.loads(raw)
                                if 'id' in message:
                                    target=requests.pop(message['id'],None)
                                    pending_at.pop(message['id'],None)
                                    if target and target[0]!='unsubscribe':
                                        if not isinstance(message.get('result'),int):raise ValueError('Subscription refused')
                                        subscriptions[message['result']]=target
                                        if target[0]=='wallet':self.connected=True;self.ready_wall=wall
                                        if target[0]=='token':
                                            mint_subs[target[1]]=message['result']
                                            self.decisions.candidates[target[1]].subscribed_at=mono
                                elif message.get('method')=='logsNotification':
                                    params=message.get('params') or {};target=subscriptions.get(params.get('subscription'))
                                    result=params.get('result') or {};value=result.get('value') or {}
                                    if target:
                                        events=native_trades(value,(result.get('context') or {}).get('slot',0),wall,mono)
                                        for event in events:self.decisions.observe(event)
                                        if not events:self.decisions.unparsed(target,value,mono)
                            self.decisions.tick(mono)
                            if len(self.results)>=self.decisions.limit:
                                summary=dict(type='paper_summary',session_id=self.session,tokens=len(self.results),
                                             outcomes=dict(Counter(self.results.values())),at=wall)
                                self.write_record(summary)
                                print(f'SIMULATION TERMINEE : bilan de {self.decisions.limit} tokens enregistre ; surveillance GMGN maintenue.',flush=True)
                                return
                            for mint,c in self.decisions.candidates.items():
                                if not c.terminal and mint not in requested:
                                    identity+=1;requests[identity]=('token',mint);pending_at[identity]=mono;requested.add(mint)
                                    await ws.send(json.dumps({'jsonrpc':'2.0','id':identity,'method':'logsSubscribe',
                                        'params':[{'mentions':[mint]},{'commitment':'processed'}]}))
                                elif c.terminal and mint in mint_subs:
                                    sub=mint_subs.pop(mint);subscriptions.pop(sub,None)
                                    identity+=1;requests[identity]=('unsubscribe',mint);pending_at[identity]=mono
                                    await ws.send(json.dumps({'jsonrpc':'2.0','id':identity,'method':'logsUnsubscribe','params':[sub]}))
                except asyncio.CancelledError:raise
                except Exception:
                    self.connected=False;self.decisions.interrupted(time.monotonic())
                    print('SIMULATION : flux indisponible, reprise dans 3 s ; decisions actives abandonnees.',flush=True)
                    await asyncio.sleep(3)
        finally:
            self.decisions.interrupted(time.monotonic())
            price_task.cancel();await asyncio.gather(price_task,return_exceptions=True)


def latest_session(path):
    events=[];session=None;limit=30
    if not path.is_file():return {},limit
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            try:row=json.loads(line)
            except (ValueError,TypeError):continue
            if row.get('type')=='paper_session':session=row.get('session_id');limit=row.get('limit',30);events=[]
            elif row.get('type')=='paper_decision' and row.get('session_id')==session:events.append(row)
    return {row['mint']:row for row in events},limit


def report(path):
    if not path.is_file():
        print('Aucun essai enregistre. Demarrer le menu 3 de Wallet Radar.');return
    latest,limit=latest_session(path)
    outcomes=Counter(row['code'] for row in latest.values())
    print(f'WALLET RADAR - SIMULATION : {len(latest)}/{limit} tokens distincts, dernier essai.')
    for code,count in sorted(outcomes.items()):print(f'{code} : {count}')
    for mint,row in latest.items():
        volume=str(row.get('usd_total','non mesure'))
        if row['code'] in ('DONNEES_INSUFFISANTES','NON_SUPPORTE','DEV_EXCLU') and not row.get('buys'):volume='non mesure'
        print(mint[:8]+'... '+row['code']+' ; volume USD observe '+volume+
              ' ; decision a '+str(row.get('elapsed_s','?'))+' s ; '+str(row.get('message','')))
    print('Aucun achat reel. Volumes observes a reception processed, pas historique exhaustif ni prix executable.')
    print('SOL/USD indicatif CoinGecko horodate (maximum 90 s) ; USDC valorise nominalement a 1 USD.')
