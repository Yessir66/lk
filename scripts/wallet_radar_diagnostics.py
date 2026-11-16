"""Bounded read-only investigation of failed paper signals. Never print RPC bodies or secrets."""
import re
import json
import sqlite3
import time

from discord_token_analysis import valid_address
from wallet_radar_paper import latest_session, native_trades, BBIG, PUMP, SOL, USDC, PUMP_TOKEN, TTWO, DOGE, ZERO

SWAP='pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'


def error_label(exc):
    result=type(exc).__name__
    for attr,label in [('status','HTTP'),('rpc_code','RPC')]:
        value=getattr(exc,attr,None)
        if isinstance(value,int) and not isinstance(value,bool):result+=' '+label+' '+str(value)
    return result


def find_signature(record,mint,state_path):
    signature=record.get('initial_signature')
    if isinstance(signature,str) and re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,100}',signature):return signature
    if not state_path.is_file():return None
    db=sqlite3.connect(state_path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        rows=db.execute("SELECT signature,trades FROM events WHERE wallet=? AND state='done' AND trades LIKE ? "
            "ORDER BY ABS(first_seen-?) LIMIT 20",(BBIG,'%'+mint+'%',record.get('at',0))).fetchall()
        for signature,trades in rows:
            try:values=json.loads(trades)
            except (ValueError,TypeError):continue
            if any(trade.get('mint')==mint and trade.get('kind')=='buy' for trade in values):return signature
        return None
    finally:db.close()


def diagnose(path,state_path,rpc):
    latest,limit=latest_session(path)
    failed=[(mint,row) for mint,row in latest.items() if row.get('code') in ('DONNEES_INSUFFISANTES','NON_SUPPORTE') and valid_address(mint)]
    failed.sort(key=lambda item:0 if 'comptes de position' in item[1].get('message','') else 1)
    print(f'DIAGNOSTIC WALLET RADAR : {len(latest)}/{limit} tokens ; maximum 4 cas examines, lecture seule.',flush=True)
    if not failed:print('Aucun cas incomplet a examiner.');return
    for mint,record in failed[:4]:
        print('\nTOKEN '+mint,flush=True)
        print('CAS '+record['code']+' ; trou abonnement : '+str(record.get('subscription_gap_ms','?'))+' ms',flush=True)
        try:
            snapshot=rpc.call('getTokenAccountsByOwner',[BBIG,{'mint':mint},{'encoding':'jsonParsed','commitment':'confirmed'}])
            accounts=snapshot.get('value') if isinstance(snapshot,dict) else None
            if not isinstance(accounts,list):print('POSITION : format de reponse inattendu.',flush=True)
            else:
                formats=[]
                for account in accounts:
                    data=account.get('account',{}).get('data') if isinstance(account,dict) else None
                    if isinstance(data,dict):formats.append('jsonParsed' if isinstance(data.get('parsed'),dict) else 'objet non parse')
                    elif isinstance(data,list):formats.append('binaire '+(data[1] if len(data)>1 and data[1] in ('base64','base58') else 'non reconnu'))
                    else:formats.append(type(data).__name__)
                print('POSITION : lecture OK ; '+str(len(accounts))+' comptes ; formats '+', '.join(sorted(set(formats))),flush=True)
        except Exception as exc:print('POSITION : '+error_label(exc),flush=True)
        try:
            signature=find_signature(record,mint,state_path)
            if not signature:
                print('TRANSACTION : signature introuvable dans le journal.',flush=True);continue
            tx=rpc.call('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':1}])
            if not isinstance(tx,dict):print('TRANSACTION : indisponible.',flush=True);continue
            meta=tx.get('meta') or {};logs=meta.get('logMessages') or []
            programs={match[1] for line in logs if isinstance(line,str) and (match:=re.match(r'^Program (\w+) invoke',line))}
            labels=[]
            if PUMP in programs:labels.append('Pump.fun')
            if SWAP in programs:labels.append('PumpSwap')
            print('PROGRAMMES : '+(', '.join(labels) if labels else 'autre plateforme'),flush=True)
            events=native_trades({'signature':signature,'err':meta.get('err'),'logs':logs},tx.get('slot',0),time.time(),time.monotonic())
            events=[event for event in events if event['mint']==mint]
            print('EVENEMENTS PUMP : '+str(len(events))+' ; utilisateurs BBIG BOSS : '+str(sum(e['user']==BBIG for e in events)),flush=True)
            for event in events[:2]:
                quote=event.get('quote_mint',ZERO)
                currency={USDC:'USDC',PUMP_TOKEN:'PUMP',TTWO:'TTWO',DOGE:'DOGE'}.get(quote,'SOL' if quote in (ZERO,SOL) else 'autre '+quote)
                print('COTATION : '+currency+' ; octets inconnus : '+str(event.get('unparsed_tail_bytes',0)),flush=True)
            if any(isinstance(line,str) and 'truncated' in line.lower() for line in logs):print('LOGS : troncature signalee.',flush=True)
        except Exception as exc:print('TRANSACTION : '+error_label(exc),flush=True)
    print('\nFin du diagnostic. Aucun ordre, aucun journal modifie, aucune cle affichee.',flush=True)
