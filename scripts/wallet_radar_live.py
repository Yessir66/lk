"""Explicit, bounded Wallet Radar live experiment. Never used by simulation mode.

All signing is local. Quotes and instructions come from the fixed Jupiter host;
instructions, signers, fees, simulation and the durable budget are checked before send.
"""
import argparse
import asyncio
import base64
from contextlib import contextmanager
from contextvars import ContextVar
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
EXECUTION_VERSION='2026.11.18.3'
OWNER='E23vfW9oA9CnANP5wzimpo3DM3WQtMuxzUP4oxFp97Jq'
PRIVATE=ROOT/'private_wallet_radar'
JUP='JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'
TOKEN='TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN22='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ATA='ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
SYSTEM='11111111111111111111111111111111'
COMPUTE='ComputeBudget111111111111111111111111111111'
ALT_PROGRAM='AddressLookupTab1e1111111111111111111111111'
SLIPPAGE=2000
# Keep quote/simulation freshness; the separate 5% reception filter was removed
# at the user's request. The transaction still enforces SLIPPAGE (20%).
QUOTE_MAX_AGE=2
MAX_HOLD_SECONDS=60
MANUAL_CHECK_INTERVAL=15
BUDGET=Decimal('45')
BUY=Decimal('1')
FEE=Decimal('.10')
RENT=Decimal('.50')
# Principal + buy fee/rent + three exit attempts' fees + exit rent.
# Charge the entire envelope permanently, even after a failed transaction/positive exit.
ENVELOPE=BUY+FEE+RENT+3*FEE+RENT
PREPARE_PROFILE=ContextVar('radar_prepare_profile',default=None)


async def measured_phase(profile,name,operation):
    started=time.monotonic()
    try:return await operation
    except Exception:
        profile['failed_phase']=name;raise
    finally:profile['phases_ms'][name]=round((time.monotonic()-started)*1000,3)


def record_transport(name,capture):
    profile=PREPARE_PROFILE.get()
    if profile is not None:
        profile['transport'].setdefault(name,[]).append({k:v for k,v in capture.items() if k!='request_at'})
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
        swapMode='ExactIn',onlyDirectRoutes='false',restrictIntermediateTokens='true',asLegacyTransaction='false',instructionVersion='V1'))
    return '/swap/v1/quote?'+query


def route_mints(q):
    """Bounded connected acyclic flow from the quoted source to its destination."""
    from solders.pubkey import Pubkey
    plan=q.get('routePlan');source=q.get('inputMint');dest=q.get('outputMint')
    if not isinstance(plan,list) or not 1<=len(plan)<=8 or source==dest:raise TradeError('Plan de route non pris en charge.')
    graph={};nodes=set()
    try:
        for step in plan:
            info=step['swapInfo'];a=info['inputMint'];b=info['outputMint']
            Pubkey.from_string(a);Pubkey.from_string(b)
            if a==b or a==dest or b==source:raise ValueError()
            graph.setdefault(a,set()).add(b);nodes.update((a,b))
    except Exception:raise TradeError('Mints du plan de route invalides.') from None
    visiting=set();visited=set()
    def visit(mint):
        if mint in visiting:raise TradeError('Route cyclique refusee.')
        if mint in visited:return
        visiting.add(mint)
        if mint!=dest and not graph.get(mint):raise TradeError('Branche de route sans destination finale.')
        for target in graph.get(mint,()):visit(target)
        visiting.remove(mint);visited.add(mint)
    visit(source)
    if visited!=nodes or dest not in visited:raise TradeError('Route deconnectee de la source ou destination.')
    return nodes


def checked_lookups(keys,result):
    from solders.address_lookup_table_account import AddressLookupTable,AddressLookupTableAccount
    from solders.pubkey import Pubkey
    values=result.get('value');slot=result.get('context',{}).get('slot')
    if not isinstance(values,list) or len(values)!=len(keys) or not isinstance(slot,int) or isinstance(slot,bool):
        raise TradeError('Lecture des tables d adresses incomplete.')
    tables=[]
    for key,value in zip(keys,values):
        if not isinstance(value,dict) or value.get('owner')!=ALT_PROGRAM or value.get('executable') is not False:
            raise TradeError('Proprietaire de table d adresses invalide.')
        try:
            data=value['data']
            if data[1]!='base64':raise ValueError()
            raw=base64.b64decode(data[0],validate=True)
            if not 56<=len(raw)<=56+256*32 or (len(raw)-56)%32:raise ValueError()
            table=AddressLookupTable.deserialize(raw)
            if table.meta.deactivation_slot!=2**64-1 or table.meta.last_extended_slot>=slot:raise ValueError()
            tables.append(AddressLookupTableAccount(Pubkey.from_string(key),list(table.addresses)))
        except Exception:raise TradeError('Table d adresses invalide, desactivee ou trop recente.') from None
    return tables


class TradeError(Exception):
    """Messages are fixed and contain no keys, URLs or provider bodies."""
    def __init__(self,message,status=None):
        super().__init__(message);self.status=status


def key_format_problem(api):
    if not api or any(c.isspace() or ord(c)<33 or ord(c)>126 for c in api):return 'caracteres non ASCII, espaces ou caracteres de controle'
    if api.startswith(('http://','https://')):return 'URL copiee au lieu de la cle seule'
    if api.startswith(('"',"'",'{','[')):return 'guillemets ou objet copie au lieu de la cle seule'
    return None


def simulation_error(err):
    suffix=''
    if isinstance(err,dict):
        instruction=err.get('InstructionError')
        if isinstance(instruction,list) and len(instruction)==2 and isinstance(instruction[0],int) and not isinstance(instruction[0],bool):
            index,detail=instruction
            if isinstance(detail,dict) and isinstance(detail.get('Custom'),int) and not isinstance(detail['Custom'],bool):
                suffix=' ; instruction '+str(index)+' ; code programme '+str(detail['Custom'])
            elif isinstance(detail,str) and detail in ('InsufficientFunds','InvalidAccountData','InvalidArgument','ComputationalBudgetExceeded','AccountNotInitialized'):
                suffix=' ; instruction '+str(index)+' ; '+detail
    elif isinstance(err,str) and err in ('BlockhashNotFound','InsufficientFundsForFee','AccountNotFound','AlreadyProcessed'):
        suffix=' ; '+err
    return TradeError('Simulation de transaction refusee'+suffix+'.')


def rpc_send_rejection(error):
    """Persist machine codes/static labels only; never message/data/log bodies."""
    info={'outcome':'rpc_rejected','preflight_commitment':'confirmed'}
    if not isinstance(error,dict):return {'outcome':'unknown','preflight_commitment':'confirmed'}
    code=error.get('code')
    if type(code) is int:info['rpc_code']=code
    if code==-32002:
        info['reason']='preflight_simulation_refused'
        data=error.get('data')
        info['simulation']=str(simulation_error(data.get('err') if isinstance(data,dict) else None))
    elif code==-32003:info['reason']='signature_verification_failed'
    elif code==-32005:info['reason']='rpc_node_unhealthy'
    else:info['reason']='rpc_request_refused'
    return info


def send_result_text(info):
    outcome=info.get('outcome')
    if outcome=='accepted':return 'Signature acceptee par le RPC ; ce retour ne prouve pas l execution.'
    if outcome=='rpc_rejected':
        code=info.get('rpc_code');text='Refus RPC'+(' '+str(code) if type(code) is int else '')
        if info.get('reason')=='preflight_simulation_refused':text+=' ; '+info.get('simulation','simulation refusee')
        elif info.get('reason')=='signature_verification_failed':text+=' ; verification de signature refusee'
        elif info.get('reason')=='rpc_node_unhealthy':text+=' ; noeud RPC indisponible'
        return text+'.'
    if outcome=='http_error':return 'Envoi HTTP '+str(info['http_status'])+' ; resultat de transmission inconnu.'
    if info.get('reason') in ('delai depasse','certificat/TLS','resolution DNS','connexion ou format JSON indisponible'):
        return 'Envoi : '+info['reason']+' ; resultat de transmission inconnu.'
    return 'Resultat de transmission inconnu ; aucune confirmation deduite.'


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
        self.rpc_interval=1/3
        self.rpc_condition=threading.Condition();self.rpc_waiters=[];self.rpc_sequence=0;self.rpc_busy=False
        # Use the installed client already used by the wallet monitor, without
        # changing hosts, credentials, TLS verification or authentication rules.
        from curl_cffi import requests
        self.jupiter_http=requests.Session(impersonate='chrome')

    @contextmanager
    def slot(self,jupiter,priority,deadline,capture,label):
        queue_ms=0.0;quota_ms=0.0
        if jupiter:
            start=time.monotonic()
            with self.locks['jup']:
                capture['queue_wait_ms']=(time.monotonic()-start)*1000
                if time.monotonic()<self.pause_jup:raise TradeError(label+' : pause apres limitation, attendre 60 s.',status=429)
                start=time.monotonic();time.sleep(max(0,self.next_jup-start))
                capture['quota_wait_ms']=(time.monotonic()-start)*1000
                self.next_jup=time.monotonic()+1
                yield
            return
        condition=self.rpc_condition
        with condition:
            self.rpc_sequence+=1;ticket=(priority,self.rpc_sequence)
            self.rpc_waiters.append(ticket);condition.notify_all()
            try:
                while True:
                    now=time.monotonic()
                    if deadline is not None and now>deadline:raise TradeError('Limite de trois secondes depassee avant requete.')
                    if now<self.pause_rpc:raise TradeError(label+' : pause apres limitation, attendre 60 s.',status=429)
                    if self.rpc_busy or min(self.rpc_waiters)!=ticket:
                        started=time.monotonic();condition.wait(timeout=max(.001,deadline-now) if deadline is not None else None)
                        queue_ms+=(time.monotonic()-started)*1000;continue
                    delay=self.next_rpc-now
                    if delay>0:
                        started=time.monotonic();condition.wait(timeout=min(delay,max(.001,deadline-now)) if deadline is not None else delay)
                        quota_ms+=(time.monotonic()-started)*1000;continue
                    self.rpc_waiters.remove(ticket);self.rpc_busy=True;self.next_rpc=time.monotonic()+self.rpc_interval
                    break
            except BaseException:
                self.rpc_waiters.remove(ticket);condition.notify_all();raise
            finally:
                capture['queue_wait_ms']=round(queue_ms,3);capture['quota_wait_ms']=round(quota_ms,3)
        try:yield
        finally:
            with condition:self.rpc_busy=False;condition.notify_all()

    def request(self,url,body=None,jupiter=False,deadline=None,capture=None,label='Fournisseur',priority=1):
        # Fixed host for Jupiter. Keys only in the request header, never stdout.
        name='jup' if jupiter else 'rpc'
        capture=capture if capture is not None else {}
        with self.slot(jupiter,priority,deadline,capture,label):
            if deadline is not None and time.monotonic()>deadline:
                raise TradeError('Limite de trois secondes depassee avant requete.')
            headers={'Accept':'application/json'}
            if body is not None:headers['Content-Type']='application/json'
            if jupiter:headers['x-api-key']=self.key
            req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            http_started=time.monotonic()
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
                if not jupiter and isinstance(body,dict) and body.get('method')=='sendTransaction':
                    capture['send_result']=dict(outcome='http_error',http_status=status,preflight_commitment='confirmed') if status else dict(outcome='unknown',reason=detail,preflight_commitment='confirmed')
                raise TradeError(label+' : '+detail+'. URL, cles et reponse privee masquees.',status=status) from None
            finally:
                if 'request_at' in capture:capture['http_response_ms']=round((time.monotonic()-http_started)*1000,3)
            if not isinstance(value,dict) or value.get('error') is not None:
                error=value.get('error') if isinstance(value,dict) else None
                code=error.get('code') if isinstance(error,dict) else None
                detail=' ; code RPC '+str(code) if isinstance(code,int) and not isinstance(code,bool) else ''
                if jupiter:
                    safe=jupiter_reason(value)
                    if safe:detail+=' ; '+safe
                elif isinstance(body,dict) and body.get('method')=='sendTransaction':
                    capture['send_result']=rpc_send_rejection(error)
                if code==429:setattr(self,'pause_'+name,time.monotonic()+60)
                raise TradeError(label+' : reponse refusee ou invalide'+detail+'.')
            return value

    def rpc(self,method,params,capture=None):
        known={'getBalance','getLatestBlockhash','getFeeForMessage','getMultipleAccounts','getTokenAccountsByOwner',
               'getRecentPrioritizationFees','simulateTransaction','getSignatureStatuses','getTransaction','getBlockHeight','getSignaturesForAddress'}
        priority=2 if method in ('getRecentPrioritizationFees','getLatestBlockhash','getSignatureStatuses','getTransaction','getBlockHeight','getSignaturesForAddress') else 1
        value=self.request(self.url,{'jsonrpc':'2.0','id':1,'method':method,'params':params},capture=capture,
                           label='RPC/'+method if method in known else 'RPC',priority=priority)
        if 'result' not in value:raise TradeError('Resultat RPC absent.')
        return value['result']

    def jup(self,path,body=None,capture=None):
        label='Jupiter/cotation' if path.startswith('/swap/v1/quote?') else 'Jupiter/construction' if path=='/swap/v1/swap-instructions' else 'Jupiter'
        return self.request('https://api.jup.ag'+path,body,jupiter=True,label=label,capture=capture)

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
            raw,{'encoding':'base64','skipPreflight':False,'preflightCommitment':'confirmed','maxRetries':2}]},deadline=deadline,capture=capture,label='RPC/envoi',priority=0)['result']


def lamports(usd,price):return int((Decimal(usd)*10**9/price).to_integral_value(rounding=ROUND_CEILING))


def exit_deadline(data):
    """Absolute durable deadline; restarting never grants another minute."""
    value=data.get('exit_deadline')
    if type(value) in (int,float) and math.isfinite(value) and value>0:return value
    confirmed=data.get('confirmed_at')
    if type(confirmed) in (int,float) and math.isfinite(confirmed) and confirmed>0:return confirmed+MAX_HOLD_SECONDS
    return None


def associated(mint,program=TOKEN):
    from solders.pubkey import Pubkey
    return str(Pubkey.find_program_address([bytes(Pubkey.from_string(OWNER)),bytes(Pubkey.from_string(program)),
                                         bytes(Pubkey.from_string(mint))],Pubkey.from_string(ATA))[0])


def token_amount(account,mint):
    """Validate the SPL/Token-2022 base account layout returned by simulation."""
    from solders.pubkey import Pubkey
    if account is None:return 0
    try:
        if account.get('owner') not in (TOKEN,TOKEN22) or account.get('executable'):raise ValueError()
        data=account['data']
        if not isinstance(data,list) or len(data)!=2 or data[1]!='base64':raise ValueError()
        raw=base64.b64decode(data[0],validate=True)
        if (len(raw)<165 or raw[:32]!=bytes(Pubkey.from_string(mint))
                or raw[32:64]!=bytes(Pubkey.from_string(OWNER)) or raw[108]!=1):raise ValueError()
        return struct.unpack_from('<Q',raw,64)[0]
    except Exception:raise TradeError('Compte token simule invalide : achat ignore.') from None


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
    route_mints(q)


def checked_instructions(payload,q,priority):
    """Build our own message; never blindly sign a provider transaction.

    Only v1 exact-in route/shared_accounts_route, own ATAs, SOL wrapping and
    compute-budget instructions. Unsupported future routes fail closed.
    """
    from solders.instruction import Instruction, AccountMeta
    from solders.pubkey import Pubkey
    from solders.compute_budget import set_compute_unit_limit,set_compute_unit_price
    source,dest=q['inputMint'],q['outputMint'];amount=int(q['inAmount'])
    permitted_mints=route_mints(q)
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
                or keys[3] not in permitted_mints or keys[4]!=SYSTEM or keys[5] not in (TOKEN,TOKEN22)
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
        self.blockhash_cache=None;self.blockhash_lock=asyncio.Lock()
        self.manual_check_after={}

    async def rpc(self,method,params):
        capture={}
        try:return await asyncio.to_thread(self.transport.rpc,method,params,capture)
        finally:record_transport('RPC/'+method,capture)

    async def jup(self,path,body=None):
        capture={}
        try:return await asyncio.to_thread(self.transport.jup,path,body,capture)
        finally:record_transport('Jupiter/cotation' if path.startswith('/swap/v1/quote?') else 'Jupiter/construction',capture)

    async def blockhash(self,refresh=False):
        if not refresh and self.blockhash_cache and time.monotonic()-self.blockhash_cache['_observed_mono']<=10:
            profile=PREPARE_PROFILE.get()
            if profile is not None:profile['blockhash_cache_hit']=True
            return self.blockhash_cache
        async with self.blockhash_lock:
            if not refresh and self.blockhash_cache and time.monotonic()-self.blockhash_cache['_observed_mono']<=10:
                profile=PREPARE_PROFILE.get()
                if profile is not None:profile['blockhash_cache_hit']=True
                return self.blockhash_cache
            result=await self.rpc('getLatestBlockhash',[{'commitment':'confirmed'}])
            try:
                from solders.hash import Hash
                Hash.from_string(result['value']['blockhash'])
                height=result['value']['lastValidBlockHeight']
                if not isinstance(height,int) or isinstance(height,bool) or height<=0:raise ValueError()
            except Exception:raise TradeError('Blockhash ou hauteur de validite invalide.') from None
            self.blockhash_cache=dict(result,_observed_mono=time.monotonic())
            return self.blockhash_cache

    async def refresh_blockhash(self):
        while True:
            try:await self.blockhash(refresh=True)
            except Exception:pass
            await asyncio.sleep(5)

    def price(self):
        value=self.prices.usd
        if value is None or not 0<=time.time()-self.prices.published<=90:raise TradeError('Cours SOL/USD absent ou perime.')
        return value

    def notify(self,mint,code,message,**details):
        if code=='SORTIE_INDISPONIBLE':
            if time.monotonic()-self.last_notice.get(mint,0)<30:return
            self.last_notice[mint]=time.monotonic()
        event=dict(v=1,type='live_decision',mode='LIVE',mint=mint,code=code,message=message,at=time.time(),execution_version=EXECUTION_VERSION,**details)
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

    async def lookups(self,payload):
        from solders.pubkey import Pubkey
        keys=payload.get('addressLookupTableAddresses') or []
        if not isinstance(keys,list) or not all(isinstance(key,str) for key in keys) or len(keys)>4 or len(keys)!=len(set(keys)):
            raise TradeError('Liste des tables d adresses non prise en charge.')
        if not keys:return []
        try:
            for key in keys:Pubkey.from_string(key)
        except Exception:raise TradeError('Adresse de table invalide.') from None
        result=await self.rpc('getMultipleAccounts',[keys,{'encoding':'base64','commitment':'confirmed'}])
        return checked_lookups(keys,result)

    async def prepare(self,mint,side,quantity=None,q=None):
        profile={'phases_ms':{},'transport':{},'blockhash_cache_hit':False}
        self.last_preparation_profile=(mint,profile)
        token=PREPARE_PROFILE.set(profile)
        try:
            prepared=await self.prepare_transaction(mint,side,quantity,q,profile)
            prepared['preparation_profile']=profile
            return prepared
        finally:PREPARE_PROFILE.reset(token)

    async def prepare_transaction(self,mint,side,quantity,q,profile):
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
        # Start construction as soon as the quote is available, without waiting
        # for unrelated RPC reads. Neither this task nor simulation can sign.
        async def quote_and_build():
            quoted=await measured_phase(profile,'cotation',self.quote(source,dest,amount) if q is None else asyncio.sleep(0,result=q))
            quote_at=time.monotonic();quote_check(quoted,source,dest,amount)
            # Quote mints bound every permitted ATA. Read wallet balance plus
            # both token-program variants in ONE RPC during Jupiter's build/quota
            # wait, rather than reading balance then accounts on the critical path.
            snapshot_addresses=[OWNER]+[associated(coin,program) for coin in sorted(route_mints(quoted)) for program in (TOKEN,TOKEN22)]
            payload,snapshot=await asyncio.gather(
                measured_phase(profile,'construction_Jupiter',self.jup('/swap/v1/swap-instructions',dict(quoteResponse=quoted,userPublicKey=OWNER,
                    wrapAndUnwrapSol=True,useSharedAccounts=False,asLegacyTransaction=False,dynamicComputeUnitLimit=False))),
                measured_phase(profile,'comptes_anticipes',self.rpc('getMultipleAccounts',[
                    snapshot_addresses,{'encoding':'base64','commitment':'confirmed'}])) if side=='buy' else asyncio.sleep(0,result=None))
            return quoted,payload,quote_at,snapshot_addresses,snapshot
        owned,built,block,balance_result=await asyncio.gather(
            measured_phase(profile,'position',self.holdings(mint) if side=='buy' else asyncio.sleep(0,result=0)),
            quote_and_build(),
            measured_phase(profile,'blockhash',self.blockhash()),
            measured_phase(profile,'solde_SOL',self.rpc('getBalance',[OWNER,{'commitment':'confirmed'}]) if side!='buy' else asyncio.sleep(0,result=None)))
        if side=='buy' and owned!=0:raise TradeError('Position preexistante sur ce token : achat ignore.')
        q,payload,quote_at,snapshot_addresses,snapshot=built
        instructions=checked_instructions(payload,q,self.priority)
        tables=await measured_phase(profile,'tables_adresses',self.lookups(payload))
        message=MessageV0.try_compile(Pubkey.from_string(OWNER),instructions,tables,Hash.from_string(block['value']['blockhash']))
        unsigned=VersionedTransaction.populate(message,[Signature.default()])
        if len(bytes(unsigned))>1232:raise TradeError('Route trop volumineuse apres compression des adresses.')
        fee_params=[base64.b64encode(to_bytes_versioned(message)).decode(),{'commitment':'confirmed'}]
        created=list(dict.fromkeys(a['pubkey'] for row in payload.get('setupInstructions',[]) if row['programId']==ATA for a in row['accounts'][1:2]))
        addresses=[OWNER]+created;entry_guard=None
        if side=='buy':
            route=payload['swapInstruction'];keys=[a['pubkey'] for a in route['accounts']]
            target=keys[3] if base64.b64decode(route['data'])[:8]==hashlib.sha256(b'global:route').digest()[:8] else keys[6]
            addresses=list(dict.fromkeys(addresses+[target]))
            values=snapshot['value']
            if len(values)!=len(snapshot_addresses) or any(key not in snapshot_addresses for key in addresses):
                raise TradeError('Comptes anticipes incomplets ou hors route.')
            before=[values[snapshot_addresses.index(key)] for key in addresses]
            if not before[0] or before[0].get('owner')!=SYSTEM:
                raise TradeError('Solde wallet ou comptes avant simulation indisponibles.')
            balance=int(before[0]['lamports'])
            if balance<lamports(ENVELOPE,price):raise TradeError('SOL insuffisant pour achat, depots et reserve de sortie.')
            previous=[before[addresses.index(key)] for key in created]
            token_before=token_amount(before[addresses.index(target)],mint)
            if token_before!=0:raise TradeError('Position apparue pendant la preparation : achat ignore.')
            profile['anticipated_accounts']=True
            fee=(await measured_phase(profile,'verification_frais',self.rpc('getFeeForMessage',fee_params)))['value']
        else:
            balance=balance_result['value']
            fee=(await measured_phase(profile,'verification_frais',self.rpc('getFeeForMessage',fee_params)))['value']
            previous=(await measured_phase(profile,'comptes_avant_simulation',self.rpc('getMultipleAccounts',[created,{'encoding':'base64','commitment':'confirmed'}])))['value'] if created else []
        if fee is None or fee>fee_lamports:raise TradeError('Frais reseau au-dessus du plafond.')
        simulation_options={
            'encoding':'base64','sigVerify':False,'commitment':'confirmed',
            'accounts':{'encoding':'base64','addresses':addresses}}
        snapshot_slot=((snapshot or {}).get('context') or {}).get('slot')
        if side=='buy' and type(snapshot_slot) is int and snapshot_slot>=0:simulation_options['minContextSlot']=snapshot_slot
        result=await measured_phase(profile,'simulation',self.rpc('simulateTransaction',[base64.b64encode(bytes(unsigned)).decode(),simulation_options]))
        sim=result['value']
        if sim.get('err') is not None:raise simulation_error(sim['err'])
        post=sim.get('accounts') or []
        if not post or post[0] is None or post[0].get('owner')!=SYSTEM:raise TradeError('Solde simule du wallet indisponible.')
        if len(post)!=len(addresses):raise TradeError('Comptes simules incomplets.')
        simulated_created=[post[addresses.index(key)] for key in created]
        rent=sum(max(0,(a or {}).get('lamports',0)-(b or {}).get('lamports',0)) for a,b in zip(simulated_created,previous))
        if rent>lamports(RENT,price):raise TradeError('Depot de comptes au-dessus du plafond reserve.')
        debit=balance-int(post[0]['lamports'])
        cap=lamports((BUY if side=='buy' else Decimal(0))+RENT+FEE,price)
        if debit>cap:raise TradeError('Depense SOL simulee au-dessus du plafond achat/frais/depots.')
        if side=='buy':
            received=token_amount(post[addresses.index(target)],mint)-token_before
            if received<=0:raise TradeError('Aucun token recu en simulation ; achat ignore avant signature.')
            if time.monotonic()-quote_at>QUOTE_MAX_AGE:raise TradeError('Cotation de plus de 2 s : achat ignore avant signature.')
            entry_guard={'quoted_tokens':int(q['outAmount']),'simulated_tokens':received,
                         'quote_at':quote_at,'checked_at':time.monotonic()}
        return dict(message=base64.b64encode(to_bytes_versioned(message)).decode(),height=block['value']['lastValidBlockHeight'],
                    amount=amount,price=str(price),fee_lamports=fee,rent_lamports=rent,
                    prepared=time.monotonic(),prepare_ms=round((time.monotonic()-start)*1000),side=side,
                    blockhash_at=block['_observed_mono'],entry_guard=entry_guard)

    def signed(self,p):
        from solders.message import from_bytes_versioned
        from solders.transaction import VersionedTransaction
        price=self.price()
        if p['side']=='buy':
            guard=p.get('entry_guard')
            if (not guard or not 0<=time.monotonic()-guard['quote_at']<=QUOTE_MAX_AGE
                or not 0<=time.monotonic()-guard['checked_at']<=.5):
                raise TradeError('Cotation ou simulation absente ou perimee : achat ignore avant signature.')
        if (time.monotonic()-p['prepared']>2 or time.monotonic()-p['blockhash_at']>15 or time.monotonic()-self.priority_at>30
            or p['fee_lamports']>lamports(FEE,price) or p['rent_lamports']>lamports(RENT,price)
            or (p['side']=='buy' and p['amount']>lamports(BUY,price))):
            raise TradeError('Preparation perimee ou plafond USD depasse avant signature.')
        tx=VersionedTransaction(from_bytes_versioned(base64.b64decode(p['message'])),[self.key])
        return dict(raw=base64.b64encode(bytes(tx)).decode(),signature=str(tx.signatures[0]),height=p['height'])

    async def buy_loop(self):
        while True:
            mint,deadline,elapsed=await self.jobs.get()
            decision_mono=deadline-max(0,3-elapsed)
            latency={'file_decision_ms':round(max(0,time.monotonic()-decision_mono)*1000,3)}
            try:
                if self.ledger.active() or self.ledger.get(mint) or self.ledger.used()+ENVELOPE>BUDGET:
                    raise TradeError('Position active, token deja traite ou budget insuffisant.')
                if self.prepared_mint!=mint or self.preparing is None:raise TradeError('Preparation non disponible pour ce signal.')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TradeError('Limite de trois secondes depassee.')
                waiting=time.monotonic()
                try:p=await asyncio.wait_for(asyncio.shield(self.preparing),remaining)
                finally:latency['preparation_restante_ms']=round((time.monotonic()-waiting)*1000,3)
                if time.monotonic()>deadline or time.monotonic()-p['prepared']>2:raise TradeError('Preparation trop ancienne ou limite de trois secondes depassee.')
                # Recheck the USD reference, priority freshness and fee cap before signing.
                checking=time.monotonic();current_price=self.price()
                if time.monotonic()-self.priority_at>30:raise TradeError('Priorite perimee.')
                tx=self.signed(p)
                latency['verifications_signature_ms']=round((time.monotonic()-checking)*1000,3)
                data=dict(buy=tx,buy_amount=p['amount'],entry_usd=str(Decimal(p['amount'])/10**9*current_price),
                          price=str(current_price),decision_elapsed_s=elapsed,prepare_ms=p['prepare_ms'],sell_attempts=0,
                          decision_wall=time.time()-(3-elapsed-(deadline-time.monotonic())),signed_at=time.time(),
                          latency_breakdown=latency,preparation_profile=p.get('preparation_profile'),execution_version=EXECUTION_VERSION)
                if p.get('entry_guard'):
                    guard=p['entry_guard'];data['entry_quote']={key:guard[key] for key in ('quoted_tokens','simulated_tokens')}
                    data['entry_quote']['quote_age_at_sign_ms']=round((time.monotonic()-guard['quote_at'])*1000,3)
                if time.monotonic()>deadline:raise TradeError('Limite de trois secondes depassee avant envoi.')
                storing=time.monotonic()
                self.ledger.reserve(mint,data)  # Durable BEFORE any possible network submission.
                latency['journal_avant_envoi_ms']=round((time.monotonic()-storing)*1000,3)
                # All sends below use the saved signature; errors never create a second buy.
                await self.send(mint,data,'buy',deadline)
            except asyncio.CancelledError:raise
            except Exception as exc:
                reason=str(exc) if isinstance(exc,TradeError) else 'Preparation non terminee avant la limite de trois secondes.' if isinstance(exc,TimeoutError) else 'Preparation impossible ; aucun nouvel ordre.'
                last=getattr(self,'last_preparation_profile',None)
                self.notify(mint,'ACHAT_IGNORE',reason,latency_breakdown=latency,
                            preparation_profile=last[1] if last and last[0]==mint else None)

    async def send(self,mint,data,side,deadline=None):
        # The exact signature must already be durable. Do not repeat the same
        # synchronous write on the critical path just to add a queue timestamp.
        current=self.ledger.get(mint)
        if not current or current['state']!=side+'_pending' or json.loads(current['data']).get(side,{}).get('signature')!=data[side]['signature']:
            raise TradeError('Signature non enregistree durablement ; envoi refuse.')
        data[side]['queued_at']=time.time()
        capture={};result=None;diagnostic=None
        try:
            try:result=await asyncio.to_thread(self.transport.send,data[side]['raw'],deadline,capture)
            finally:
                current=self.ledger.get(mint);saved=json.loads(current['data'])
                saved[side]['queued_at']=data[side]['queued_at']
                if side=='buy':
                    breakdown=dict(data.get('latency_breakdown') or saved.get('latency_breakdown') or {})
                    for raw,name in [('queue_wait_ms','file_rpc_envoi_ms'),('quota_wait_ms','quota_rpc_envoi_ms'),
                                     ('http_response_ms','reponse_http_apres_envoi_ms')]:
                        if raw in capture:breakdown[name]=round(capture[raw],3)
                    saved['latency_breakdown']=breakdown
                if 'request_at' in capture:
                    data[side]['send_at']=capture['request_at']
                    saved[side]['send_at']=capture['request_at']
                diagnostic=(dict(outcome='accepted',preflight_commitment='confirmed') if result==data[side]['signature'] else
                            capture.get('send_result') or dict(outcome='unknown',preflight_commitment='confirmed'))
                saved[side]['rpc_send']=diagnostic
                self.ledger.save(mint,current['state'],saved)
            if result!=data[side]['signature']:raise TradeError('Signature renvoyee incoherente.')
            self.notify(mint,'ORDRE_ENVOYE',side+' : transaction envoyee, confirmation attendue.',signature=result,
                        prepare_ms=data.get('prepare_ms'),decision_to_send_ms=round((data[side]['send_at']-data['decision_wall'])*1000) if data[side].get('send_at') and side=='buy' else None)
        except Exception:
            self.notify(mint,'ORDRE_A_VERIFIER',send_result_text(diagnostic or {})+' Suivi de la meme signature, aucun nouvel ordre.',
                        signature=data[side]['signature'],rpc_send=diagnostic or {'outcome':'unknown'})

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
                data['exit_deadline']=data['confirmed_at']+MAX_HOLD_SECONDS
                self.ledger.save(mint,'open',data)
                self.notify(mint,'ACHAT_CONFIRME','Achat reel confirme ; SL -80 % / TP +75 % ; sortie totale prevue apres 60 s si encore ouverte.',signature=tx['signature'],
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

    async def reconcile_manual(self,row):
        if row['state']!='manual':return False
        mint=row['mint'];now=time.monotonic()
        if not hasattr(self,'manual_check_after'):self.manual_check_after={}
        if now<self.manual_check_after.get(mint,0):return False
        self.manual_check_after[mint]=now+MANUAL_CHECK_INTERVAL
        try:
            found=await verify_external_sale(row,self)
            close_external_sale(self.ledger.db,row,found,automatic=True)
        except asyncio.CancelledError:raise
        except Exception:
            # No proof yet (finality, unsupported/partial transfer, unavailable
            # provider). Keep the blocker and retry at the bounded cadence.
            return False
        finally:self.manual_check_after[mint]=time.monotonic()+MANUAL_CHECK_INTERVAL
        self.notify(mint,'VENTE_CONFIRMEE','Vente manuelle finalisee verifiee automatiquement ; suivi cloture.',
                    signature=found['signature'],external=True)
        return True

    async def positions(self):
        while True:
            rows=self.ledger.active()
            for row in rows:
                mint=row['mint'];data=json.loads(row['data'])
                stage='suivi de transaction'
                try:
                    if row['state'].endswith('_pending'):await self.reconcile(row)
                    elif row['state']=='manual':await self.reconcile_manual(row)
                    elif row['state']=='open':
                        deadline=exit_deadline(data)
                        if deadline is not None and time.time()>=deadline and not data.get('exit_reason'):
                            data['exit_deadline']=deadline;data['exit_reason']='TIMEOUT_60S'
                            # Persist the decision before any quote/RPC can fail.
                            self.ledger.save(mint,'open',data)
                        stage='lecture du solde token'
                        quantity=await self.holdings(mint)
                        if quantity!=data['quantity']:
                            self.ledger.save(mint,'manual',data)
                            if quantity==0:self.notify(mint,'VENTE_MANUELLE_EN_VERIFICATION','Solde nul ; verification automatique de la vente avant reprise des achats.')
                            else:self.notify(mint,'INTERVENTION_REQUISE','Solde modifie hors bot ; vente partielle ou mouvement a verifier.')
                            await self.reconcile_manual(self.ledger.get(mint));continue
                        stage='cotation de vente Jupiter';q=await self.quote(mint,SOL,quantity)
                        stage='cours SOL/USD'
                        value=Decimal(q['outAmount'])/10**9*self.price()
                        entry=Decimal(data['entry_usd'])
                        reason=data.get('exit_reason') or ('SL' if value<=entry*Decimal('.20') else 'TP' if value>=entry*Decimal('1.75') else
                            'TIMEOUT_60S' if deadline is not None and time.time()>=deadline else None)
                        if reason:
                            data['exit_reason']=reason
                            if data['sell_attempts']>=3:
                                self.ledger.save(mint,'manual',data);self.notify(mint,'INTERVENTION_REQUISE','Trois tentatives de vente atteintes.');continue
                            self.ledger.save(mint,'open',data)
                            stage='preparation de vente';p=await self.prepare(mint,'sell',quantity,q)
                            stage='verification et signature locale'
                            data['sell']=self.signed(p);data['sell_attempts']+=1
                            stage='journal de vente'
                            self.ledger.save(mint,'sell_pending',data)
                            await self.send(mint,data,'sell')
                except asyncio.CancelledError:raise
                except Exception as exc:
                    # Do not silently advertise protection when quotes/exits are unavailable.
                    cause=str(exc) if isinstance(exc,TradeError) else type(exc).__name__
                    self.notify(mint,'SORTIE_INDISPONIBLE','Etape '+stage+' : '+cause+' Vente non confirmee ; suivi maintenu.',stage=stage)
            await asyncio.sleep(2)

    async def run(self):
        tasks=[asyncio.create_task(fn()) for fn in (self.fees,self.refresh_blockhash,self.buy_loop,self.positions)]
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
        print('ESSAI REEL : 1 USD/achat, budget cumule 45 USD ; SL -80 %, TP +75 %, glissement 20 %.',flush=True)
        print('Sortie totale : SL, TP ou 60 s apres confirmation achat ; premier declenchement, delai reseau possible.',flush=True)
        print('Filtre de reception 5 % retire ; simulation obligatoire et glissement maximum 20 % conserves.',flush=True)
        print('Ventes manuelles : rapprochement automatique des ventes totales finalisees ; controle toutes les 15 s.',flush=True)
        print('Une position a la fois. Arreter le processus suspend les sorties automatiques.',flush=True)
        tasks=[asyncio.create_task(super().run()),asyncio.create_task(self.executor.run()),
               asyncio.create_task(self.prices.run()),asyncio.create_task(self.prices.run_doge())]
        try:await asyncio.gather(*tasks)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            self.executor.ledger.db.close()


class ExitReadOnlyTransport(Transport):
    """Diagnostic access cannot submit transactions, even by accidental RPC call."""
    READ_METHODS={'getTokenAccountsByOwner','getRecentPrioritizationFees','getLatestBlockhash','getFeeForMessage',
                  'getMultipleAccounts','getBalance','simulateTransaction','getSignatureStatuses','getBlockHeight',
                  'getTransaction','getSignaturesForAddress'}
    def __init__(self,*args):
        super().__init__(*args);self.rpc_interval=1

    def rpc(self,method,params,capture=None):
        if method not in self.READ_METHODS:raise TradeError('Diagnostic : RPC de mutation interdit.')
        return super().rpc(method,params,capture)

    def jup(self,path,body=None,capture=None):
        if not ((path.startswith('/swap/v1/quote?') and body is None) or (path=='/swap/v1/swap-instructions' and isinstance(body,dict))):
            raise TradeError('Diagnostic : seul devis ou construction non signee autorise.')
        return super().jup(path,body,capture)

    def send(self,*args,**kwargs):raise TradeError('Diagnostic : envoi interdit.')


async def diagnose_exit(path,http):
    """Read a snapshot; never instantiate a signer, Ledger, or live event loop."""
    path=Path(path)
    if not path.is_file():print('Aucun journal reel a examiner.');return
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:rows=[dict(row) for row in db.execute("SELECT mint,state,data FROM trades WHERE state IN ('open','manual','buy_pending','sell_pending') LIMIT 1")]
    finally:db.close()
    if not rows:print('Aucune position ouverte ou transaction en attente dans le journal.');return
    row=rows[0];data=json.loads(row['data']);mint=row['mint']
    from solders.pubkey import Pubkey
    Pubkey.from_string(mint)
    print('DIAGNOSTIC SORTIE — lecture seule ; aucun ordre, aucune signature, journal inchange.',flush=True)
    print('TOKEN : '+mint+' ; etat journal : '+row['state'],flush=True)
    probe=Executor.__new__(Executor)
    probe.transport=ExitReadOnlyTransport(http,secure_read(PRIVATE/'jupiter_api_key.txt'))
    from wallet_radar_paper import Prices
    probe.prices=Prices();probe.blockhash_cache=None;probe.blockhash_lock=asyncio.Lock()
    probe.priority=None;probe.priority_at=0
    stage='lecture position'
    try:
        if row['state'].endswith('_pending'):
            side='buy' if row['state']=='buy_pending' else 'sell';signature=data[side]['signature']
            print('Transaction en attente : '+signature,flush=True)
            result=(await probe.rpc('getSignatureStatuses',[[signature],{'searchTransactionHistory':True}]))['value'][0]
            status=result.get('confirmationStatus') if result else None
            print('Statut observe : '+(status if status in ('processed','confirmed','finalized') else 'non disponible')+
                  ' ; echec indique : '+str(bool(result and result.get('err') is not None)),flush=True)
            return
        quantity=await probe.holdings(mint)
        print('Quantite enregistree : '+str(data.get('quantity'))+' ; quantite actuelle : '+str(quantity),flush=True)
        if quantity!=data.get('quantity') or quantity<=0:
            print('Quantite differente ou nulle : verifier le solde et le journal, aucune mutation effectuee.');return
        stage='cotation de vente Jupiter';print('ETAPE : '+stage,flush=True)
        q=await probe.quote(mint,SOL,quantity)
        print('Cotation : OK ; segments : '+str(len(q['routePlan'])),flush=True)
        stage='cours SOL/USD';print('ETAPE : '+stage,flush=True)
        def price_data():
            with urllib.request.urlopen('https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd&include_last_updated_at=true',timeout=5) as response:return json.load(response)
        probe.prices.update(await asyncio.to_thread(price_data),time.time())
        value=Decimal(q['outAmount'])/10**9*probe.price();entry=Decimal(data['entry_usd'])
        if not entry.is_finite() or entry<=0:raise TradeError('Reference entree invalide.')
        deadline=exit_deadline(data)
        reason=data.get('exit_reason') or ('SL' if value<=entry*Decimal('.20') else 'TP' if value>=entry*Decimal('1.75') else
            'TIMEOUT_60S' if deadline is not None and time.time()>=deadline else None)
        print('Valeur de liquidation estimee USD : '+str(value.quantize(Decimal('.0001')))+
              ' ; entree USD : '+str(entry)+' ; seuil ou decision sortie : '+str(reason or 'aucun'),flush=True)
        if not reason:
            print('Evaluation possible actuellement ; aucun seuil observe et aucune sortie deja demandee. Aucun ordre envoye.');return
        stage='estimation priorite';print('ETAPE : '+stage,flush=True)
        fees=await probe.rpc('getRecentPrioritizationFees',[[]])
        values=sorted(int(r['prioritizationFee']) for r in fees if int(r['prioritizationFee'])>=0)
        if not values:raise TradeError('Estimation de priorite indisponible.')
        probe.priority=max(100_000,values[int((len(values)-1)*.75)]);probe.priority_at=time.monotonic()
        stage='preparation et simulation non signee';print('ETAPE : '+stage,flush=True)
        prepared=await probe.prepare(mint,'sell',quantity,q)
        print('Preparation et simulation : OK ; frais reseau lamports : '+str(prepared['fee_lamports'])+
              ' ; depot lamports : '+str(prepared['rent_lamports']),flush=True)
        print('La sortie parait preparable a cet instant. Aucun ordre signe ou envoye ; aucun etat modifie.',flush=True)
    except Exception as exc:
        cause=str(exc) if isinstance(exc,TradeError) else 'HTTP '+str(exc.code) if isinstance(getattr(exc,'code',None),int) and 100<=exc.code<=599 else type(exc).__name__
        profile=getattr(probe,'last_preparation_profile',None)
        failed=profile[1].get('failed_phase') if profile else None
        print('BLOCAGE : '+stage+(' / '+failed if failed else '')+' : '+cause,flush=True)
        print('Diagnostic termine sans signature, ordre ou changement du journal.',flush=True)


async def verify_external_sale(row,probe):
    """Bounded read-only proof, shared by the live loop and menu13."""
    from discord_token_analysis import parse_trade
    from solders.signature import Signature
    if row['state']!='manual':raise TradeError('Position non manuelle : rapprochement refuse.')
    data=json.loads(row['data']);mint=row['mint'];quantity=data.get('quantity')
    if type(quantity) is not int or quantity<=0:raise TradeError('Quantite enregistree invalide.')
    buy_signature=data.get('buy',{}).get('signature');Signature.from_string(buy_signature)
    if await probe.holdings(mint)!=0:raise TradeError('Solde token non nul : position manuelle conservee.')

    async def transaction(signature):
        Signature.from_string(signature)
        result=await probe.rpc('getTransaction',[signature,{'encoding':'jsonParsed','commitment':'finalized','maxSupportedTransactionVersion':1}])
        if not result or result.get('meta',{}).get('err') is not None:return None
        if (result.get('transaction') or {}).get('signatures',[None])[0]!=signature:return None
        return result

    def token_balance(tx,side):
        return sum(int(b['uiTokenAmount']['amount']) for b in tx['meta'].get(side,[])
                   if b.get('owner')==OWNER and b.get('mint')==mint)

    buy=await transaction(buy_signature)
    proof=parse_trade(buy,OWNER,mint,buy_signature) if buy else None
    if (not proof or proof['kind']!='buy' or proof['swap_proof_version']!=1 or proof['raw']!=quantity
            or token_balance(buy,'preTokenBalances')!=0 or token_balance(buy,'postTokenBalances')!=quantity):
        raise TradeError('Achat initial non verifiable ou quantite differente : journal conserve.')
    buy_slot=buy.get('slot')
    if type(buy_slot) is not int:raise TradeError('Slot achat indisponible : journal conserve.')
    recent=await probe.rpc('getSignaturesForAddress',[OWNER,{'limit':20,'commitment':'finalized','until':buy_signature}])
    found=None;checked=0
    for item in recent[:20]:
        if item.get('err') is not None or type(item.get('slot')) is not int or item['slot']<=buy_slot:continue
        if checked>=8:break
        checked+=1;signature=item['signature'];tx=await transaction(signature)
        if not tx or type(tx.get('slot')) is not int or tx['slot']<=buy_slot:continue
        trade=parse_trade(tx,OWNER,mint,signature)
        if (trade and trade['kind']=='sell' and trade['swap_proof_version']==1
                and trade['raw']==quantity and Decimal(trade['quotes'].get('SOL','0'))>0
                and token_balance(tx,'preTokenBalances')==quantity and token_balance(tx,'postTokenBalances')==0):
            found={'signature':signature,'slot':tx['slot'],'quantity':quantity,'verified_at':time.time()};break
    if not found:
        raise TradeError('Vente totale non prouvee dans la recherche bornee (20 signatures, 8 transactions). Journal conserve ; fournir le lien public de vente pour poursuivre.')
    if await probe.holdings(mint)!=0:raise TradeError('Solde token modifie pendant le controle : journal conserve.')
    return found


def close_external_sale(db,row,found,automatic=False):
    """CAS preserves budget/packets and never overwrites a concurrent update."""
    data=json.loads(row['data']);mint=row['mint']
    proof=dict(found)
    if automatic:proof['automatic']=True
    updated=dict(data,external_sale=proof,exit_reason='MANUAL_EXTERNAL',closed_at=time.time())
    db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
    try:
        result=db.execute("UPDATE trades SET state='closed',data=? WHERE mint=? AND state='manual' AND reserved=? AND data=?",
                          (json.dumps(updated),mint,row['reserved'],row['data']))
        if result.rowcount!=1:raise TradeError('Journal modifie pendant le controle : rapprochement annule.')
        db.commit()
    except BaseException:db.rollback();raise


async def reconcile_external_sale(path,http):
    """Explicit menu13 uses the same proof as automatic live reconciliation."""
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise TradeError('Journal reel absent ou symbolique : rapprochement refuse.')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:rows=[dict(row) for row in db.execute("SELECT * FROM trades WHERE state IN ('open','manual','buy_pending','sell_pending')")]
    finally:db.close()
    if not rows:print('Aucune position active a rapprocher.');return
    if len(rows)!=1 or rows[0]['state']!='manual':
        raise TradeError('Seule une position en intervention manuelle peut etre rapprochee ; utiliser le diagnostic 12.')
    row=rows[0]
    probe=Executor.__new__(Executor);probe.transport=ExitReadOnlyTransport(http,'')
    print('RAPPROCHEMENT : lectures RPC uniquement ; aucun ordre ni signature nouvelle.',flush=True)
    print('TOKEN : '+row['mint'],flush=True)
    print('Une cloture verifiee debloque les nouveaux achats si le mode reel est actif.',flush=True)
    found=await verify_external_sale(row,probe)
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True)
    try:close_external_sale(db,row,found)
    finally:db.close()
    print('POSITION CLOTUREE : vente externe finalisee verifiee : '+found['signature'],flush=True)
    print('Budget reserve et anciennes signatures conserves. Aucun ordre envoye.',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup',action='store_true')
    parser.add_argument('--setup-if-missing',action='store_true',help='Conserver les cles locales deja configurees')
    parser.add_argument('--report',action='store_true')
    parser.add_argument('--check',action='store_true',help='Verifier les acces en lecture seule, sans signature ou ordre')
    parser.add_argument('--update-jupiter',action='store_true',help='Remplacer localement la cle API Jupiter seule, sans toucher au wallet')
    parser.add_argument('--diagnose-exit',action='store_true',help='Diagnostiquer la position ouverte sans signer/envoyer ni modifier le journal')
    parser.add_argument('--reconcile-external-sale',action='store_true',help='Cloturer le suivi manuel apres preuve RPC de vente totale, sans ordre')
    args=parser.parse_args()
    try:
        if args.reconcile_external_sale:
            from wallet_alerts_ws import settings
            _,_,http,_=settings(ROOT/'wallet_alerts_config.json')
            asyncio.run(reconcile_external_sale(ROOT/'data/wallet_alerts_ws/live_trades.sqlite3',http))
        elif args.diagnose_exit:
            from wallet_alerts_ws import settings
            _,_,http,_=settings(ROOT/'wallet_alerts_config.json')
            asyncio.run(diagnose_exit(ROOT/'data/wallet_alerts_ws/live_trades.sqlite3',http))
        elif args.update_jupiter:update_jupiter()
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
                                  useSharedAccounts=False,asLegacyTransaction=False,dynamicComputeUnitLimit=False))
            checked_instructions(payload,q,1)
            print('Wallet correct, RPC, cotation et instructions Jupiter : OK. Solde SOL :',Decimal(balance)/10**9)
            print('Aucune signature, aucun ordre envoye. Routes des futurs tokens non garanties.')
        elif args.report:
            path=ROOT/'data/wallet_alerts_ws/live_trades.sqlite3'
            if not path.is_file():print('Aucun essai reel enregistre.');return
            db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            print('Budget reserve cumule (non recycle) :',sum((Decimal(r[0]) for r in db.execute('SELECT reserved FROM trades')),Decimal(0)),'/',BUDGET,'USD')
            for mint,state,data in db.execute('SELECT mint,state,data FROM trades'):
                value=json.loads(data);buy=value.get('buy',{})
                print(mint,state,'buy:',buy.get('signature','-'),'sell:',value.get('sell',{}).get('signature','-'))
                for side in ('buy','sell'):
                    result=value.get(side,{}).get('rpc_send')
                    if result:print('Envoi '+side+' :',send_result_text(result),'; details :',json.dumps(result))
                    elif side=='buy' and state=='expired':print('Envoi buy : ancien enregistrement, retour RPC original non conserve.')
                if state=='open' and exit_deadline(value) is not None:
                    print('Sortie automatique : reste environ',max(0,math.ceil(exit_deadline(value)-time.time())),'s ; motif enregistre :',value.get('exit_reason','aucun'))
                if state=='manual':print('Vente manuelle : rapprochement automatique si le mode reel est actif ; menu 13 disponible en secours.')
                if value.get('external_sale'):print('Vente externe verifiee :',value['external_sale']['signature'])
                if value.get('entry_guard'):print('Controle prix avant signature (quantites brutes) :',json.dumps(value['entry_guard']))
                if value.get('entry_quote'):print('Cotation/simulation avant signature (quantites brutes, sans filtre 5 %) :',json.dumps(value['entry_quote']))
                metrics={'detection_decision_s':value.get('decision_elapsed_s'),'preparation_ms':value.get('prepare_ms')}
                if buy.get('send_at') and value.get('decision_wall'):metrics['decision_envoi_HTTP_ms']=round((buy['send_at']-value['decision_wall'])*1000)
                if buy.get('send_at') and value.get('confirmed_at'):metrics['envoi_confirmation_observee_ms']=round((value['confirmed_at']-buy['send_at'])*1000)
                print('Mesures locales :',json.dumps(metrics))
                if value.get('latency_breakdown'):
                    print('Detail validation/envoi (ms ; reponse HTTP apres envoi) :',json.dumps(value['latency_breakdown']))
                profile=value.get('preparation_profile')
                if profile:
                    print('Preparation (phases paralleles, ne pas additionner) :',json.dumps(profile.get('phases_ms',{})),
                          '; blockhash precharge :',profile.get('blockhash_cache_hit',False))
                    totals={}
                    for name,entries in profile.get('transport',{}).items():
                        group=totals.setdefault(name.split('/')[0],{'file_ms':0,'quota_ms':0,'reponse_ms':0})
                        for item in entries:
                            for key,field in [('queue_wait_ms','file_ms'),('quota_wait_ms','quota_ms'),('http_response_ms','reponse_ms')]:group[field]+=item.get(key,0)
                    print('Preparation : cumul reseau par fournisseur (ms) :',json.dumps(totals))
            ignored=[]
            for raw, in db.execute("SELECT data FROM notices WHERE code='ACHAT_IGNORE' ORDER BY rowid DESC"):
                try:record=json.loads(raw)
                except (TypeError,ValueError):continue
                if record.get('execution_version')==EXECUTION_VERSION:ignored.append(record)
            print('Achats ignores mesures avec '+EXECUTION_VERSION+' :',len(ignored))
            for record in ignored[:5]:
                print(record['mint'],record['message'],'; detail (ms) :',json.dumps(record.get('latency_breakdown',{})))
            db.close()
        else:parser.print_help()
    except Exception as exc:raise SystemExit(str(exc) if isinstance(exc,TradeError) else 'Configuration impossible ; verifier la cle locale, les permissions et les dependances.') from None


if __name__=='__main__':main()
