"""Explicit, bounded Wallet Radar live experiment. Never used by simulation mode.

All signing is local. Quotes and instructions come from the fixed Jupiter host;
instructions, signers, fees, simulation and the durable budget are checked before send.
"""
import argparse
import asyncio
import base64
from decimal import Decimal, ROUND_CEILING
import getpass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import socket
import ssl
import stat
import struct
import threading
import time
import urllib.parse
import urllib.error
import urllib.request

from wallet_radar_paper import PaperObserver, SOL, BBIG, USDC

ROOT=Path(__file__).resolve().parents[1]
OWNER='E23vfW9oA9CnANP5wzimpo3DM3WQtMuxzUP4oxFp97Jq'
PRIVATE=ROOT/'private_wallet_radar'
JUP='JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'
TOKEN='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN22='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ATA='ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
SYSTEM='11111111111111111111111111111111'
COMPUTE='ComputeBudget111111111111111111111111111111'
SLIPPAGE=2000
BUDGET=Decimal('15')
BUY=Decimal('1')
FEE=Decimal('.10')
RENT=Decimal('.50')
# Principal + buy fee/rent + three exit attempts' fees + exit rent.
# Charge the entire envelope permanently, even after a failed transaction/positive exit.
ENVELOPE=BUY+FEE+RENT+3*FEE+RENT
JUPITER_ERRORS={
    'COULD_NOT_FIND_ANY_ROUTE':'aucune route trouvee',
    'NO_ROUTES_FOUND':'aucune route trouvee',
    'TOKEN_NOT_TRADABLE':'token non negociable',
    'AMOUNT_TOO_SMALL':'montant trop petit',
    'AMOUNT_TOO_LARGE':'montant trop grand',
    'ROUTE_PLAN_DOES_NOT_CONSUME_ALL_THE_AMOUNT':'route incompatible avec le montant',
    'NOT_SUPPORTED':'option non prise en charge',
    'INVALID_QUERY_PARAMETER':'parametre refuse',
    'INVALID_API_KEY':'acces API refuse',
    'UNAUTHORIZED':'acces API refuse',
    'CANNOT_COMPUTE_OTHER_AMOUNT_THRESHOLD':'minimum de reception non calculable',
    'FAILED_TO_GET_SWAP_AND_ACCOUNT_METAS':'construction de route indisponible',
    'BLOCKHASH_NOT_FOUND':'blockhash indisponible'}


def jupiter_reason(data):
    """Only known machine codes/static descriptions can leave a provider response."""
    if not isinstance(data,dict):return None
    code=data.get('errorCode')
    if isinstance(code,str) and code in JUPITER_ERRORS:return code+' : '+JUPITER_ERRORS[code]
    # Some quote errors have no errorCode. Classify known text, never echo it.
    error=data.get('error') or data.get('message')
    if isinstance(error,dict):error=error.get('message')
    if isinstance(error,str):
        text=error.lower()
        if 'restrict_intermediate_tokens' in text or 'restrictintermediatetokens' in text:
            return 'parametre restrictIntermediateTokens refuse ou requis'
        if 'could not find any route' in text or 'no routes found' in text:return 'aucune route trouvee'
        if 'not tradable' in text:return 'token non negociable'
        if 'amount too small' in text:return 'montant trop petit'
        if 'api key' in text and any(word in text for word in ('invalid','missing','unauthorized')):
            return 'cle API Jupiter absente ou refusee'
        if 'query' in text and ('deserialize' in text or 'parse' in text):return 'format de parametre de cotation refuse'
    return None


def quote_path(source,dest,amount):
    query=urllib.parse.urlencode(dict(inputMint=source,outputMint=dest,amount=amount,slippageBps=SLIPPAGE,
        swapMode='ExactIn',onlyDirectRoutes='true',restrictIntermediateTokens='true',asLegacyTransaction='true'))
    return '/swap/v1/quote?'+query


class TradeError(Exception):
    """Messages are fixed and contain no keys, URLs or provider bodies."""
    def __init__(self,message,status=None):
        super().__init__(message);self.status=status


def key_format_problem(api):
    if not api or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in api):return 'caracteres non ASCII, espaces ou caracteres de controle'
    if api.startswith(('http://','https://')):return 'URL copiee au lieu de la cle seule'
    if api.startswith(('"',"'",'{','[')):return 'guillemets ou objet copie au lieu de la cle seule'
    return None


def update_jupiter():
    # Only this API credential is touched; never read or rewrite the wallet key.
    api=input('Nouvelle cle API Jupiter seule (saisie VISIBLE) : ').strip()
    problem=key_format_problem(api)
    if problem or len(api)>512:raise TradeError('Cle Jupiter non enregistree : '+(problem or 'format trop long')+'.')
    PRIVATE.mkdir(mode=0o700,exist_ok=True)
    if PRIVATE.is_symlink():raise TradeError('Dossier prive symbolique refuse.')
    os.chmod(PRIVATE,0o700)
    path=PRIVATE/'jupiter_api_key.txt'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',closefd=False) as handle:handle.write(api+'\n');handle.flush();os.fsync(fd)
    finally:os.close(fd)
    print('Cle Jupiter mise a jour localement. Cle wallet inchangee ; aucun ordre envoye.')


def secure_read(path):
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(path,flags)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_uid!=os.getuid():
            raise TradeError('Fichier prive : proprietaire ou permissions invalides (0600 requis).')
        with os.fdopen(fd,'r',encoding='utf-8',closefd=False) as handle:return handle.read().strip()
    finally:os.close(fd)


def load_signer():
    from solders.keypair import Keypair
    try:
        value=secure_read(PRIVATE/'wallet.json')
        key=Keypair.from_bytes(bytes(json.loads(value)))
    except Exception:raise TradeError('Signature locale absente ou invalide : configurer le wallet avec le menu 7.') from None
    if str(key.pubkey())!=OWNER:raise TradeError('Le wallet de signature ne correspond pas au wallet de test.')
    return key


def setup(reuse=False):
    from solders.keypair import Keypair
    print('Saisie locale : cle wallet masquee ; cle API Jupiter visible. Ne pas envoyer de cle dans le chat.')
    if reuse and (PRIVATE/'wallet.json').exists():key=load_signer()
    else:
        raw=getpass.getpass('Cle privee du wallet de test (base58 ou tableau JSON) : ').strip()
        key=Keypair.from_bytes(bytes(json.loads(raw))) if raw.startswith('[') else Keypair.from_base58_string(raw)
    if str(key.pubkey())!=OWNER:raise TradeError('Cle refusee : autre adresse publique.')
    api=secure_read(PRIVATE/'jupiter_api_key.txt') if reuse and (PRIVATE/'jupiter_api_key.txt').exists() else input('Cle API Jupiter gratuite (saisie VISIBLE) : ').strip()
    if not api or len(api)>512 or any(c.isspace() for c in api):raise TradeError('Cle Jupiter invalide.')
    PRIVATE.mkdir(mode=0o700,exist_ok=True)
    if PRIVATE.is_symlink():raise TradeError('Dossier prive symbolique refuse.')
    os.chmod(PRIVATE,0o700)
    for name,value in [('wallet.json',json.dumps(list(bytes(key)))),('jupiter_api_key.txt',api)]:
        path=PRIVATE/name
        if reuse and path.exists():continue
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'w',closefd=False) as handle:handle.write(value+'\n');handle.flush();os.fsync(fd)
        finally:os.close(fd)
    print('Signature configuree/conservee pour '+OWNER+'. Aucun ordre envoye.')


class Ledger:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        if path.is_symlink():raise TradeError('Journal symbolique refuse.')
        self.db=sqlite3.connect(path)
        os.chmod(path,0o600)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS trades(mint TEXT PRIMARY KEY,state TEXT NOT NULL,
            reserved TEXT NOT NULL DEFAULT '0',data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS notices(at REAL, mint TEXT, code TEXT, data TEXT);''')

    def rows(self):return [dict(row) for row in self.db.execute('SELECT * FROM trades')]

    def used(self):return sum((Decimal(row['reserved']) for row in self.rows()),Decimal(0))

    def active(self):return [row for row in self.rows() if row['state'] in ('buy_pending','open','sell_pending','manual')]

    def get(self,mint):
        row=self.db.execute('SELECT * FROM trades WHERE mint=?',(mint,)).fetchone()
        return dict(row) if row else None

    def save(self,mint,state,data,reserve=None):
        previous=self.get(mint)
        value=str(reserve) if reserve is not None else previous['reserved'] if previous else '0'
        self.db.execute('INSERT OR REPLACE INTO trades VALUES(?,?,?,?)',(mint,state,value,json.dumps(data)))
        self.db.commit()

    def reserve(self,mint,data):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.active() or self.get(mint) or self.used()+ENVELOPE>BUDGET:
                raise TradeError('Position deja active, token deja traite ou budget reserve insuffisant.')
            self.db.execute('INSERT INTO trades VALUES(?,?,?,?)',(mint,'buy_pending',str(ENVELOPE),json.dumps(data)))
            self.db.commit()
        except BaseException:self.db.rollback();raise

    def notice(self,event):
        self.db.execute('INSERT INTO notices VALUES(?,?,?,?)',(event['at'],event['mint'],event['code'],json.dumps(event)))
        self.db.commit()


class Transport:
    def __init__(self,rpc_url,api_key):
        self.url=rpc_url;self.key=api_key
        self.locks={'rpc':threading.Lock(),'jup':threading.Lock()};self.next_rpc=0;self.next_jup=0
        self.pause_rpc=0;self.pause_jup=0
        # Use the installed client already used by the wallet monitor, without
        # changing hosts, credentials, TLS verification or authentication rules.
        from curl_cffi import requests
        self.jupiter_http=requests.Session(impersonate='chrome')

    def request(self,url,body=None,jupiter=False,deadline=None,capture=None,label='Fournisseur'):
        # Fixed host for Jupiter. Keys only in the request header, never stdout.
        name='jup' if jupiter else 'rpc'
        with self.locks[name]:
            now=time.monotonic()
            if now<getattr(self,'pause_'+name):raise TradeError(label+' : pause apres limitation, attendre 60 s.')
            time.sleep(max(0,getattr(self,'next_'+name)-now))
            if deadline is not None and time.monotonic()>deadline:
                raise TradeError('Limite de trois secondes depassee avant requete.')
            setattr(self,'next_'+name,time.monotonic()+(1 if jupiter else 1/3))
            headers={'Accept':'application/json'}
            if body is not None:headers['Content-Type']='application/json'
            if jupiter:headers['x-api-key']=self.key
            req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                if deadline is not None and time.monotonic()>deadline:raise TradeError('Limite de trois secondes depassee avant requete.')
                if capture is not None:capture['request_at']=time.time()
                if jupiter:
                    response=self.jupiter_http.request('GET' if body is None else 'POST',url,json=body,
                        headers=headers,timeout=5,allow_redirects=False)
                    if response.status_code!=200:
                        raise urllib.error.HTTPError('Jupiter',response.status_code,'Reponse refusee',response.headers,
                                                     io.BytesIO(response.content[:8192]))
                    value=response.json()
                else:
                    with urllib.request.urlopen(req,timeout=5) as response:value=json.load(response)
            except Exception as exc:
                if isinstance(exc,TradeError):raise
                code=getattr(exc,'code',None)
                if isinstance(code,int) and not isinstance(code,bool) and 100<=code<=599:
                    if code==429:setattr(self,'pause_'+name,time.monotonic()+60)
                    detail='HTTP '+str(code)
                    if jupiter:
                        try:
                            error_data=json.loads(exc.read(8192))
                            safe=jupiter_reason(error_data)
                            if safe:detail+=' ; '+safe
                            else:detail+=' ; motif JSON non reconnu'
                        except Exception:
                            content_type=(getattr(exc,'headers',None) or {}).get('Content-Type','').lower()
                            detail+=' ; reponse HTML' if 'text/html' in content_type else ' ; reponse non JSON'
                else:
                    reason=getattr(exc,'reason',exc)
                    detail=('delai depasse' if isinstance(reason,(TimeoutError,socket.timeout)) or (jupiter and code==28) else
                            'certificat/TLS' if isinstance(reason,ssl.SSLError) or (jupiter and code==60) else
                            'resolution DNS' if isinstance(reason,socket.gaierror) or (jupiter and code==6) else 'connexion ou format JSON indisponible')
                status=code if isinstance(code,int) and not isinstance(code,bool) and 100<=code<=599 else None
                raise TradeError(label+' : '+detail+'. URL, cles et reponse privee masquees.',status=status) from None
            if not isinstance(value,dict) or value.get('error') is not None:
                error=value.get('error') if isinstance(value,dict) else None
                code=error.get('code') if isinstance(error,dict) else None
                detail=' ; code RPC '+str(code) if isinstance(code,int) and not isinstance(code,bool) else ''
                if jupiter:
                    safe=jupiter_reason(value)
                    if safe:detail+=' ; '+safe
                if code==429:setattr(self,'pause_'+name,time.monotonic()+60)
                raise TradeError(label+' : reponse refusee ou invalide'+detail+'.')
            return value

    def rpc(self,method,params):
        known={'getBalance','getLatestBlockhash','getFeeForMessage','getMultipleAccounts','getTokenAccountsByOwner',
               'getRecentPrioritizationFees','simulateTransaction','getSignatureStatuses','getTransaction','getBlockHeight'}
        value=self.request(self.url,{'jsonrpc':'2.0','id':1,'method':method,'params':params},label='RPC/'+method if method in known else 'RPC')
        if 'result' not in value:raise TradeError('Resultat RPC absent.')
        return value['result']

    def jup(self,path,body=None):
        label='Jupiter/cotation' if path.startswith('/swap/v1/quote?') else 'Jupiter/construction' if path=='/swap/v1/swap-instructions' else 'Jupiter'
        return self.request('https://api.jup.ag'+path,body,jupiter=True,label=label)

    def quote_probe(self,kind):
        """Two fixed diagnostic GETs only. Never used by order construction/send."""
        if kind not in ('public','invalid'):raise TradeError('Controle de cotation non autorise.')
        with self.locks['jup']:
            time.sleep(max(0,self.next_jup-time.monotonic()));self.next_jup=time.monotonic()+1
            headers={'Accept':'application/json'}
            if kind=='invalid':headers['x-api-key']='wallet-radar-invalid-key-probe'
            try:
                response=self.jupiter_http.request('GET','https://api.jup.ag'+quote_path(SOL,USDC,1000000),
                    headers=headers,timeout=5,allow_redirects=False)
                status=response.status_code
                if not isinstance(status,int) or isinstance(status,bool) or not 100<=status<=599:return {'status':None,'valid':False}
                valid=False
                if status==200:
                    try:quote_check(response.json(),SOL,USDC,1000000);valid=True
                    except Exception:pass
                return {'status':status,'valid':valid}
            except Exception:return {'status':None,'valid':False}

    def compare_quotes(self):
        print('COMPARAISON LECTURE SEULE : aucun wallet signe, aucun ordre.',flush=True)
        problem=key_format_problem(self.key)
        print('Format cle enregistree : '+(problem if problem else 'aucun defaut simple detecte (validite non prouvee)'),flush=True)
        proxy=any(os.environ.get(name) for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'))
        print('Proxy declare dans l environnement : '+('oui' if proxy else 'non')+' (valeurs masquees).',flush=True)
        results={}
        for kind,label in [('public','SANS CLE'),('invalid','CLE FACTICE')]:
            result=self.quote_probe(kind);results[kind]=result
            print(label+' : '+('HTTP '+str(result['status']) if result['status'] else 'connexion indisponible')+
                  (' ; cotation valide' if result['valid'] else ''),flush=True)
        if results['public']['valid'] and results['invalid']['status']==401:
            print('Le service repond et rejette normalement la cle factice. Verifier le format/type/acces de la cle enregistree ; menu 11 pour remplacer seulement Jupiter.',flush=True)
        elif results['public']['status']==400:
            print('Le refus 400 existe aussi sans cle : une regeneration de cle n est pas justifiee par ce resultat seul.',flush=True)
        elif results['public']['valid'] and results['invalid']['status']==400:
            print('Le refus depend de l entete d authentification, meme factice ; verifier acces/intermediaire avant regeneration.',flush=True)
        else:print('Comparaison non concluante. Conserver les cles et relever uniquement ces statuts.',flush=True)
        return results

    def send(self,raw,deadline=None,capture=None):
        return self.request(self.url,{'jsonrpc':'2.0','id':1,'method':'sendTransaction','params':[
            raw,{'encoding':'base64','skipPreflight':False,'maxRetries':2}]},deadline=deadline,capture=capture,label='RPC/envoi')['result']


def lamports(usd,price):return int((Decimal(usd)*10**9/price).to_integral_value(rounding=ROUND_CEILING))


def associated(mint,program=TOKEN):
    from solders.pubkey import Pubkey
    return str(Pubkey.find_program_address([bytes(Pubkey.from_string(OWNER)),bytes(Pubkey.from_string(program)),
                                         bytes(Pubkey.from_string(mint))],Pubkey.from_string(ATA))[0])


def quote_check(q,source,destination,amount):
    if (q.get('inputMint')!=source or q.get('outputMint')!=destination or q.get('swapMode')!='ExactIn'
        or int(q.get('inAmount',0))!=amount or int(q.get('slippageBps',-1))!=SLIPPAGE
        or int(q.get('outAmount',0))<=0 or not q.get('routePlan')):
        raise TradeError('Cotation incompatible avec le montant, le token ou le glissement.')
    expected=int(q['outAmount'])*(10000-SLIPPAGE)//10000
    if not expected<=int(q.get('otherAmountThreshold',0))<=int(q['outAmount']):
        raise TradeError('Minimum de reception incompatible avec le glissement.')
    if q.get('platformFee') and int(q['platformFee'].get('feeBps',0)):
        raise TradeError('Frais de plateforme inattendus.')


def checked_instructions(payload,q,priority):
    """Build our own message; never blindly sign a provider transaction.

    Only v1 exact-in route/shared_accounts_route, own ATAs, SOL wrapping and
    compute-budget instructions. Unsupported future routes fail closed.
    """
    from solders.instruction import Instruction, AccountMeta
    from solders.pubkey import Pubkey
    from solders.compute_budget import set_compute_unit_limit,set_compute_unit_price
    source,dest=q['inputMint'],q['outputMint'];amount=int(q['inAmount'])
    valid_atas={associated(m,token) for m in (source,dest) for token in (TOKEN,TOKEN22)}
    wsol=associated(SOL)
    rows=payload.get('setupInstructions',[])+[payload.get('swapInstruction')]
    if payload.get('cleanupInstruction'):rows.append(payload['cleanupInstruction'])
    if payload.get('otherInstructions') or payload.get('tokenLedgerInstruction'):
        raise TradeError('Instructions auxiliaires non prises en charge.')
    instructions=[set_compute_unit_limit(1_400_000),set_compute_unit_price(priority)]
    routes=0;transfers=0
    for row in rows:
        if not isinstance(row,dict):raise TradeError('Instruction manquante.')
        pid=row['programId'];accounts=row['accounts'];keys=[a['pubkey'] for a in accounts]
        data=base64.b64decode(row['data'],validate=True)
        if any(a['isSigner'] and a['pubkey']!=OWNER for a in accounts):raise TradeError('Autre signataire refuse.')
        if pid==JUP:
            routes+=1
            if len(data)<27:raise TradeError('Route tronquee.')
            if data[:8]==hashlib.sha256(b'global:route').digest()[:8]:
                if len(keys)<9 or keys[1]!=OWNER or keys[2] not in valid_atas or keys[3] not in valid_atas or keys[5]!=dest:
                    raise TradeError('Comptes de route incompatibles.')
                if keys[2]!=associated(source,TOKEN) and keys[2]!=associated(source,TOKEN22):raise TradeError('Compte source refuse.')
                if keys[3]!=associated(dest,TOKEN) and keys[3]!=associated(dest,TOKEN22):raise TradeError('Compte destination refuse.')
                if keys[4]!=JUP or keys[6]!=JUP:raise TradeError('Destination ou frais externes refuses.')
            elif data[:8]==hashlib.sha256(b'global:shared_accounts_route').digest()[:8]:
                if (len(keys)<13 or keys[2]!=OWNER or keys[3] not in {associated(source,TOKEN),associated(source,TOKEN22)}
                    or keys[6] not in {associated(dest,TOKEN),associated(dest,TOKEN22)} or keys[7]!=source
                    or keys[8]!=dest or keys[9]!=JUP):raise TradeError('Comptes partages incompatibles.')
            else:raise TradeError('Version de route Jupiter non prise en charge.')
            in_amount,out_amount,slip,fee=struct.unpack('<QQHB',data[-19:])
            if (in_amount!=amount or out_amount!=int(q['outAmount']) or slip!=SLIPPAGE or fee):
                raise TradeError('Montants signes differents de la cotation.')
        elif pid==ATA:
            if (data not in (b'',b'\x00',b'\x01') or len(keys)<6 or keys[0]!=OWNER or keys[2]!=OWNER
                or keys[3] not in (source,dest) or keys[4]!=SYSTEM or keys[5] not in (TOKEN,TOKEN22)
                or keys[1]!=associated(keys[3],keys[5])):raise TradeError('Creation de compte non autorisee.')
        elif pid==SYSTEM:
            if len(data)!=12 or struct.unpack('<I',data[:4])[0]!=2 or keys[:2]!=[OWNER,wsol]:
                raise TradeError('Instruction systeme refusee.')
            transfers+=struct.unpack('<Q',data[4:])[0]
        elif pid in (TOKEN,TOKEN22):
            if not ((data==b'\x11' and keys==[wsol] and pid==TOKEN)
                    or (data==b'\x09' and keys==[wsol,OWNER,OWNER] and pid==TOKEN)):
                raise TradeError('Instruction token autre que wrapping SOL refusee.')
        else:raise TradeError('Programme de premier niveau non autorise.')
        instructions.append(Instruction(Pubkey.from_string(pid),data,[AccountMeta(Pubkey.from_string(a['pubkey']),a['isSigner'],a['isWritable']) for a in accounts]))
    if routes!=1 or transfers!=(amount if source==SOL else 0):raise TradeError('Route ou transfert SOL incoherent.')
    return instructions


class Executor:
    def __init__(self,rpc_url,path,prices):
        self.key=load_signer();api=secure_read(PRIVATE/'jupiter_api_key.txt')
        if not api:raise TradeError('Cle Jupiter absente.')
        self.transport=Transport(rpc_url,api);self.ledger=Ledger(path);self.prices=prices
        self.priority=None;self.priority_at=0;self.preparing=None;self.prepared_mint=None
        self.jobs=asyncio.Queue(maxsize=1)
        self.last_notice={}

    async def rpc(self,method,params):return await asyncio.to_thread(self.transport.rpc,method,params)
    async def jup(self,path,body=None):return await asyncio.to_thread(self.transport.jup,path,body)

    def price(self):
        value=self.prices.usd
        if value is None or not 0<=time.time()-self.prices.published<=90:raise TradeError('Cours SOL/USD absent ou perime.')
        return value

    def notify(self,mint,code,message,**details):
        if code=='SORTIE_INDISPONIBLE':
            if time.monotonic()-self.last_notice.get(mint,0)<30:return
            self.last_notice[mint]=time.monotonic()
        event=dict(v=1,type='live_decision',mode='LIVE',mint=mint,code=code,message=message,at=time.time(),**details)
        self.ledger.notice(event)
        print('RADAR_LIVE '+json.dumps(event,separators=(',',':')),flush=True)

    async def fees(self):
        while True:
            try:
                rows=await self.rpc('getRecentPrioritizationFees',[[]])
                values=sorted(int(row['prioritizationFee']) for row in rows if int(row['prioritizationFee'])>=0)
                if values:self.priority=max(100_000,values[int((len(values)-1)*.75)]);self.priority_at=time.monotonic()
            except Exception:pass
            await asyncio.sleep(10)

    def signal(self,event):
        mint=event['mint'];code=event['code']
        if code=='SUIVI':
            if not self.ledger.active() and self.ledger.get(mint) is None and self.ledger.used()+ENVELOPE<=BUDGET:
                if self.preparing is None or self.preparing.done():
                    self.prepared_mint=mint
                    self.preparing=asyncio.create_task(self.prepare(mint,'buy'))
                    # Retrieve exceptions even if the strategy later abandons this token.
                    self.preparing.add_done_callback(lambda task:None if task.cancelled() else task.exception())
        elif code=='ACHAT_SIMULE':
            deadline=time.monotonic()+max(0,3-event['elapsed_s'])
            try:self.jobs.put_nowait((mint,deadline,event['elapsed_s']))
            except asyncio.QueueFull:self.notify(mint,'ACHAT_IGNORE','Autre decision deja en cours.')

    async def quote(self,source,dest,amount):
        q=await self.jup(quote_path(source,dest,amount));quote_check(q,source,dest,amount);return q

    async def holdings(self,mint):
        result=await self.rpc('getTokenAccountsByOwner',[OWNER,{'mint':mint},{'encoding':'jsonParsed','commitment':'confirmed'}])
        return sum(int(a['account']['data']['parsed']['info']['tokenAmount']['amount']) for a in result['value'])

    async def prepare(self,mint,side,quantity=None,q=None):
        from solders.hash import Hash
        from solders.message import MessageV0,to_bytes_versioned
        from solders.pubkey import Pubkey
        from solders.signature import Signature
        from solders.transaction import VersionedTransaction
        start=time.monotonic();price=self.price()
        if self.priority is None or time.monotonic()-self.priority_at>30:raise TradeError('Estimation de priorite perimee.')
        fee_lamports=lamports(FEE,price)
        if 5000+math.ceil(self.priority*1_400_000/10**6)>fee_lamports:raise TradeError('Priorite estimee au-dessus du plafond de frais.')
        amount=lamports(BUY,price) if side=='buy' else int(quantity)
        source,dest=(SOL,mint) if side=='buy' else (mint,SOL)
        # Independent reads overlap the HTTP quote/build path, with separate rate buckets.
        owned,q,block,balance_result=await asyncio.gather(
            self.holdings(mint) if side=='buy' else asyncio.sleep(0,result=0),
            self.quote(source,dest,amount) if q is None else asyncio.sleep(0,result=q),
            self.rpc('getLatestBlockhash',[{'commitment':'confirmed'}]),
            self.rpc('getBalance',[OWNER,{'commitment':'confirmed'}]))
        if side=='buy' and owned!=0:raise TradeError('Position preexistante sur ce token : achat ignore.')
        balance=balance_result['value']
        if side=='buy' and balance<lamports(ENVELOPE,price):raise TradeError('SOL insuffisant pour achat, depots et reserve de sortie.')
        quote_check(q,source,dest,amount)
        payload=await self.jup('/swap/v1/swap-instructions',dict(quoteResponse=q,userPublicKey=OWNER,
                    wrapAndUnwrapSol=True,useSharedAccounts=False,asLegacyTransaction=True,dynamicComputeUnitLimit=False))
        instructions=checked_instructions(payload,q,self.priority)
        message=MessageV0.try_compile(Pubkey.from_string(OWNER),instructions,[],Hash.from_string(block['value']['blockhash']))
        unsigned=VersionedTransaction.populate(message,[Signature.default()])
        if len(bytes(unsigned))>1232:raise TradeError('Route trop volumineuse pour cette premiere version.')
        fee=(await self.rpc('getFeeForMessage',[base64.b64encode(to_bytes_versioned(message)).decode(),{'commitment':'confirmed'}]))['value']
        if fee is None or fee>fee_lamports:raise TradeError('Frais reseau au-dessus du plafond.')
        created=list({a['pubkey'] for row in payload.get('setupInstructions',[]) if row['programId']==ATA for a in row['accounts'][1:2]})
        previous=(await self.rpc('getMultipleAccounts',[created,{'encoding':'base64','commitment':'confirmed'}]))['value'] if created else []
        result=await self.rpc('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),{
            'encoding':'base64','sigVerify':False,'commitment':'confirmed',
            'accounts':{'encoding':'base64','addresses':[OWNER]+created}}])
        sim=result['value']
        if sim.get('err') is not None:raise TradeError('Simulation de transaction refusee.')
        post=sim.get('accounts') or []
        if not post or post[0] is None or post[0].get('owner')!=SYSTEM:raise TradeError('Solde simule du wallet indisponible.')
        if len(post)!=len(created)+1:raise TradeError('Comptes simules incomplets.')
        rent=sum(max(0,(a or {}).get('lamports',0)-(b or {}).get('lamports',0)) for a,b in zip(post[1:],previous))
        if rent>lamports(RENT,price):raise TradeError('Depot de comptes au-dessus du plafond reserve.')
        debit=balance-int(post[0]['lamports'])
        cap=lamports((BUY if side=='buy' else Decimal(0))+RENT+FEE,price)
        if debit>cap:raise TradeError('Depense SOL simulee au-dessus du plafond achat/frais/depots.')
        return dict(message=base64.b64encode(to_bytes_versioned(message)).decode(),height=block['value']['lastValidBlockHeight'],
                    amount=amount,price=str(price),fee_lamports=fee,rent_lamports=rent,
                    prepared=time.monotonic(),prepare_ms=round((time.monotonic()-start)*1000),side=side)

    def signed(self,p):
        from solders.message import from_bytes_versioned
        from solders.transaction import VersionedTransaction
        price=self.price()
        if (time.monotonic()-p['prepared']>2 or time.monotonic()-self.priority_at>30
            or p['fee_lamports']>lamports(FEE,price) or p['rent_lamports']>lamports(RENT,price)
            or (p['side']=='buy' and p['amount']>lamports(BUY,price))):
            raise TradeError('Preparation perimee ou plafond USD depasse avant signature.')
        tx=VersionedTransaction(from_bytes_versioned(base64.b64decode(p['message'])),[self.key])
        return dict(raw=base64.b64encode(bytes(tx)).decode(),signature=str(tx.signatures[0]),height=p['height'])

    async def buy_loop(self):
        while True:
            mint,deadline,elapsed=await self.jobs.get()
            try:
                if self.ledger.active() or self.ledger.get(mint) or self.ledger.used()+ENVELOPE>BUDGET:
                    raise TradeError('Position active, token deja traite ou budget insuffisant.')
                if self.prepared_mint!=mint or self.preparing is None:raise TradeError('Preparation non disponible pour ce signal.')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TradeError('Limite de trois secondes depassee.')
                p=await asyncio.wait_for(asyncio.shield(self.preparing),remaining)
                if time.monotonic()>deadline or time.monotonic()-p['prepared']>2:raise TradeError('Preparation trop ancienne ou limite de trois secondes depassee.')
                # Recheck the USD reference, priority freshness and fee cap before signing.
                current_price=self.price()
                if time.monotonic()-self.priority_at>30:raise TradeError('Priorite perimee.')
                tx=self.signed(p)
                data=dict(buy=tx,buy_amount=p['amount'],entry_usd=str(Decimal(p['amount'])/10**9*current_price),
                          price=str(current_price),decision_elapsed_s=elapsed,prepare_ms=p['prepare_ms'],sell_attempts=0,
                          decision_wall=time.time()-(3-elapsed-(deadline-time.monotonic())),signed_at=time.time())
                if time.monotonic()>deadline:raise TradeError('Limite de trois secondes depassee avant envoi.')
                self.ledger.reserve(mint,data)  # Durable BEFORE any possible network submission.
                # All sends below use the saved signature; errors never create a second buy.
                await self.send(mint,data,'buy',deadline)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.notify(mint,'ACHAT_IGNORE',str(exc) if isinstance(exc,TradeError) else 'Preparation impossible ; aucun nouvel ordre.')

    async def send(self,mint,data,side,deadline=None):
        data[side]['queued_at']=time.time();self.ledger.save(mint,side+'_pending',data)
        capture={}
        try:
            try:result=await asyncio.to_thread(self.transport.send,data[side]['raw'],deadline,capture)
            finally:
                if 'request_at' in capture:
                    data[side]['send_at']=capture['request_at']
                    current=self.ledger.get(mint);saved=json.loads(current['data'])
                    saved[side]['send_at']=capture['request_at']
                    self.ledger.save(mint,current['state'],saved)
            if result!=data[side]['signature']:raise TradeError('Signature renvoyee incoherente.')
            self.notify(mint,'ORDRE_ENVOYE',side+' : transaction envoyee, confirmation attendue.',signature=result,
                        prepare_ms=data.get('prepare_ms'),decision_to_send_ms=round((data[side]['send_at']-data['decision_wall'])*1000) if data[side].get('send_at') and side=='buy' else None)
        except Exception:
            self.notify(mint,'ORDRE_A_VERIFIER','Reponse inconnue ; suivi de la meme signature, aucun nouvel ordre.',signature=data[side]['signature'])

    async def reconcile(self,row):
        mint=row['mint'];data=json.loads(row['data']);side='buy' if row['state']=='buy_pending' else 'sell'
        tx=data[side];status=(await self.rpc('getSignatureStatuses',[[tx['signature']],{'searchTransactionHistory':True}]))['value'][0]
        if status and status.get('confirmationStatus') in ('confirmed','finalized'):
            full=await self.rpc('getTransaction',[tx['signature'],{'encoding':'jsonParsed','commitment':'confirmed','maxSupportedTransactionVersion':0}])
            if not full or not full.get('meta'):return
            latest=json.loads(self.ledger.get(mint)['data'])
            data[side].update(latest.get(side,{}));tx=data[side]
            meta=full['meta']
            if meta.get('err') is not None:
                target='failed' if side=='buy' else 'open' if data['sell_attempts']<3 else 'manual'
                self.ledger.save(mint,target,data)
                self.notify(mint,'ORDRE_ECHOUE','Transaction echouee ; frais possibles, enveloppe conservee.',signature=tx['signature']);return
            before=sum(int(b['uiTokenAmount']['amount']) for b in meta.get('preTokenBalances',[]) if b.get('owner')==OWNER and b.get('mint')==mint)
            after=sum(int(b['uiTokenAmount']['amount']) for b in meta.get('postTokenBalances',[]) if b.get('owner')==OWNER and b.get('mint')==mint)
            if side=='buy' and after>before:
                data['quantity']=after-before;data['confirmed_at']=time.time()
                self.ledger.save(mint,'open',data)
                self.notify(mint,'ACHAT_CONFIRME','Achat reel confirme ; surveillance SL -80 % / TP +75 % active.',signature=tx['signature'],
                            send_to_confirm_ms=round((time.time()-tx['send_at'])*1000) if tx.get('send_at') else None,quantity=str(data['quantity']))
            elif side=='sell' and before-after==data['quantity']:
                self.ledger.save(mint,'closed',data)
                self.notify(mint,'VENTE_CONFIRMEE','Vente totale confirmee : '+data.get('exit_reason','sortie')+'.',signature=tx['signature'])
            else:
                self.ledger.save(mint,'manual',data);self.notify(mint,'INTERVENTION_REQUISE','Quantite executee inattendue ; nouveaux achats bloques.')
        elif not status:
            height=await self.rpc('getBlockHeight',[{'commitment':'confirmed'}])
            if height>tx['height']:
                # One final status read closes the status/blockheight race before any retry.
                check=(await self.rpc('getSignatureStatuses',[[tx['signature']],{'searchTransactionHistory':True}]))['value'][0]
                if check:return
                state='expired' if side=='buy' else 'open' if data['sell_attempts']<3 else 'manual'
                self.ledger.save(mint,state,data)
                self.notify(mint,'ORDRE_EXPIRE','Signature expiree sans confirmation ; aucun achat rejoue.')

    async def positions(self):
        while True:
            rows=self.ledger.active()
            for row in rows:
                mint=row['mint'];data=json.loads(row['data'])
                try:
                    if row['state'].endswith('_pending'):await self.reconcile(row)
                    elif row['state']=='open':
                        quantity=await self.holdings(mint)
                        if quantity!=data['quantity']:
                            self.ledger.save(mint,'manual',data);self.notify(mint,'INTERVENTION_REQUISE','Solde modifie hors bot ; nouveaux achats bloques.');continue
                        q=await self.quote(mint,SOL,quantity)
                        value=Decimal(q['outAmount'])/10**9*self.price()
                        entry=Decimal(data['entry_usd'])
                        reason=data.get('exit_reason') or ('SL' if value<=entry*Decimal('.20') else 'TP' if value>=entry*Decimal('1.75') else None)
                        if reason:
                            data['exit_reason']=reason
                            if data['sell_attempts']>=3:
                                self.ledger.save(mint,'manual',data);self.notify(mint,'INTERVENTION_REQUISE','Trois tentatives de vente atteintes.');continue
                            self.ledger.save(mint,'open',data)
                            p=await self.prepare(mint,'sell',quantity,q)
                            data['sell']=self.signed(p);data['sell_attempts']+=1
                            self.ledger.save(mint,'sell_pending',data)
                            await self.send(mint,data,'sell')
                except asyncio.CancelledError:raise
                except Exception:
                    # Do not silently advertise protection when quotes/exits are unavailable.
                    self.notify(mint,'SORTIE_INDISPONIBLE','Cotation, RPC ou preparation indisponible ; position conservee, nouvelle verification prevue.')
            await asyncio.sleep(2)

    async def run(self):
        tasks=[asyncio.create_task(fn()) for fn in (self.fees,self.buy_loop,self.positions)]
        try:await asyncio.gather(*tasks)
        finally:
            for task in tasks:task.cancel()
            if self.preparing:self.preparing.cancel();tasks.append(self.preparing)
            await asyncio.gather(*tasks,return_exceptions=True)


class LiveObserver(PaperObserver):
    def __init__(self,ws_url,journal_path,rpc_url):
        super().__init__(ws_url,journal_path)
        self.manage_prices=False;self.is_live=True
        self.executor=Executor(rpc_url,journal_path.parent/'live_trades.sqlite3',self.prices)

    def emit(self,event):
        event=dict(event,session_id=self.session)
        if event.get('code') not in ('SUIVI','ATTENTE_VOLUME'):self.results[event['mint']]=event['code']
        self.write_record(event)  # The condition report remains explicitly a simulation of conditions.
        self.executor.signal(event)
        message=event['message'] if event['code']!='ACHAT_SIMULE' else 'Conditions validees ; verification de l ordre reel de 1 USD.'
        self.executor.notify(event['mint'],'CONDITIONS_VALIDEES' if event['code']=='ACHAT_SIMULE' else event['code'],message)

    async def run(self):
        print('ESSAI REEL : 1 USD/achat, budget cumule 15 USD ; SL -80 %, TP +75 %, glissement 20 %.',flush=True)
        print('Une position a la fois. Arreter le processus suspend les sorties automatiques.',flush=True)
        tasks=[asyncio.create_task(super().run()),asyncio.create_task(self.executor.run()),
               asyncio.create_task(self.prices.run()),asyncio.create_task(self.prices.run_doge())]
        try:await asyncio.gather(*tasks)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.executor.ledger.db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup',action='store_true')
    parser.add_argument('--setup-if-missing',action='store_true',help='Conserver les cles locales deja configurees')
    parser.add_argument('--report',action='store_true')
    parser.add_argument('--check',action='store_true',help='Verifier les acces en lecture seule, sans signature ou ordre')
    parser.add_argument('--update-jupiter',action='store_true',help='Remplacer localement la cle API Jupiter seule, sans toucher au wallet')
    args=parser.parse_args()
    try:
        if args.update_jupiter:update_jupiter()
        elif args.setup or args.setup_if_missing:setup(reuse=args.setup_if_missing)
        elif args.check:
            from wallet_alerts_ws import settings
            _,wallets,http,_=settings(ROOT/'wallet_alerts_config.json')
            if BBIG not in wallets.values():raise TradeError('BBIG BOSS absent des wallets suivis.')
            load_signer()  # Verify identity only; never sign in this command.
            transport=Transport(http,secure_read(PRIVATE/'jupiter_api_key.txt'))
            print('CONTROLE 1/3 : RPC/getBalance...',flush=True)
            balance=transport.rpc('getBalance',[OWNER,{'commitment':'confirmed'}])['value']
            print('RPC : OK. Solde SOL : '+str(Decimal(balance)/10**9),flush=True)
            print('CONTROLE 2/3 : Jupiter/cotation (client curl_cffi)...',flush=True)
            try:q=transport.jup(quote_path(SOL,USDC,1000000))
            except TradeError as exc:
                if exc.status==400:
                    print(str(exc),flush=True);transport.compare_quotes()
                    raise TradeError('Controle arrete apres comparaison ; aucun ordre envoye.') from None
                raise
            quote_check(q,SOL,USDC,1000000)
            print('Jupiter/cotation : OK.',flush=True)
            print('CONTROLE 3/3 : Jupiter/construction non signee...',flush=True)
            payload=transport.jup('/swap/v1/swap-instructions',dict(quoteResponse=q,userPublicKey=OWNER,wrapAndUnwrapSol=True,
                                  useSharedAccounts=False,asLegacyTransaction=True,dynamicComputeUnitLimit=False))
            checked_instructions(payload,q,1)
            print('Wallet correct, RPC, cotation et instructions Jupiter : OK. Solde SOL :',Decimal(balance)/10**9)
            print('Aucune signature, aucun ordre envoye. Routes des futurs tokens non garanties.')
        elif args.report:
            path=ROOT/'data/wallet_alerts_ws/live_trades.sqlite3'
            if not path.is_file():print('Aucun essai reel enregistre.');return
            db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            print('Budget reserve cumule (non recycle) :',sum((Decimal(r[0]) for r in db.execute('SELECT reserved FROM trades')),Decimal(0)),'/ 15 USD')
            for mint,state,data in db.execute('SELECT mint,state,data FROM trades'):
                value=json.loads(data);buy=value.get('buy',{})
                print(mint,state,'buy:',buy.get('signature','-'),'sell:',value.get('sell',{}).get('signature','-'))
                metrics={'detection_decision_s':value.get('decision_elapsed_s'),'preparation_ms':value.get('prepare_ms')}
                if buy.get('send_at') and value.get('decision_wall'):metrics['decision_envoi_HTTP_ms']=round((buy['send_at']-value['decision_wall'])*1000)
                if buy.get('send_at') and value.get('confirmed_at'):metrics['envoi_confirmation_observee_ms']=round((value['confirmed_at']-buy['send_at'])*1000)
                print('Mesures locales :',json.dumps(metrics))
            db.close()
        else:parser.print_help()
    except Exception as exc:raise SystemExit(str(exc) if isinstance(exc,TradeError) else 'Configuration impossible ; verifier la cle locale, les permissions et les dependances.') from None


if __name__=='__main__':main()
