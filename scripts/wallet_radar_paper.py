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
from wallet_radar_pumpswap import PROGRAM as PUMPSWAP, decode_trade as decode_swap, decode_pool, normalize as normalize_swap

BBIG='BLw4fXAc9P7NYhnRH9GyccXKUmvpUSiqc67BGbanxcDp'
BWAM='bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa'
PUMP='6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
SOL='So11111111111111111111111111111111111111112'
USDC='EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
PUMP_TOKEN='pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn'
TTWO='TTWofwAge91oFhZs7kpQdyrVRkmevgM88xijGvQFbKo'
DOGE='DoGEV7LASBkQbibMc5k5vKnTZoMg423GpJ5QtJEGfm7R'
TTWO_PRICE_ID='take-two-interactive-software-backpack-securities'
DOGE_POOL='4n9rkpKkPUGFquBh6UXqNYbaCNyBcFq9VwNbutwGX5d6'
MSFTX='XspzcW1PRtgf6Wj92HCiZdjzKCyFekVD8P5Ueh3dRMX'
COST='CZEB3WNZuF2Yz1z2H81RcCk8T7fsw82KB33zqamASVsg'
GTAVI='ERiM8NiCanarGEnWMnURAoxTuTvUXZbtJsPTirnHpump'
GTAVI_POOL='9YbrZx4ytWsuhsbfM75r4RfPW25MGaos4CVcuXM6qBNY'
# Exact Solana contracts and decimals cross-checked with Jupiter/GeckoTerminal;
# stock-token CoinGecko contract metadata also confirms these mappings.
EXTRA_QUOTES={MSFTX:('MSFTX',8,'microsoft-xstock'),COST:('COST',6,'costco-wholesale-backpack-securities'),
              GTAVI:('GTAVI',6,None)}
ZERO='11111111111111111111111111111111'
PRICE_REASONS={'DEVISE_COTATION_NON_SUPPORTEE','COURS_SOL_USD_ABSENT_OU_PERIME',
               'COURS_PUMP_USD_ABSENT_OU_PERIME','COURS_TTWO_USD_ABSENT_OU_PERIME',
               'COURS_DOGE_USD_ABSENT_OU_PERIME','MONTANT_COTATION_INVALIDE'}
PRICE_REASONS.update('COURS_'+symbol+'_USD_ABSENT_OU_PERIME' for symbol,_,_ in EXTRA_QUOTES.values())
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
        if stack and stack[-1] in (PUMP,PUMPSWAP) and line.startswith('Program data: '):
            try:
                raw=base64.b64decode(line[14:],validate=True)
                event=decode_event(raw) if stack[-1]==PUMP else decode_swap(raw)
            except (ValueError,UnicodeError,struct.error):
                if stack[-1]!=PUMPSWAP:continue
                event=dict(platform='PumpSwap',decode_error=True)
            if event:
                event.update(signature=signature,slot=slot,wall=wall,mono=mono,index=len(events))
                events.append(event)
    return events


class Prices:
    def __init__(self):
        self.usd=None;self.published=0;self.received=0
        self.pump_usd=None;self.pump_published=0
        self.ttwo_usd=None;self.ttwo_published=0
        self.doge_usd=None;self.doge_published=0
        self.extra_prices={}

    def update_extra(self,mint,data,now):
        _,_,coin_id=EXTRA_QUOTES[mint]
        if coin_id is None:raise ValueError('This asset requires an address-specific candle')
        row=data.get(coin_id,{})
        self.extra_prices[mint]=self.checked_price(row.get('usd'),row.get('last_updated_at'),now)

    def update_gtavi(self,data,now):
        if data.get('meta',{}).get('base',{}).get('address')!=GTAVI:raise ValueError('Unexpected GTAVI pool asset')
        candles=data.get('data',{}).get('attributes',{}).get('ohlcv_list',[])
        if not candles or not isinstance(candles[0],list) or len(candles[0])<6:raise ValueError('Missing GTAVI USD candle')
        self.extra_prices[GTAVI]=self.checked_price(candles[0][4],candles[0][0],now)

    def update_ttwo(self,data,now):
        row=data.get(TTWO_PRICE_ID,{})
        price,published=self.checked_price(row.get('usd'),row.get('last_updated_at'),now)
        self.ttwo_usd=price;self.ttwo_published=published

    @staticmethod
    def checked_price(value,published,now):
        price=Decimal(str(value))
        if (not price.is_finite() or price<=0 or isinstance(published,bool)
            or not isinstance(published,(int,float)) or not 0<=now-published<=90):
            raise ValueError('Invalid or stale USD price')
        return price,published

    def update_doge(self,data,now):
        # Explicit token address in the request and response: never substitute native DOGE.
        if data.get('meta',{}).get('base',{}).get('address')!=DOGE:
            raise ValueError('Unexpected DOGE pool asset')
        candles=data.get('data',{}).get('attributes',{}).get('ohlcv_list',[])
        if not candles or not isinstance(candles[0],list) or len(candles[0])<6:
            raise ValueError('Missing DOGE USD candle')
        candle=candles[0]
        # Age uses the minute START, conservatively, not the HTTP response date.
        price,published=self.checked_price(candle[4],candle[0],now)
        self.doge_usd=price;self.doge_published=published

    def update(self,data,now):
        row=data.get('solana',{});price=Decimal(str(row.get('usd','0')));published=row.get('last_updated_at')
        if not price.is_finite() or price<=0 or not isinstance(published,(int,float)) or not 0<=now-published<=90:
            raise ValueError('Stale SOL/USD price')
        self.usd=price;self.published=published;self.received=now

    def update_pump(self,data,now):
        row=data.get('pump-fun',{});price=Decimal(str(row.get('usd','0')));published=row.get('last_updated_at')
        if not price.is_finite() or price<=0 or not isinstance(published,(int,float)) or not 0<=now-published<=90:
            raise ValueError('Stale PUMP/USD price')
        self.pump_usd=price;self.pump_published=published

    def dollars(self,event,now):
        mint=event.get('quote_mint',ZERO)
        if mint in EXTRA_QUOTES:
            price,published=self.extra_prices.get(mint,(None,0))
            if price is None or not 0<=now-published<=90:return None
            return Decimal(event.get('quote_amount',0))/10**EXTRA_QUOTES[mint][1]*price
        if mint==USDC:return Decimal(event.get('quote_amount',0))/10**6
        if mint in (TTWO,DOGE):
            price,published,decimals=(self.ttwo_usd,self.ttwo_published,6) if mint==TTWO else (self.doge_usd,self.doge_published,8)
            if price is None or not 0<=now-published<=90:return None
            return Decimal(event.get('quote_amount',0))/10**decimals*price
        if mint==PUMP_TOKEN:
            if self.pump_usd is None or not 0<=now-self.pump_published<=90:return None
            # Official CoinGecko contract metadata: Pump.fun PUMP token has 6 decimals.
            return Decimal(event.get('quote_amount',0))/10**6*self.pump_usd
        if mint not in (ZERO,SOL):return None
        amount=event.get('quote_amount') if mint==SOL else event.get('sol_amount')
        if not amount or self.usd is None or not 0<=now-self.published<=90:return None
        return Decimal(amount)/10**9*self.usd

    async def run(self):
        ids=','.join(['solana','pump-fun',TTWO_PRICE_ID]+[coin_id for _,_,coin_id in EXTRA_QUOTES.values() if coin_id])
        url='https://api.coingecko.com/api/v3/simple/price?ids='+ids+'&vs_currencies=usd&include_last_updated_at=true'
        def fetch():
            with urllib.request.urlopen(url,timeout=5) as response:return json.load(response)
        while True:
            try:
                data=await asyncio.to_thread(fetch);now=time.time()
                for update in (self.update,self.update_pump,self.update_ttwo):
                    try:update(data,now)
                    except (ValueError,TypeError,ArithmeticError):pass
                for mint,(_,_,coin_id) in EXTRA_QUOTES.items():
                    if coin_id:
                        try:self.update_extra(mint,data,now)
                        except (ValueError,TypeError,ArithmeticError):pass
            except Exception:pass  # No credentials, provider bodies or URLs in console errors.
            await asyncio.sleep(15)

    async def run_doge(self):
        def fetch(url):
            with urllib.request.urlopen(url,timeout=5) as response:return json.load(response)
        while True:
            for mint,pool,update in ((DOGE,DOGE_POOL,self.update_doge),(GTAVI,GTAVI_POOL,self.update_gtavi)):
                url=('https://api.geckoterminal.com/api/v2/networks/solana/pools/'+pool+
                     '/ohlcv/minute?aggregate=1&limit=1&currency=usd&token='+mint)
                try:update(await asyncio.to_thread(fetch,url),time.time())
                except Exception:pass
            await asyncio.sleep(30)


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
    pending_pools:set=field(default_factory=set)
    subscribed_at:float|None=None
    reasons:set=field(default_factory=set)
    pending_prices:list=field(default_factory=list)


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

    def observe(self,event,evaluated_at=None):
        now=event['mono'];mint=event['mint'];c=self.candidates.get(mint)
        evaluated=now if evaluated_at is None else max(now,evaluated_at)
        if c is not None and evaluated-c.t0>3:self.tick(evaluated);return
        if c is None:
            if event['user']!=BBIG or not event['is_buy']:return
            if mint in self.sampled or len(self.sampled)>=self.limit:return
            self.sampled.add(mint)
            c=Candidate(mint,event['signature'],event['creator'],now,event['wall'],event['slot'])
            if len(self.candidates)>=2048:
                for old in list(self.candidates):
                    if self.candidates[old].terminal:del self.candidates[old];break
            self.candidates[mint]=c
            self.notify(c,'SUIVI','Achat BBIG BOSS observe ; analyse 1 s / 3 s en simulation.',evaluated)
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
            c.pending_prices.append(event)
        self.tick(evaluated)

    def value_pending(self,c,now):
        wall=c.wall+max(0,now-c.t0);remaining=[]
        c.reasons.difference_update(PRICE_REASONS)
        for event in c.pending_prices:
            dollars=self.prices.dollars(event,wall)
            if dollars is None or dollars<=0:
                remaining.append(event);quote=event.get('quote_mint',ZERO)
                if quote in EXTRA_QUOTES:
                    symbol=EXTRA_QUOTES[quote][0];price,published=self.prices.extra_prices.get(quote,(None,0))
                    c.reasons.add('COURS_'+symbol+'_USD_ABSENT_OU_PERIME' if price is None or not 0<=wall-published<=90 else 'MONTANT_COTATION_INVALIDE')
                elif quote not in (ZERO,SOL,USDC,PUMP_TOKEN,TTWO,DOGE):c.reasons.add('DEVISE_COTATION_NON_SUPPORTEE')
                elif quote==TTWO and (self.prices.ttwo_usd is None or not 0<=wall-self.prices.ttwo_published<=90):c.reasons.add('COURS_TTWO_USD_ABSENT_OU_PERIME')
                elif quote==DOGE and (self.prices.doge_usd is None or not 0<=wall-self.prices.doge_published<=90):c.reasons.add('COURS_DOGE_USD_ABSENT_OU_PERIME')
                elif quote==PUMP_TOKEN and (self.prices.pump_usd is None or not 0<=wall-self.prices.pump_published<=90):c.reasons.add('COURS_PUMP_USD_ABSENT_OU_PERIME')
                elif quote in (ZERO,SOL) and (self.prices.usd is None or not 0<=wall-self.prices.published<=90):c.reasons.add('COURS_SOL_USD_ABSENT_OU_PERIME')
                else:c.reasons.add('MONTANT_COTATION_INVALIDE')
            else:c.volume+=dollars;c.buys+=1
        c.pending_prices=remaining

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
            if 0<=elapsed<=3:self.value_pending(c,now)
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
                if c.pending_pools:c.reasons.add('POOL_PUMPSWAP_EN_COURS')
                code='DONNEES_INSUFFISANTES' if balance is None or c.unknown_position or c.unknown_volume or c.pending_prices or c.pending_pools else 'VOLUME_INSUFFISANT'
                self.notify(c,code,'Fin des 3 s : '+('donnees incompletes.' if code=='DONNEES_INSUFFISANTES' else '400 USD non observes.'),now,True);continue
            if elapsed>=1 and balance is not None and not c.unknown_position and not c.pending_pools:
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
        self.read_rpc=None;self.swap_pools={};self.swap_pending={};self.swap_tasks={}
        self.swap_confirmations={};self.swap_errors={};self.swap_epoch=0

    @staticmethod
    def has_pumpswap(tx):
        return any(isinstance(line,str) and line.startswith('Program '+PUMPSWAP+' invoke') for line in (tx.get('meta') or {}).get('logMessages',[]))

    def needs_position_check(self,trade,tx):
        return trade.get('wallet')==BBIG and trade.get('kind')=='buy' and trade.get('swap_proof_version')==1 and self.has_pumpswap(tx)

    def fail_swap(self,signature,reason):
        self.swap_errors[signature]=reason
        if len(self.swap_errors)>64:self.swap_errors.pop(next(iter(self.swap_errors)))
        saved=self.swap_confirmations.pop(signature,None)
        if not saved:return
        trade=saved[0];mint=trade['mint'];c=self.decisions.candidates.get(mint)
        if c and not c.terminal:
            c.reasons.add(reason);self.decisions.notify(c,'DONNEES_INSUFFISANTES','PumpSwap : donnees du pool indisponibles ou incompletes.',time.monotonic(),True)
        elif mint not in self.decisions.sampled and len(self.decisions.sampled)<self.decisions.limit:
            self.decisions.sampled.add(mint)
            self.emit(dict(v=1,type='paper_decision',mode='SIMULATION',code='DONNEES_INSUFFISANTES',mint=mint,
                initial_signature=signature,missing_reasons=[reason],message='PumpSwap : '+reason+' ; aucune decision.',at=time.time()))

    def reject_swap(self,event,reason):
        self.fail_swap(event['signature'],reason)
        if event.get('user')==BBIG:
            for c in self.decisions.candidates.values():
                if not c.terminal and 0<=event['mono']-c.t0<=1:
                    c.unknown_position=True;c.reasons.add(reason)

    def observe_native(self,events,target,value):
        truncated=any(isinstance(line,str) and 'truncated' in line.lower() for line in value.get('logs',[]))
        receipt=min((e['mono'] for e in events),default=time.monotonic())
        if truncated:self.decisions.unparsed(target,value,receipt)
        for event in events:
            if event.get('platform')!='PumpSwap':
                self.decisions.observe(event);continue
            if event.get('decode_error'):
                self.decisions.unparsed(target,value,event['mono']);self.fail_swap(event['signature'],'FORMAT_PUMPSWAP_INCONNU');continue
            if target[0]=='wallet' and event['user']!=BBIG:continue
            if (target[0]=='wallet' and not event['is_buy'] and not self.swap_tasks and not self.swap_confirmations
                and not any(not c.terminal for c in self.decisions.candidates.values())):continue
            pool=event['pool'];cached=self.swap_pools.get(pool)
            if cached and 0<=time.monotonic()-cached['_cached_at']<=60:
                self.apply_swap(event,target,cached);continue
            if pool not in self.swap_tasks:
                if self.read_rpc is None or len(self.swap_tasks)>=8:
                    self.reject_swap(event,'LECTURE_POOL_PUMPSWAP_INDISPONIBLE');continue
                self.swap_pending[pool]=[]
                task=asyncio.create_task(self.resolve_swap(pool));self.swap_tasks[pool]=task
                task.add_done_callback(lambda task:None if task.cancelled() else task.exception())
            pending=self.swap_pending[pool]
            if event['user']==BBIG:
                for c in self.decisions.candidates.values():
                    if not c.terminal and 0<=event['mono']-c.t0<=1:c.pending_pools.add(pool)
            identity=(event['signature'],event['index'])
            if not any((e['signature'],e['index'])==identity for e,_,_ in pending):
                if len(pending)>=64:self.reject_swap(event,'CAPACITE_PUMPSWAP_DEPASSEE')
                else:pending.append((event,target,self.swap_epoch))
        if truncated:self.decisions.unparsed(target,value,receipt)

    def apply_swap(self,event,target,pool):
        try:normalized=normalize_swap(event,pool)
        except ValueError:
            self.swap_pools.pop(event['pool'],None);self.reject_swap(event,'POOL_PUMPSWAP_INCOHERENT');return
        if target[0]=='token' and normalized['mint']!=target[1]:return
        now=time.monotonic()
        if now-event['mono']>3:
            self.fail_swap(event['signature'],'POOL_PUMPSWAP_VALIDE_TROP_TARD')
            self.decisions.tick(now);return
        fresh=normalized['mint'] not in self.decisions.candidates
        self.decisions.observe(normalized,evaluated_at=now)
        if fresh:
            c=self.decisions.candidates.get(normalized['mint'])
            if c and not c.terminal:
                # Other notifications may have arrived before this mint could
                # be identified. Keep its first-second gate closed until every
                # potentially relevant buffered BBIG movement is resolved.
                for pending_pool,batch in self.swap_pending.items():
                    if any(e.get('user')==BBIG and 0<=e['mono']-c.t0<=1 for e,_,_ in batch):
                        c.pending_pools.add(pending_pool)
        saved=self.swap_confirmations.pop(event['signature'],None)
        if saved:
            if normalized['mint']!=saved[0]['mint']:
                self.swap_confirmations[event['signature']]=saved;self.fail_swap(event['signature'],'ORIENTATION_POOL_PUMPSWAP_NON_SUPPORTEE')
            else:self.confirmed(saved[0],saved[1],max(saved[2],now),processed_mono=saved[3],processed_wall=saved[4],account_snapshot=saved[5])

    async def resolve_swap(self,pool):
        verified=False
        try:
            result=await self.read_rpc('getAccountInfo',[pool,{'encoding':'base64','commitment':'confirmed'}],background=False)
            # A just-created pool may not yet exist at confirmed commitment.
            # At most one extra read, under the same shared quota and deadline.
            oldest=min((e['mono'] for e,_,_ in self.swap_pending.get(pool,[])),default=0)
            if isinstance(result,dict) and result.get('value') is None and self.connected and time.monotonic()-oldest<2.5:
                await asyncio.sleep(.1)
                result=await self.read_rpc('getAccountInfo',[pool,{'encoding':'base64','commitment':'confirmed'}],background=False)
            metadata=decode_pool(result,pool);metadata['_cached_at']=time.monotonic()
            if len(self.swap_pools)>=64:self.swap_pools.pop(next(iter(self.swap_pools)))
            self.swap_pools[pool]=metadata
            verified=True
            for event,target,epoch in self.swap_pending.get(pool,[]):
                if self.connected and epoch==self.swap_epoch:self.apply_swap(event,target,metadata)
                else:self.fail_swap(event['signature'],'FLUX_INTERROMPU')
        except asyncio.CancelledError:raise
        except Exception:
            for event,_,_ in self.swap_pending.get(pool,[]):self.fail_swap(event['signature'],'POOL_PUMPSWAP_NON_VERIFIE')
        finally:
            self.swap_pending.pop(pool,None);self.swap_tasks.pop(pool,None)
            for c in self.decisions.candidates.values():
                if pool in c.pending_pools:
                    c.pending_pools.discard(pool)
                    if not verified:c.unknown_position=True;c.reasons.add('POOL_PUMPSWAP_NON_VERIFIE')
            self.decisions.tick(time.monotonic())

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
            covered=self.connected and (trade.get('processed_seen_at') or 0)>=self.ready_wall
            if covered and self.needs_position_check(trade,tx):
                self.swap_confirmations[trade['signature']]=(trade,tx,max(mono,time.monotonic()),processed_mono,processed_wall,account_snapshot)
                if len(self.swap_confirmations)>64:self.swap_confirmations.pop(next(iter(self.swap_confirmations)))
                if trade['signature'] in self.swap_errors:
                    self.fail_swap(trade['signature'],self.swap_errors[trade['signature']]);return
                events=native_trades({'signature':trade['signature'],'err':(tx.get('meta') or {}).get('err'),
                    'logs':(tx.get('meta') or {}).get('logMessages',[])},tx.get('slot',0),
                    processed_wall if processed_wall is not None else time.time(),processed_mono if processed_mono is not None else mono)
                events=[event for event in events if event.get('platform')=='PumpSwap' and (event.get('decode_error') or event.get('user')==BBIG)]
                if not events:self.fail_swap(trade['signature'],'EVENEMENT_PUMPSWAP_ABSENT')
                else:self.observe_native(events,('wallet',BBIG),{'logs':(tx.get('meta') or {}).get('logMessages',[])})
                return
            self.unsupported.add(trade['mint']);self.decisions.sampled.add(trade['mint'])
            self.emit(dict(v=1,type='paper_decision',mode='SIMULATION',code='NON_SUPPORTE' if covered else 'DONNEES_INSUFFISANTES',mint=trade['mint'],
                           message='Pas d evenement Pump.fun/PumpSwap exploitable ou flux non pret au signal ; aucune decision.',at=time.time()))

    async def run(self):
        from websockets.asyncio.client import connect
        self.write_record(dict(type='paper_session',session_id=self.session,limit=self.decisions.limit,at=time.time()))
        price_tasks=[asyncio.create_task(self.prices.run()),asyncio.create_task(self.prices.run_doge())] if getattr(self,'manage_prices',True) else []
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
                        print('CONDITIONS REELLES : flux Pump.fun et PumpSwap.' if getattr(self,'is_live',False) else 'SIMULATION : logs Pump.fun et PumpSwap ; aucun achat reel.',flush=True)
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
                                        self.observe_native(events,target,value)
                                        if not events:self.decisions.unparsed(target,value,mono)
                            self.decisions.tick(mono)
                            if len(self.results)>=self.decisions.limit:
                                summary=dict(type='paper_summary',session_id=self.session,tokens=len(self.results),
                                             outcomes=dict(Counter(self.results.values())),at=wall)
                                self.write_record(summary)
                                print(f'CONDITIONS TERMINEES : {self.decisions.limit} tokens ; suivi des positions reelles maintenu.' if getattr(self,'is_live',False) else f'SIMULATION TERMINEE : bilan de {self.decisions.limit} tokens enregistre ; surveillance GMGN maintenue.',flush=True)
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
                    self.connected=False;self.swap_epoch+=1;self.decisions.interrupted(time.monotonic())
                    print('SIMULATION : flux indisponible, reprise dans 3 s ; decisions actives abandonnees.',flush=True)
                    await asyncio.sleep(3)
        finally:
            self.decisions.interrupted(time.monotonic())
            tasks=price_tasks+list(self.swap_tasks.values())
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)


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
    print('SOL/PUMP/TTWO USD indicatifs CoinGecko ; DOGE USD bougie minute GeckoTerminal ; age maximum 90 s ; USDC nominal 1 USD.')
