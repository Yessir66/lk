#!/usr/bin/env python3
"""Acheter 2 USD puis publier un callout pump.fun, au plus tôt 15 s après l'original."""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import random
import math
import sqlite3
import threading
import time
import uuid
from decimal import Decimal, ROUND_DOWN

from callout_alerts import API, Pump, RemoteError, WALLET, timestamp

SOL = 'So11111111111111111111111111111111111111112'
CALLOUT_TEXTS = [
    "let's run it, seems like a good narra",
    'narra looks clean on this one, sending it',
    "like this narrative a lot, let's see where it goes",
    "good narra, early enough, let's run it",
    "this one has a real narrative, i'm in",
    'grabbed a small bag early on this one',
    "the timing on this one feels right, i'm in",
    'chart looks healthy and the community is waking up',
    'could see this ticker everywhere soon, taking a position',
    'still early here, holding this one',
]

ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == 'scripts' else Path(__file__).resolve().parent
# Addresses taken from the original repository's caller list; stable through profile renames.
CALLERS = {
    '6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm': '0xEthan',
    'Be24Gbf5KisDk1LcWWZsBn8dvB816By7YzYF5zWZnRR6': 'chairman',
    '5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM': 'schoen',
    '87rRdssFiTJKY4MGARa4G5vQ31hmR7MxSmhzeaJ5AAxJ': 'Dior',
    'ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT': 'trunoest',
    'CE44oKS3wpUerx8afyeii56u5oQjBLZknzm4Q2CYHUz9': 'ely',
    'DuARUpsEzdVbVpSnbGipLJvWxaXUCPLDJDjNov6Rc6s6': 'HarryPaulDavies',
    'EqBQhoU88ty3a3aDphDRCBuiRirjUsFSFi4UHGY64qvv': 'sadcrissy',
    '2M2vLX34LXMg24dMEnjWHvRXS1tshpEDRWzmXgV8ENNZ': 'SossaDotKek',
    'AH39BneW9UeWxQysUcwogQrYFdPvJuRVZ5w4ccq9SKfL': '_logjam',
}


def lamports_for(usd, sol_price):
    price = Decimal(str(sol_price))
    if not price.is_finite() or price <= 0:
        raise RemoteError('Prix SOL/USD invalide')
    return int((Decimal(str(usd)) / price * 10**9).to_integral_value(rounding=ROUND_DOWN))


class Journal:
    def __init__(self, path, since, owner):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (mint TEXT PRIMARY KEY, data TEXT NOT NULL)')
        for key, value in [('since', str(since)), ('owner', owner)]:
            self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', (key, value))
        self.db.commit()
        saved = dict(self.db.execute('SELECT key,value FROM meta'))
        if saved['owner'] != owner:
            raise RemoteError('Cet état appartient à un autre wallet ; choisir un autre --state')
        self.since = float(saved['since'])

    def save(self, job):
        self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)', (job['mint'], json.dumps(job)))
        self.db.commit()

    def jobs(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM jobs ORDER BY rowid')]

    def add(self, call, now, max_age, delay, template):
        mint, author = call.get('coinMint', ''), call.get('author')
        if author not in CALLERS or not WALLET.fullmatch(mint):
            return False
        created = timestamp(call['createdAt'])
        if created < self.since or created > now + 5 or now - created > max_age:
            return False
        if self.db.execute('SELECT 1 FROM jobs WHERE mint=?', (mint,)).fetchone():
            return False
        text = random.choice(CALLOUT_TEXTS) if template is None else template.format(caller=CALLERS[author], text=call.get('thesis') or f'Call de {CALLERS[author]}')
        self.save(dict(mint=mint, call_id=str(call['calloutId']), caller=CALLERS[author], text=text,
                       created=created, due=created + delay, state='new', retry_at=0))
        return True


class Execution:
    def __init__(self, args, owner=None):
        from curl_cffi import requests
        self.args = args
        self.http = requests.Session(impersonate='chrome')
        self.pump = Pump('browser')
        self.keypair = None
        self.owner = owner or self.pump.resolve(args.profile)[0]
        if args.live:
            from solders.keypair import Keypair
            raw = Path(args.wallet).read_text().strip()
            self.keypair = Keypair.from_bytes(bytes(json.loads(raw))) if raw.startswith('[') else Keypair.from_base58_string(raw)
            if str(self.keypair.pubkey()) != self.owner:
                raise RemoteError('Le wallet fourni ne correspond pas au profil pump.fun demandé')
            self.check_auth()

    def json(self, url, body=None, headers=None):
        try:
            response = self.http.request('GET' if body is None else 'POST', url, json=body, headers=headers or {}, timeout=20)
            if response.status_code != 200:
                raise RemoteError(f'Service HTTP {response.status_code}', status=response.status_code)
            data = response.json()
            if 'error' in data:
                raise RemoteError('Service : requête refusée')
            return data
        except RemoteError:
            raise
        except Exception:
            raise RemoteError('Service inaccessible ou réponse invalide') from None

    def rpc(self, method, params):
        data = self.json(self.args.rpc, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        if 'result' not in data:
            raise RemoteError(f'RPC {method} : résultat absent')
        return data['result']

    def jupiter(self, path, body=None):
        headers = {'x-api-key': os.getenv('JUP_API_KEY')} if os.getenv('JUP_API_KEY') else {}
        return self.json(self.args.jupiter.rstrip('/') + path, body, headers)

    def pump_call(self, fields=None):
        # Re-read to allow session renewal without restarting or buying again.
        token = Path(self.args.token).read_text().strip()
        headers = {'Authorization': f'Bearer {token}', 'Cookie': f'auth_token={token}',
                   'Origin': 'https://pump.fun', 'Referer': 'https://pump.fun/'}
        data = None
        if fields is not None:
            boundary = '----callout' + uuid.uuid4().hex
            data = ''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items())
            data = (data + f'--{boundary}--\r\n').encode()
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        try:
            response = self.http.request('GET' if fields is None else 'POST',
                                         API + ('/auth/my-profile' if fields is None else '/callout/create'),
                                         headers=headers, data=data, timeout=20)
            return response.status_code, response.text
        except Exception:
            raise RemoteError('pump.fun : réponse de publication inconnue') from None

    def check_auth(self):
        status, body = self.pump_call()
        if status != 200:
            raise RemoteError(f'Session pump.fun refusée (HTTP {status})')
        if json.loads(body).get('address') != self.owner:
            raise RemoteError('La session pump.fun et le wallet ne correspondent pas')

    def prepare(self, mint):
        from solders.transaction import VersionedTransaction
        price = self.jupiter('/price/v3?ids=' + SOL)[SOL]['usdPrice']
        amount = lamports_for(self.args.usd, price)
        balance = self.rpc('getBalance', [self.owner, {'commitment': 'confirmed'}])['value']
        if balance < amount + 5_000_000:
            raise RemoteError('SOL insuffisant pour le montant et la réserve de frais')
        quote = self.jupiter(f'/swap/v1/quote?inputMint={SOL}&outputMint={mint}&amount={amount}&slippageBps={self.args.slippage_bps}')
        if quote.get('inputMint') != SOL or quote.get('outputMint') != mint or int(quote.get('inAmount', 0)) != amount:
            raise RemoteError('La cotation ne correspond pas au montant et au token demandés')
        swap = self.jupiter('/swap/v1/swap', {'quoteResponse': quote, 'userPublicKey': self.owner,
                           'wrapAndUnwrapSol': True, 'dynamicComputeUnitLimit': True,
                           'prioritizationFeeLamports': {'priorityLevelWithMaxLamports': {
                               'maxLamports': 100_000, 'priorityLevel': 'high'}}})
        unsigned = VersionedTransaction.from_bytes(base64.b64decode(swap['swapTransaction']))
        tx = VersionedTransaction(unsigned.message, [self.keypair])
        return {'raw': base64.b64encode(bytes(tx)).decode(), 'signature': str(tx.signatures[0]),
                'last_valid_height': int(swap['lastValidBlockHeight']), 'lamports': amount}

    def status(self, signature):
        return self.rpc('getSignatureStatuses', [[signature], {'searchTransactionHistory': True}])['value'][0]

    def broadcast(self, raw):
        return self.rpc('sendTransaction', [raw, {'encoding': 'base64', 'skipPreflight': False, 'maxRetries': 2}])

    def expired(self, height):
        return self.rpc('getBlockHeight', [{'commitment': 'confirmed'}]) > height

    def holds(self, mint):
        result = self.rpc('getTokenAccountsByOwner', [self.owner, {'mint': mint},
                          {'encoding': 'jsonParsed', 'commitment': 'confirmed'}])
        return any(int(a['account']['data']['parsed']['info']['tokenAmount']['amount']) > 0 for a in result['value'])

    def already_posted(self, job):
        return any(c.get('coinMint') == job['mint'] and c.get('thesis') == job['text']
                   and timestamp(c['createdAt']) >= job['created'] for c in self.pump.callouts(self.owner))


class Engine:
    def __init__(self, journal, execution, args):
        self.journal, self.execution, self.args = journal, execution, args

    def step(self, now):
        for job in self.journal.jobs():
            if job['state'] in ('posted', 'simulated', 'failed', 'review') or job.get('retry_at', 0) > now:
                continue
            try:
                self.advance(job, now)
            except Exception as exc:
                # Never print responses, headers, private keys or arbitrary exception text.
                detail = str(exc) if isinstance(exc, RemoteError) else type(exc).__name__
                print(f'{job["caller"]} : étape {job["state"]} différée ({detail})', flush=True)
                job['retry_at'] = now + 10
                self.journal.save(job)

    def advance(self, job, now):
        if job['state'] == 'new':
            if now - job['created'] > self.args.max_age:
                job['state'] = 'failed'
            elif not self.args.live:
                print(f'SIMULATION : achat {self.args.usd} USD pour {job["caller"]}, publication au plus tôt à {job["due"]:.0f} : {job["text"]}', flush=True)
                job['state'] = 'simulated'
            else:
                # Preparation never broadcasts. Persist the signature BEFORE any send.
                job.update(self.execution.prepare(job['mint']), state='signed')
            self.journal.save(job)
            return
        if job['state'] == 'signed':
            status = self.execution.status(job['signature'])
            if status and status.get('err') is not None:
                job['state'] = 'failed'
            elif status and status.get('confirmationStatus') in ('confirmed', 'finalized'):
                job['state'] = 'confirmed'
                print(f'Achat confirmé : {job["mint"]}', flush=True)
            elif status:
                # Already processed: wait for confirmation, never replace this transaction.
                job['retry_at'] = now + 1
            elif self.execution.expired(job['last_valid_height']):
                job['state'] = 'review'  # Unknown history must never trigger a fresh purchase.
                print(f'Transaction expirée/non retrouvée : vérifier {job["signature"]}', flush=True)
            else:
                # All retries use the same signed transaction and therefore the same signature.
                self.execution.broadcast(job['raw'])
                job['retry_at'] = now + 2
            self.journal.save(job)
            return
        if job['state'] in ('publishing', 'uncertain'):
            if self.execution.already_posted(job):
                job['state'] = 'posted'
                print(f'Publication retrouvée : {job["mint"]}', flush=True)
            elif now - job.get('post_started', now) > 120:
                job['state'] = 'review'
                print(f'Publication incertaine : vérifier le profil pour {job["mint"]}', flush=True)
            else:
                job['retry_at'] = now + 5
            self.journal.save(job)
            return
        if job['state'] == 'confirmed' and now >= job['due']:
            if not self.execution.holds(job['mint']):
                job['retry_at'] = now + 3
                self.journal.save(job)
                return
            self.execution.check_auth()
            job.update(state='publishing', post_started=now)
            self.journal.save(job)
            # If this call times out/crashes, next step reconciles rather than posts twice.
            status, body = self.execution.pump_call({'coinMint': job['mint'], 'thesis': job['text']})
            if 200 <= status < 300:
                job['state'] = 'posted'
                print(f'CALLOUT PUBLIÉ : {job["caller"]} → {job["mint"]}', flush=True)
            elif status in (401, 403, 429) or (400 <= status < 500 and 'INSUFFICIENT_BALANCE' in body):
                job.update(state='confirmed', retry_at=now + 30)
                print(f'Publication refusée HTTP {status} ; achat conservé, nouvelle tentative sans racheter.', flush=True)
            elif 400 <= status < 500:
                job['state'] = 'failed'
                print(f'Publication refusée HTTP {status} : {job["mint"]}', flush=True)
            else:
                job['state'] = 'uncertain'
            self.journal.save(job)


def detect(events, stop, since):
    pump, index, feed_after, list_after = Pump('browser'), 0, 0, 0
    while not stop.is_set():
        for source in ('feed', 'list'):
            now = time.monotonic()
            if now < (feed_after if source == 'feed' else list_after):
                continue
            try:
                if source == 'feed':
                    rows = pump.feed()
                else:
                    wallet = list(CALLERS)[index % len(CALLERS)]
                    index += 1
                    rows = [dict(c, author=wallet) for c in pump.callouts(wallet)]
                for call in rows:
                    try:
                        if call.get('author') in CALLERS and timestamp(call['createdAt']) >= since:
                            events.put(call)
                    except (KeyError, TypeError, ValueError, OverflowError):
                        pass
            except RemoteError as exc:
                print(f'Détection {source} : {exc}', flush=True)
                if source == 'feed':
                    feed_after = now + 120
                else:
                    list_after = now + 120
        stop.wait(2)


def parse_args(argv=None, configure=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='Lelouch_0x')
    parser.add_argument('--live', action='store_true', help='Effectuer réellement les achats et publications')
    parser.add_argument('--usd', type=Decimal, default=Decimal('2'))
    parser.add_argument('--delay', type=float, default=15)
    parser.add_argument('--max-age', type=float, default=60, help='Ne pas acheter un callout découvert trop tard')
    parser.add_argument('--slippage-bps', type=int, default=100)
    parser.add_argument('--wallet', default=str(ROOT / 'data' / 'pump_wallet.txt'))
    parser.add_argument('--token', default=str(ROOT / 'data' / 'pump_token.txt'))
    parser.add_argument('--rpc', default=os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com'))
    parser.add_argument('--jupiter', default='https://api.jup.ag')
    parser.add_argument('--text', default=None, help='Remplacer le choix aléatoire : {text} et {caller} sont remplacés')
    parser.add_argument('--state')
    if configure:
        configure(parser)
    args = parser.parse_args(argv)
    if not args.usd.is_finite() or args.usd <= 0 or not math.isfinite(args.delay) or args.delay < 0 or not math.isfinite(args.max_age) or args.max_age <= 0 or not 1 <= args.slippage_bps <= 5000:
        parser.error('Montant/délais/slippage invalides')
    try:
        if args.text is not None:
            args.text.format(text='exemple', caller='exemple')
    except (KeyError, ValueError, AttributeError, IndexError):
        parser.error('--text accepte seulement {text} et {caller}')
    if args.live and any(not Path(p).is_file() for p in (args.wallet, args.token)):
        parser.error('Créer les fichiers locaux --wallet et --token (voir CALLOUT_REPOST.md)')
    return args


def main():
    args = parse_args()
    execution = Execution(args)
    path = args.state or str(ROOT / 'data' / ('repost_live.sqlite3' if args.live else 'repost_simulation.sqlite3'))
    journal = Journal(path, time.time(), execution.owner)
    engine = Engine(journal, execution, args)
    events, stop = queue.Queue(), threading.Event()
    thread = threading.Thread(target=detect, args=(events, stop, journal.since), daemon=True)
    print(f'{"RÉEL" if args.live else "SIMULATION"} : 10 callers, {args.usd} USD/token, délai minimal {args.delay}s. Profil {args.profile}', flush=True)
    thread.start()
    try:
        while True:
            try:
                call = events.get(timeout=.5)
                journal.add(call, time.time(), args.max_age, args.delay, args.text)
            except queue.Empty:
                pass
            engine.step(time.time())
    finally:
        stop.set()
        journal.db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nArrêt. État conservé.')
    except RemoteError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit(f'Démarrage impossible ({type(exc).__name__}) : vérifier configuration et accès aux API.') from None
