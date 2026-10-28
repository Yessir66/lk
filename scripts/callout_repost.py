#!/usr/bin/env python3
"""Acheter après 12 s, publier après confirmation, suivre les updates et les sorties."""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import math
import sqlite3
import threading
import time
import uuid
import re
from decimal import Decimal, ROUND_DOWN

from callout_alerts import API, Pump, RemoteError, WALLET, retry_seconds, timestamp
from callout_content import local_text, image_type

SOL = 'So11111111111111111111111111111111111111112'


ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == 'scripts' else Path(__file__).resolve().parent
# Only these three profiles may trigger new purchases and publications.
CALLERS = {
    'CkPFGv2Wv1vwdWjtXioEgb8jhZQfs3eVZez3QCetu7xD': 'lynk0x_',
    '8NFh9y6qmMigMvRtT2KhrnCDo4BE2jMYcondAzmXZ26E': 'YourMistyy',
    'CxgPWvH2GoEDENELne2XKAR2z2Fr4shG2uaeyqZceGve': 'narc',
}


def lamports_for(usd, sol_price):
    price = Decimal(str(sol_price))
    if not price.is_finite() or price <= 0:
        raise RemoteError('Prix SOL/USD invalide')
    return int((Decimal(str(usd)) / price * 10**9).to_integral_value(rounding=ROUND_DOWN))


class RunnerLock:
    """One trading runner per installation, released by the OS even after a crash."""
    def __init__(self, path):
        self.path, self.stream = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise RemoteError('Un hub ou bot de republication tourne déjà dans cette installation. Arrête-le avant de démarrer ici.') from None
        return self

    def __exit__(self, *_):
        if self.stream:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


class Journal:
    def __init__(self, path, since, owner):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (mint TEXT PRIMARY KEY, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS updates (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
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

    def add(self, call, now, max_age, delay, template, buy_delay=0, stop_loss=None):
        mint, author = call.get('coinMint', ''), call.get('author')
        if author not in CALLERS or not WALLET.fullmatch(mint):
            return False
        created = timestamp(call['createdAt'])
        if created < self.since or created > now + 5 or now - created > max_age:
            return False
        if self.db.execute('SELECT 1 FROM jobs WHERE mint=?', (mint,)).fetchone():
            return False
        text = local_text(call) if template is None else template.format(caller=CALLERS[author], text=call.get('thesis') or '')
        self.save(dict(mint=mint, call_id=str(call['calloutId']), caller=CALLERS[author], text=text,
                       created=created, due=created + delay, state='new', retry_at=0,
                       detected_at=call.get('_detected_at', now), queued_at=now,
                       buy_at=created + buy_delay, source_author=author, source_thesis=call.get('thesis') or '',
                       source_media=call.get('mediaUrl'), content_ready=False, workflow_version=2,
                       stop_loss=str(stop_loss) if stop_loss is not None else None,
                       name=call.get('name'), symbol=call.get('symbol'), entry_mc=call.get('marketCap')))
        return True

    def apply_policy(self):
        for job in self.jobs():
            if job.get('source_author') not in CALLERS:
                job['publication_disabled'] = True
                if job['state'] == 'new':
                    job.update(state='failed', failure_reason='caller_removed')
                self.save(job)  # Existing holdings still keep their TP/SL monitoring.

    def save_update(self, update):
        self.db.execute('INSERT OR REPLACE INTO updates VALUES (?,?)', (update['key'], json.dumps(update)))
        self.db.commit()

    def updates(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM updates ORDER BY rowid')]

    def add_update(self, event, now, delay):
        row = self.db.execute('SELECT data FROM jobs WHERE mint=?', (event.get('mint'),)).fetchone()
        if not row:
            return False
        job = json.loads(row[0])
        if job.get('publication_disabled') or job.get('source_author') not in CALLERS or event.get('source_call_id') != job['call_id']:
            return False
        source = event['update']
        created = timestamp(source['createdAt'])
        identity = str(source['id'])
        key = job['call_id'] + ':' + identity
        if created < job['created'] or created > now + 5 or self.db.execute('SELECT 1 FROM updates WHERE id=?', (key,)).fetchone():
            return False
        self.save_update(dict(key=key, mint=job['mint'], source=source, created=created, due=created+delay,
                              text=local_text(source, 'update', job.get('exit_state') in ('sold', 'empty')),
                              state='new', retry_at=0, content_ready=False))
        return True


def response_detail(response):
    """Classify known provider errors without logging raw bodies or echoed credentials."""
    try:
        data = response.json()
    except Exception:
        return 'réponse non JSON'
    if not isinstance(data, dict):
        return 'réponse inattendue'
    parts = [data.get('errorCode', ''), data.get('message', ''), data.get('error', '')]
    text = ' '.join(str(p) for p in parts).lower()
    for terms, detail in [
        (('insufficient funds', 'insufficient_funds'), 'solde insuffisant signalé'),
        (('not tradable', 'not_tradable'), 'token non échangeable signalé'),
        (('simulate', 'simulation'), 'échec de simulation signalé'),
        (('prioritization fee', 'priority fee'), 'calcul des frais prioritaires en échec'),
        (('blockhash',), 'blockhash indisponible'),
        (('route',), 'échec de route de swap signalé'),
        (('compute',), 'budget de calcul en échec'),
        (('timeout', 'timed out'), 'délai du fournisseur dépassé'),
    ]:
        if any(term in text for term in terms):
            return detail
    return 'aucun détail reconnu'


def show_status(path):
    """Read an existing journal without connecting to a wallet or any remote service."""
    path = Path(path).resolve()
    if not path.is_file():
        print(f'Journal introuvable : {path}', flush=True)
        return
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT data FROM jobs ORDER BY rowid DESC LIMIT 20').fetchall()
        if not rows:
            print('Journal vide.', flush=True)
        for row in rows:
            job = json.loads(row[0])
            print(f'{job.get("caller", "?")} | {job["mint"]} | état : {job["state"]}', flush=True)
            if job.get('buy_confirmed') or job['state'] in ('confirmed', 'publishing', 'uncertain', 'posted'):
                print('  Achat confirmé ; sortie ' + ('SL' if job.get('exit_reason') == 'sl' else 'TP/SL' if job.get('stop_loss') else 'TP') + ' : ' + job.get('exit_state', 'en attente de contrôle'), flush=True)
            elif job.get('signature'):
                print('  Transaction signée ; confirmation non établie par cet état.', flush=True)
            else:
                print('  Aucune transaction d’achat signée enregistrée.', flush=True)
            if job.get('failure_reason') == 'entry_expired':
                print('  Achat abandonné : limite d’âge du callout dépassée avant préparation.', flush=True)
            if job.get('last_error'):
                print('  Dernière erreur : ' + job['last_error'], flush=True)
            if job.get('tp_error'):
                print('  Dernière erreur TP : ' + job['tp_error'], flush=True)
            if job.get('attempts'):
                print(f'  Tentatives de préparation : {job["attempts"]}', flush=True)
            if job.get('detect_to_send_ms') is not None:
                print(f'  Latence détection→première tentative d’envoi : {job["detect_to_send_ms"]}ms ; '
                      f'préparation : {job.get("prepare_ms", "?")}ms ; réponse RPC : {job.get("broadcast_ms", "?")}ms', flush=True)
    finally:
        db.close()


def check_swap(args):
    """Exercise the local API access and unsigned builder without loading signing keys."""
    import copy
    options = copy.copy(args)
    options.live = False
    mint = options.check_swap
    if not WALLET.fullmatch(mint):
        raise RemoteError('Adresse du token invalide')
    execution = Execution(options)
    print('TEST EN LECTURE SEULE : prix, cotation et construction non signée.', flush=True)
    price = execution.sol_price()
    amount = lamports_for(options.usd, price)
    quote = execution.jupiter(f'/swap/v1/quote?inputMint={SOL}&outputMint={mint}&amount={amount}&slippageBps={options.slippage_bps}')
    if quote.get('inputMint') != SOL or quote.get('outputMint') != mint or int(quote.get('inAmount', 0)) != amount:
        raise RemoteError('Cotation incohérente pour ce test')
    print('Cotation Jupiter : OK.', flush=True)
    execution.build_swap(quote)
    print('Construction de la transaction non signée : OK.', flush=True)
    print('Le test n’a signé ni envoyé de transaction. Il ne relance pas l’ancien achat.', flush=True)


class Execution:
    def __init__(self, args, owner=None):
        from curl_cffi import requests
        self.args = args
        self.http = requests.Session(impersonate='chrome')
        self.pump = Pump('browser')
        self.keypair = None
        self._price = None
        self._price_at = 0
        self._limits = {}
        self._balances = {}
        self.owner = owner or self.pump.resolve(args.profile)[0]
        if args.live:
            from solders.keypair import Keypair
            raw = Path(args.wallet).read_text().strip()
            self.keypair = Keypair.from_bytes(bytes(json.loads(raw))) if raw.startswith('[') else Keypair.from_base58_string(raw)
            if str(self.keypair.pubkey()) != self.owner:
                raise RemoteError('Le wallet fourni ne correspond pas au profil pump.fun demandé')
            self.check_auth()

    def json(self, url, body=None, headers=None, label='Service', bucket='service'):
        # Only fixed service/method labels are logged: URLs may contain private RPC keys.
        if not hasattr(self, '_limits'):
            self._limits = {}
        limit = self._limits.setdefault(bucket, {'until': 0, 'next': 0, 'failures': 0})
        remaining = limit['until'] - time.monotonic()
        if remaining > 0:
            raise RemoteError(f'{label} : pause fournisseur ({remaining:.0f}s restantes)', status=429, retry_after=remaining)
        interval = 1 / getattr(self.args, 'jupiter_rps', .5) if bucket == 'jupiter' else 1 / getattr(self.args, 'rpc_rps', 2)
        time.sleep(max(0, limit['next'] - time.monotonic()))
        limit['next'] = time.monotonic() + interval
        try:
            response = self.http.request('GET' if body is None else 'POST', url, json=body, headers=headers or {}, timeout=20)
            retry = retry_seconds(response.headers.get('Retry-After'))
            if response.status_code != 200:
                detail = response_detail(response) if response.status_code >= 400 and response.status_code != 429 else ''
                suffix = f' ({detail})' if detail else ''
                raise RemoteError(f'{label} : HTTP {response.status_code}{suffix}', status=response.status_code, retry_after=retry)
            data = response.json()
            if not isinstance(data, dict):
                raise RemoteError(f'{label} : format de réponse inattendu')
            if data.get('error') is not None:
                error = data['error']
                code = error.get('code') if isinstance(error, dict) else None
                message = str(error.get('message', '')) if isinstance(error, dict) else str(error)
                limited = code == 429 or any(term in message.lower() for term in ('rate limit', 'ratelimit', 'too many requests'))
                safe_code = f' (code {code})' if isinstance(code, int) else ''
                raise RemoteError(f'{label} : erreur de service{safe_code}', status=429 if limited else None, retry_after=retry)
            limit['failures'] = 0
            return data
        except RemoteError as exc:
            if exc.status == 429:
                wait = max(exc.retry_after or 0, min(300, 15 * 2 ** min(limit['failures'], 5)))
                limit['failures'] += 1
                limit['until'] = time.monotonic() + wait
                raise RemoteError(f'{exc} ; reprise dans {wait:.0f}s', status=429, retry_after=wait) from None
            if exc.retry_after:
                limit['until'] = time.monotonic() + exc.retry_after
            raise
        except Exception:
            raise RemoteError(f'{label} : connexion impossible ou réponse invalide') from None

    def rpc(self, method, params):
        data = self.json(self.args.rpc, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params},
                         label=f'RPC Solana/{method}', bucket='rpc')
        if 'result' not in data:
            raise RemoteError(f'RPC {method} : résultat absent')
        return data['result']

    def jupiter(self, path, body=None):
        headers = {'x-api-key': os.getenv('JUP_API_KEY')} if os.getenv('JUP_API_KEY') else {}
        return self.json(self.args.jupiter.rstrip('/') + path, body, headers,
                         label=f'Jupiter{path.split("?")[0]}', bucket='jupiter')

    def pump_call(self, fields=None, image=None, reply_to=None):
        # Re-read to allow session renewal without restarting or buying again.
        token = Path(self.args.token).read_text().strip()
        headers = {'Authorization': f'Bearer {token}', 'Cookie': f'auth_token={token}',
                   'Origin': 'https://pump.fun', 'Referer': 'https://pump.fun/'}
        data = None
        if fields is not None:
            boundary = '----callout' + uuid.uuid4().hex
            data = ''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()).encode()
            if image and image.get('image_path'):
                path = Path(image['image_path']).resolve()
                if not path.is_relative_to((ROOT / 'data' / 'content').resolve()):
                    raise RemoteError('Chemin du média refusé')
                if path.is_file() and path.stat().st_size <= 8*1024*1024:
                    blob = path.read_bytes()
                    mime = image_type(blob)
                    if mime:
                        suffix = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}[mime]
                        data += (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="callout.{suffix}"\r\n'
                                 f'Content-Type: {mime}\r\n\r\n').encode() + blob + b'\r\n'
            data += f'--{boundary}--\r\n'.encode()
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        if reply_to is not None and not re.fullmatch(r'[0-9a-fA-F-]{36}', str(reply_to)):
            raise RemoteError('Identifiant du callout de destination invalide')
        route = '/auth/my-profile' if fields is None else '/callout/create'
        if reply_to is not None:
            route = '/callout/' + reply_to + '/replies'
        try:
            response = self.http.request('GET' if fields is None else 'POST',
                                         API + route,
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
        started = time.monotonic()
        price = self.sol_price()
        priced = time.monotonic()
        amount = lamports_for(self.args.usd, price)
        balance = self.rpc('getBalance', [self.owner, {'commitment': 'confirmed'}])['value']
        balanced = time.monotonic()
        if balance < amount + 5_000_000:
            raise RemoteError('SOL insuffisant pour le montant et la réserve de frais')
        quote = self.jupiter(f'/swap/v1/quote?inputMint={SOL}&outputMint={mint}&amount={amount}&slippageBps={self.args.slippage_bps}')
        if quote.get('inputMint') != SOL or quote.get('outputMint') != mint or int(quote.get('inAmount', 0)) != amount:
            raise RemoteError('La cotation ne correspond pas au montant et au token demandés')
        quoted = time.monotonic()
        signed = self.sign_quote(quote)
        signed.update(lamports=amount, cost_usd=str(Decimal(amount) / 10**9 * Decimal(str(price))))
        signed['prepare_timings_ms'] = dict(price=round((priced - started) * 1000),
                                          balance=round((balanced - priced) * 1000),
                                          quote=round((quoted - balanced) * 1000),
                                          build_sign=round((time.monotonic() - quoted) * 1000))
        return signed

    def build_swap(self, quote):
        swap = self.jupiter('/swap/v1/swap', {'quoteResponse': quote, 'userPublicKey': self.owner,
                           'wrapAndUnwrapSol': True, 'dynamicComputeUnitLimit': True,
                           'prioritizationFeeLamports': {'priorityLevelWithMaxLamports': {
                               'maxLamports': 100_000, 'priorityLevel': 'high'}}})
        if swap.get('simulationError') is not None:
            raise RemoteError('Jupiter/swap/v1/swap : échec de simulation signalé')
        if not swap.get('swapTransaction') or 'lastValidBlockHeight' not in swap:
            raise RemoteError('Jupiter/swap/v1/swap : transaction ou validité absente')
        return swap

    def sign_quote(self, quote):
        from solders.transaction import VersionedTransaction
        swap = self.build_swap(quote)
        unsigned = VersionedTransaction.from_bytes(base64.b64decode(swap['swapTransaction']))
        tx = VersionedTransaction(unsigned.message, [self.keypair])
        return {'raw': base64.b64encode(bytes(tx)).decode(), 'signature': str(tx.signatures[0]),
                'last_valid_height': int(swap['lastValidBlockHeight'])}

    def sol_price(self):
        if getattr(self, '_price', None) is None or time.monotonic() - self._price_at >= 5:
            price = Decimal(str(self.jupiter('/price/v3?ids=' + SOL)[SOL]['usdPrice']))
            if not price.is_finite() or price <= 0:
                raise RemoteError('Prix SOL/USD invalide')
            self._price, self._price_at = price, time.monotonic()
        return self._price

    def acquired(self, signature, mint):
        tx = self.transaction(signature)
        if not tx or not tx.get('meta') or tx['meta'].get('err'):
            raise RemoteError('Métadonnées de l’achat indisponibles')
        meta = tx['meta']
        post = [b for b in meta.get('postTokenBalances', []) if b.get('mint') == mint and b.get('owner') == self.owner]
        indexes = {b['accountIndex'] for b in post}
        before = sum(int(b['uiTokenAmount']['amount']) for b in meta.get('preTokenBalances', [])
                     if b.get('mint') == mint and (b.get('owner') == self.owner or b['accountIndex'] in indexes))
        after = sum(int(b['uiTokenAmount']['amount']) for b in post)
        if after <= before:
            raise RemoteError('Quantité achetée non déterminée')
        if not hasattr(self, '_balances'):
            self._balances = {}
        self._balances[mint] = (after, time.monotonic())
        return after - before

    def transaction(self, signature):
        if not hasattr(self, '_transactions'):
            self._transactions = {}
        tx = self._transactions.get(signature)
        if tx is None:
            tx = self.rpc('getTransaction', [signature, {'encoding': 'jsonParsed',
                          'commitment': 'confirmed', 'maxSupportedTransactionVersion': 0}])
            if tx and tx.get('meta') and tx['meta'].get('err') is None:
                if len(self._transactions) >= 256:
                    self._transactions.pop(next(iter(self._transactions)))
                self._transactions[signature] = tx
        return tx

    def trade_details(self, job, side):
        """Read confirmed fills only. Market caps are estimates, never spot snapshots.

        Supply is observed after confirmation, not reconstructed at the historic slot.
        USD conversion uses the recorded SOL reference at buy preparation / TP trigger.
        """
        signature = job.get('signature') if side == 'buy' else job.get('exit_tx', {}).get('signature')
        if not signature:
            raise RemoteError('Transaction non enregistrée')
        tx = self.transaction(signature)
        if not tx or not tx.get('meta') or tx['meta'].get('err') is not None:
            raise RemoteError('Détails de transaction confirmée indisponibles')
        meta = tx['meta']
        pre, post = meta.get('preTokenBalances', []), meta.get('postTokenBalances', [])
        owned = {b['accountIndex'] for b in pre + post if b.get('owner') == self.owner}
        def balances(rows):
            return [b for b in rows if b.get('mint') == job['mint'] and b['accountIndex'] in owned]
        before, after = balances(pre), balances(post)
        change = sum(int(b['uiTokenAmount']['amount']) for b in after) - sum(int(b['uiTokenAmount']['amount']) for b in before)
        quantity = change if side == 'buy' else -change
        if quantity <= 0:
            raise RemoteError('Quantité exécutée non déterminée')
        decimals = {int(b['uiTokenAmount']['decimals']) for b in before + after}
        if len(decimals) != 1:
            raise RemoteError('Décimales du token incohérentes')
        if side == 'buy':
            usd = Decimal(str(job['cost_usd'])) if job.get('cost_usd') else None
            sol = Decimal(job['lamports']) / 10**9 if job.get('lamports') else None
        else:
            keys = [key.get('pubkey') if isinstance(key, dict) else key for key in tx['transaction']['message']['accountKeys']]
            owner_indexes = {i for i, key in enumerate(keys) if key == self.owner}
            if not owner_indexes:
                raise RemoteError('Solde SOL de la vente non déterminé')
            # Include retained/closed owned token-account rent so a rent refund is
            # not incorrectly counted as swap proceeds. Network fee is separated.
            indexes = owner_indexes | owned
            received = sum(int(meta['postBalances'][i]) - int(meta['preBalances'][i]) for i in indexes)
            if keys[0] == self.owner:
                received += int(meta['fee'])
            if received <= 0:
                raise RemoteError('Produit SOL de la vente non déterminé')
            sol = Decimal(received) / 10**9
            usd = sol * Decimal(str(job['exit_sol_usd'])) if job.get('exit_sol_usd') else None
        if not hasattr(self, '_supplies'):
            self._supplies = {}
        cached = self._supplies.get(job['mint'])
        if not cached or time.monotonic() - cached[0] >= 3600:
            supply = self.rpc('getTokenSupply', [job['mint'], {'commitment': 'confirmed'}])
            cached = (time.monotonic(), supply, time.time())
            self._supplies[job['mint']] = cached
        supply = cached[1]
        supply_amount = int(supply['value']['amount'])
        if int(supply['value']['decimals']) != next(iter(decimals)) or supply_amount <= 0:
            raise RemoteError('Offre du token invalide')
        if usd is not None and (not usd.is_finite() or usd <= 0):
            raise RemoteError('Référence USD indisponible')
        return dict(mc_estimate=str(usd * supply_amount / quantity) if usd is not None else None,
                    usd_estimate=str(usd) if usd is not None else None,
                    sol_amount=str(sol) if sol is not None else None,
                    token_raw=str(quantity), token_decimals=next(iter(decimals)),
                    block_time=tx.get('blockTime'), slot=tx.get('slot'),
                    supply_raw=str(supply_amount), supply_slot=supply.get('context', {}).get('slot'),
                    supply_observed_at=cached[2])

    def token_amount(self, mint, fresh=False):
        if not hasattr(self, '_balances'):
            self._balances = {}
        cached = self._balances.get(mint)
        if not fresh and cached and time.monotonic() - cached[1] < 15:
            return cached[0]
        result = self.rpc('getTokenAccountsByOwner', [self.owner, {'mint': mint},
                          {'encoding': 'jsonParsed', 'commitment': 'confirmed'}])
        amount = sum(int(a['account']['data']['parsed']['info']['tokenAmount']['amount']) for a in result['value'])
        self._balances[mint] = (amount, time.monotonic())
        return amount

    def exit_quote(self, mint, amount):
        quote = self.jupiter(f'/swap/v1/quote?inputMint={mint}&outputMint={SOL}&amount={amount}&slippageBps={self.args.slippage_bps}')
        if quote.get('inputMint') != mint or quote.get('outputMint') != SOL or int(quote.get('inAmount', 0)) != amount:
            raise RemoteError('Cotation de vente incompatible avec la position')
        if int(quote.get('otherAmountThreshold', 0)) <= 0:
            raise RemoteError('Minimum de sortie de la vente absent')
        return quote

    def status(self, signature):
        return self.rpc('getSignatureStatuses', [[signature], {'searchTransactionHistory': True}])['value'][0]

    def broadcast(self, raw):
        return self.rpc('sendTransaction', [raw, {'encoding': 'base64', 'skipPreflight': False, 'maxRetries': 2}])

    def expired(self, height):
        return self.rpc('getBlockHeight', [{'commitment': 'confirmed'}]) > height

    def holds(self, mint):
        return self.token_amount(mint, fresh=True) > 0

    def already_posted(self, job):
        for c in self.pump.callouts(self.owner):
            if c.get('coinMint') == job['mint'] and c.get('thesis') == job['text'] and timestamp(c['createdAt']) >= job['created']:
                job['own_callout_id'] = c.get('calloutId')
                return True
        return False

    def reply_exists(self, callout_id, update):
        rows = self.pump.get(f'/callout/{callout_id}/replies?filter=AUTHOR_ONLY&sort=RECENCY&limit=100').get('replies', [])
        return any(r.get('content') == update['text'] and timestamp(r['createdAt']) >= update['created'] for r in rows if r.get('createdAt'))


class Engine:
    def __init__(self, journal, execution, args):
        self.journal, self.execution, self.args = journal, execution, args

    def manage_updates(self, now, entry_pending=None):
        if now < getattr(self, 'next_update_post', 0) or (entry_pending and entry_pending()):
            return
        jobs = {j['mint']: j for j in self.journal.jobs()}
        if any(j['state'] == 'signed' or j.get('exit_state') == 'signed' for j in jobs.values()):
            return
        for update in self.journal.updates():
            job = jobs.get(update['mint'])
            if not job or job.get('publication_disabled') or job.get('source_author') not in CALLERS:
                continue
            if update['state'] not in ('new', 'publishing', 'uncertain') or update.get('retry_at', 0) > now or now < update['due'] or job['state'] != 'posted':
                continue
            try:
                if not self.args.live:
                    update['state'] = 'simulated'
                    self.journal.save_update(update)
                    continue
                if not job.get('own_callout_id'):
                    self.execution.already_posted(job)
                    self.journal.save(job)
                    if not job.get('own_callout_id'):
                        update['retry_at'] = now + 15
                        self.journal.save_update(update)
                        return
                if update['state'] in ('publishing', 'uncertain'):
                    if self.execution.reply_exists(job['own_callout_id'], update):
                        update['state'] = 'posted'
                    elif now - update['post_started'] > 120:
                        update['state'] = 'review'
                    update['retry_at'] = now + 10
                    self.journal.save_update(update)
                    return
                if job.get('exit_state') == 'review':
                    update['retry_at'] = now + 60
                    self.journal.save_update(update)
                    return
                closed = job.get('exit_state') in ('sold', 'empty')
                if closed and not update.get('prepared_closed'):
                    update['text'] = local_text(update['source'], 'update', True)
                self.execution.check_auth()
                update.update(state='publishing', post_started=now)
                self.journal.save_update(update)
                self.next_update_post = now + 6.1  # The replies route is limited to 10/min.
                status, body = self.execution.pump_call({'content': update['text']}, image=update, reply_to=job['own_callout_id'])
                if 200 <= status < 300:
                    update['state'] = 'posted'
                    update['published_at'] = now
                    print(f'UPDATE PUBLIÉE : {job["caller"]} → {job["mint"]}', flush=True)
                elif status in (401, 403, 429):
                    update.update(state='new', retry_at=now+30)
                elif status == 400 and update.get('image_path') and any(s in body for s in ('MEDIA_REJECTED', 'INVALID_IMAGE', 'UNSUPPORTED_MEDIA')):
                    update.pop('image_path', None)
                    update.update(state='new', retry_at=now+1, image_rejected=True)
                elif 400 <= status < 500:
                    update['state'] = 'failed'
                else:
                    update.update(state='uncertain', retry_at=now+10)
                self.journal.save_update(update)
                return
            except Exception as exc:
                update['retry_at'] = now + max(10, (exc.retry_after or 0) if isinstance(exc, RemoteError) else 0)
                self.journal.save_update(update)
                print('Update différée : ' + (str(exc) if isinstance(exc, RemoteError) else type(exc).__name__), flush=True)
                return

    def collect_details(self, now, entry_pending=None):
        # One read-only enrichment per turn, after orders/TPs; never on entry's
        # critical path. The existing provider pacer and 429 cooldown still apply.
        if not self.args.live or now < getattr(self, 'details_next_at', 0) or (entry_pending and entry_pending()):
            return
        jobs = self.journal.jobs()
        if any(j['state'] in ('new', 'signed') or j.get('exit_state') == 'signed' for j in jobs):
            return  # Never put reporting requests between sending and confirmation.
        for job in sorted(jobs, key=lambda j: j.get('created', 0), reverse=True):
            confirmed = job.get('buy_confirmed') or job['state'] in ('confirmed', 'publishing', 'uncertain', 'posted')
            if not confirmed:
                continue
            for side in ('buy', 'sell'):
                if side == 'sell' and job.get('exit_state') != 'sold':
                    continue
                key = side + '_details'
                if key in job or job.get(key + '_retry_at', 0) > now:
                    continue
                self.details_next_at = now + 5
                try:
                    job[key] = self.execution.trade_details(job, side)
                    job.pop(key + '_error', None)
                except Exception as exc:
                    job[key + '_error'] = str(exc) if isinstance(exc, RemoteError) else 'Détails indisponibles'
                    job[key + '_retry_at'] = time.time() + max(300, (exc.retry_after or 0) if isinstance(exc, RemoteError) else 0)
                self.journal.save(job)
                return

    def step(self, now, entry_pending=None):
        tick_started = time.monotonic()
        # Work already waiting in the queue must not sit behind every historical TP quote.
        # A single worker still serializes all wallet operations and provider quotas.
        jobs = sorted(self.journal.jobs(), key=lambda j: 0 if j.get('exit_state') == 'signed'
                      else 1 if j['state'] in ('new', 'signed') else 2)
        for job in jobs:
            # Yield between background jobs if a fresh callout arrived while a
            # remote request was in flight. Never interrupt an ongoing transaction.
            if (job['state'] not in ('new', 'signed') and job.get('exit_state') != 'signed'
                    and entry_pending and entry_pending()):
                break
            job_now = now + time.monotonic() - tick_started
            if self.args.live and getattr(self.args, 'take_profit', None) is not None:
                try:
                    self.manage_exit(job, job_now)
                except Exception as exc:
                    detail = str(exc) if isinstance(exc, RemoteError) else type(exc).__name__
                    job['tp_error'] = detail
                    job['tp_error_at'] = now + time.monotonic() - tick_started
                    print(f'TP {job["mint"]} différé : {detail}', flush=True)
                    job['exit_retry_at'] = job['tp_error_at'] + max(getattr(self.args, 'tp_interval', 5), (exc.retry_after or 0) if isinstance(exc, RemoteError) else 0)
                    self.journal.save(job)
            if job.get('exit_state') in ('signed', 'sold', 'empty', 'review'):
                continue
            if job['state'] in ('posted', 'simulated', 'failed', 'review') or job.get('retry_at', 0) > job_now:
                continue
            try:
                self.advance(job, job_now)
            except Exception as exc:
                # Never print responses, headers, private keys or arbitrary exception text.
                detail = str(exc) if isinstance(exc, RemoteError) else type(exc).__name__
                job['last_error'] = detail
                job['retry_at'] = now + (time.monotonic() - tick_started) + max(10, (exc.retry_after or 0) if isinstance(exc, RemoteError) else 0)
                if job['state'] == 'new' and job['retry_at'] > job['created'] + self.args.max_age:
                    self.expire_entry(job)
                else:
                    wait = max(0, job['retry_at'] - (now + time.monotonic() - tick_started))
                    print(f'{job["caller"]} : étape {job["state"]} différée ({detail}) ; nouvelle tentative planifiée dans {wait:.0f}s', flush=True)
                self.journal.save(job)

    def manage_exit(self, job, now):
        if job.get('exit_state') in ('sold', 'empty', 'review') or job.get('exit_retry_at', 0) > now:
            return
        if job.get('exit_state') == 'signed':
            tx = job['exit_tx']
            status = self.execution.status(tx['signature'])
            job.pop('tp_error', None)
            if status and status.get('err') is not None:
                # A conclusively failed sell can be prepared again; keep tracking the holding.
                job.update(exit_state='watching', exit_retry_at=now + self.args.tp_interval)
            elif status and status.get('confirmationStatus') in ('confirmed', 'finalized'):
                job.update(exit_state='sold', sold_at=now)
                print(f'{"SL" if job.get("exit_reason") == "sl" else "TP"} CONFIRMÉ : 100 % de la position vendue, {job["mint"]}', flush=True)
            elif status:
                job['exit_retry_at'] = now + 1
            elif self.execution.expired(tx['last_valid_height']):
                job['exit_state'] = 'review'
                print(f'Vente à vérifier : {tx["signature"]}. Aucune nouvelle vente automatique.', flush=True)
            else:
                self.execution.broadcast(tx['raw'])
                job['exit_retry_at'] = now + 2
            self.journal.save(job)
            return
        confirmed = job.get('buy_confirmed') or job['state'] in ('confirmed', 'publishing', 'uncertain', 'posted')
        if not confirmed:
            return
        if not job.get('cost_usd'):
            if not job.get('tp_missing_basis'):
                print(f'TP non activé pour l’ancien achat {job["mint"]} : coût USD non enregistré.', flush=True)
                job['tp_missing_basis'] = True
                self.journal.save(job)
            return
        if not job.get('position_tokens'):
            job['position_tokens'] = self.execution.acquired(job['signature'], job['mint'])
            self.journal.save(job)
            print(f'TP actif +{self.args.take_profit}% : {job["mint"]}', flush=True)
        amount = min(job['position_tokens'], self.execution.token_amount(job['mint']))
        if amount <= 0:
            job.pop('tp_error', None)
            job['exit_state'] = 'empty'
        else:
            quote = self.execution.exit_quote(job['mint'], amount)
            sol_usd = Decimal(str(self.execution.sol_price()))
            value = Decimal(quote['otherAmountThreshold']) / 10**9 * sol_usd
            cost = Decimal(job['cost_usd']) * Decimal(amount) / Decimal(job['position_tokens'])
            job['mark_cost_usd'] = str(cost)
            job.pop('tp_error', None)
            job.update(mark_usd=str(value), mark_checked_at=now)
            target = cost * (1 + self.args.take_profit / 100)
            # SL uses the quoted expected output, not the slippage-adjusted floor:
            # a wide tolerance must not trigger a stop before the market reaches it.
            expected = Decimal(quote.get('outAmount', quote['otherAmountThreshold'])) / 10**9 * sol_usd
            stop = job.get('stop_loss')
            stopped = stop is not None and expected <= cost * (1 - Decimal(str(stop)) / 100)
            if value >= target or stopped:
                if self.execution.token_amount(job['mint'], fresh=True) < amount:
                    job['exit_retry_at'] = now + self.args.tp_interval
                    self.journal.save(job)
                    return
                # Persist before broadcasting, just as for the buy; never sell twice after a timeout.
                job['exit_tx'] = self.execution.sign_quote(quote)
                job.update(exit_state='signed', exit_tokens=amount, exit_trigger_usd=str(value), exit_sol_usd=str(sol_usd), exit_reason='sl' if stopped else 'tp')
                print(f'{"SL" if stopped else "TP"} déclenché : vente totale de la position {job["mint"]}', flush=True)
            else:
                job['exit_state'] = 'watching'
        job['exit_retry_at'] = now + (0 if job.get('exit_state') == 'signed' else self.args.tp_interval)
        self.journal.save(job)

    def expire_entry(self, job):
        job.update(state='failed', failure_reason='entry_expired')
        print(f'ACHAT ABANDONNÉ : {job["caller"]} → {job["mint"]} ; limite de {self.args.max_age:g}s dépassée avant préparation.', flush=True)
        if job.get('last_error'):
            print(f'  Dernière erreur : {job["last_error"]}', flush=True)

    def advance(self, job, now):
        if job['state'] == 'new':
            if now < job.get('buy_at', 0):
                return
            if now - job['created'] > self.args.max_age:
                self.expire_entry(job)
            elif not self.args.live:
                print(f'SIMULATION : achat {self.args.usd} USD pour {job["caller"]}, publication au plus tôt à {job["due"]:.0f} : {job["text"]} ; TP prévu +{getattr(self.args, "take_profit", 50)}%, vente totale', flush=True)
                job['state'] = 'simulated'
            else:
                # Preparation never broadcasts. Persist the signature BEFORE any send.
                job['attempts'] = job.get('attempts', 0) + 1
                self.journal.save(job)
                print(f'PRÉPARATION ACHAT : {job["caller"]}, tentative {job["attempts"]}', flush=True)
                started = time.monotonic()
                job.update(self.execution.prepare(job['mint']), state='signed')
                elapsed = time.monotonic() - started
                job['prepare_ms'] = round(elapsed * 1000)
                # Commit signed bytes before sending, including on the first attempt.
                # No status/height lookup is needed for a transaction just constructed.
                self.journal.save(job)
                self.send_buy(job, now + elapsed)
            self.journal.save(job)
            return
        if job['state'] == 'signed':
            status_started = time.monotonic()
            status = self.execution.status(job['signature'])
            now += time.monotonic() - status_started
            if status and status.get('err') is not None:
                job['state'] = 'failed'
            elif status and status.get('confirmationStatus') in ('confirmed', 'finalized'):
                job['state'] = 'confirmed'
                job['buy_confirmed'] = True
                job.setdefault('confirmed_at', now)
                print(f'Achat confirmé : {job["mint"]}', flush=True)
                if job.get('first_send_at') is not None:
                    print(f'LATENCE CONFIRMATION : {max(0, now - job["first_send_at"]):.2f}s depuis le premier envoi (confirmation observée par le bot).', flush=True)
                self.journal.save(job)
                if not job.get('publication_disabled') and now >= job['due']:
                    self.advance(job, now)
                return
            elif status:
                # Already processed: wait for confirmation, never replace this transaction.
                job['retry_at'] = now + 1
            elif self.execution.expired(job['last_valid_height']):
                job['state'] = 'review'  # Unknown history must never trigger a fresh purchase.
                print(f'Transaction expirée/non retrouvée : vérifier {job["signature"]}', flush=True)
            else:
                # All retries use the same signed transaction and therefore the same signature.
                self.send_buy(job, now)
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
            if job.get('publication_disabled'):
                return
            if not self.execution.holds(job['mint']):
                job['retry_at'] = now + 3
                self.journal.save(job)
                return
            self.execution.check_auth()
            job.update(state='publishing', post_started=now)
            self.journal.save(job)
            # If this call times out/crashes, next step reconciles rather than posts twice.
            fields = {'coinMint': job['mint'], 'thesis': job['text']}
            status, body = (self.execution.pump_call(fields, image=job) if job.get('image_path') else self.execution.pump_call(fields))
            if 200 <= status < 300:
                job['state'] = 'posted'
                try:
                    payload = json.loads(body)
                    job['own_callout_id'] = payload.get('callout', {}).get('calloutId') or payload.get('calloutId')
                except (ValueError, TypeError, AttributeError):
                    pass
                print(f'CALLOUT PUBLIÉ : {job["caller"]} → {job["mint"]}', flush=True)
            elif status == 400 and job.get('image_path') and any(s in body for s in ('MEDIA_REJECTED', 'INVALID_IMAGE', 'UNSUPPORTED_MEDIA')):
                job.pop('image_path', None)
                job.update(state='confirmed', retry_at=now+1, image_rejected=True)
                print('Image refusée : nouvelle tentative de publication du texte seul, sans racheter.', flush=True)
            elif status in (401, 403, 429) or (400 <= status < 500 and 'INSUFFICIENT_BALANCE' in body):
                job.update(state='confirmed', retry_at=now + 30)
                print(f'Publication refusée HTTP {status} ; achat conservé, nouvelle tentative sans racheter.', flush=True)
            elif 400 <= status < 500:
                job['state'] = 'failed'
                print(f'Publication refusée HTTP {status} : {job["mint"]}', flush=True)
            else:
                job['state'] = 'uncertain'
            self.journal.save(job)

    def send_buy(self, job, now):
        first = 'first_send_at' not in job
        if first:
            job['first_send_at'] = now
            job['detect_to_send_ms'] = round(max(0, now - job.get('detected_at', now)) * 1000)
            job['callout_to_send_ms'] = round(max(0, now - job['created']) * 1000)
        # Persist the attempt even if the RPC response is lost. Recovery always checks
        # this exact signature; it never reconstructs the purchase after an ambiguous send.
        self.journal.save(job)
        if first:
            stages = ', '.join(f'{key}={value}ms' for key, value in job.get('prepare_timings_ms', {}).items())
            print(f'LATENCE ENTRÉE : {job["caller"]} ; détection→envoi={job["detect_to_send_ms"]}ms ; '
                  f'callout→envoi≈{job["callout_to_send_ms"]}ms ; préparation={job.get("prepare_ms", 0)}ms'
                  + (f' ({stages}, attentes de quota incluses)' if stages else ''), flush=True)
        started = time.monotonic()
        self.execution.broadcast(job['raw'])
        elapsed = time.monotonic() - started
        job['broadcast_ms'] = round(elapsed * 1000)
        job['retry_at'] = now + elapsed + (0 if first else 2)
        self.journal.save(job)
        print(f'DIFFUSION ACHAT ACCEPTÉE : {job["mint"]} ; réponse RPC en {job["broadcast_ms"]}ms, confirmation en attente.', flush=True)


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
                            events.put(dict(call, _detected_at=time.time()))
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
    parser.add_argument('--callers', help='Noms ou wallets des callers configurés à activer, séparés par des virgules')
    parser.add_argument('--check-swap', metavar='MINT', help='Tester une cotation et une construction non signée, sans achat')
    parser.add_argument('--status', action='store_true', help='Afficher le journal réel en lecture seule, sans connexion ni achat')
    parser.add_argument('--profile', default='Lelouch_0x')
    parser.add_argument('--live', action='store_true', help='Effectuer réellement les achats et publications')
    parser.add_argument('--usd', type=Decimal, default=Decimal('2'))
    parser.add_argument('--delay', type=float, default=0, help='Délai minimal supplémentaire de publication depuis le callout (défaut : 0)')
    parser.add_argument('--buy-delay', type=float, default=12, help='Attendre ce délai depuis le callout avant de préparer l’achat')
    parser.add_argument('--update-delay', type=float, default=10, help='Délai minimal depuis une update du caller avant sa republication')
    parser.add_argument('--rewrite-mode', choices=('local', 'ai'), default='local')
    parser.add_argument('--stop-loss', type=Decimal, default=Decimal('50'), help='Perte en %% déclenchant la vente totale (défaut : 50)')
    parser.add_argument('--max-age', type=float, default=60, help='Ne pas acheter un callout découvert trop tard')
    parser.add_argument('--take-profit', type=Decimal, default=Decimal('50'), help='Gain en %% du coût USD déclenchant une vente totale (défaut : 50)')
    parser.add_argument('--tp-interval', type=float, default=5, help='Intervalle de contrôle du TP par position, en secondes')
    parser.add_argument('--slippage-bps', type=int, default=100)
    parser.add_argument('--wallet', default=str(ROOT / 'data' / 'pump_wallet.txt'))
    parser.add_argument('--token', default=str(ROOT / 'data' / 'pump_token.txt'))
    parser.add_argument('--rpc', default=os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com'))
    parser.add_argument('--rpc-rps', type=float, default=2, help='Plafond local des requêtes RPC par seconde')
    parser.add_argument('--jupiter-rps', type=float, default=.5, help='Plafond Jupiter par seconde (défaut : 0,5, soit une requête toutes les 2 s)')
    parser.add_argument('--jupiter', default='https://api.jup.ag')
    parser.add_argument('--text', default=None, help='Remplacer le choix aléatoire : {text} et {caller} sont remplacés')
    parser.add_argument('--state')
    if configure:
        configure(parser)
    args = parser.parse_args(argv)
    if not args.usd.is_finite() or args.usd <= 0 or not math.isfinite(args.delay) or args.delay < 0 or not math.isfinite(args.max_age) or args.max_age <= 0 or not 1 <= args.slippage_bps <= 5000:
        parser.error('Montant/délais/slippage invalides')
    if any(not math.isfinite(rate) or rate <= 0 for rate in (args.rpc_rps, args.jupiter_rps)):
        parser.error('Les plafonds RPC et Jupiter doivent être positifs')
    if not args.take_profit.is_finite() or args.take_profit <= 0 or not math.isfinite(args.tp_interval) or args.tp_interval < 1:
        parser.error('TP et intervalle de contrôle invalides')
    if not args.stop_loss.is_finite() or not 0 < args.stop_loss < 100 or any(not math.isfinite(n) or n < 0 for n in (args.buy_delay, args.update_delay)):
        parser.error('Stop loss ou délais invalides')
    try:
        if args.text is not None:
            args.text.format(text='exemple', caller='exemple')
    except (KeyError, ValueError, AttributeError, IndexError):
        parser.error('--text accepte seulement {text} et {caller}')
    if args.live and not (args.status or args.check_swap) and any(not Path(p).is_file() for p in (args.wallet, args.token)):
        parser.error('Créer les fichiers locaux --wallet et --token (voir CALLOUT_REPOST.md)')
    if args.callers is not None:
        requested = {name.strip().lower() for name in args.callers.split(',') if name.strip()}
        selected = {wallet: name for wallet, name in CALLERS.items() if name.lower() in requested or wallet.lower() in requested}
        if not selected or len(selected) != len(requested):
            parser.error('--callers doit sélectionner des noms ou wallets configurés, sans entrée inconnue')
        CALLERS.clear()
        CALLERS.update(selected)
    return args


def main():
    args = parse_args()
    if args.status:
        show_status(args.state or str(ROOT / 'data' / 'repost_live.sqlite3'))
        return
    if args.check_swap:
        check_swap(args)
        return
    with RunnerLock(ROOT / 'data' / '.callout-runner.lock'):
        run_repost(args)


def run_repost(args):
    execution = Execution(args)
    path = args.state or str(ROOT / 'data' / ('repost_live.sqlite3' if args.live else 'repost_simulation.sqlite3'))
    journal = Journal(path, time.time(), execution.owner)
    journal.apply_policy()
    engine = Engine(journal, execution, args)
    events, stop = queue.Queue(), threading.Event()
    from callout_workflow import Workflow
    workflow = Workflow(journal, args, events, stop)
    thread = threading.Thread(target=detect, args=(events, stop, journal.since), daemon=True)
    print(f'{"RÉEL" if args.live else "SIMULATION"} : {len(CALLERS)} callers, {args.usd} USD/token, achat après {args.buy_delay}s. Profil {args.profile}', flush=True)
    thread.start()
    try:
        while True:
            try:
                call = events.get(timeout=.5)
                if call.get('_kind') == 'update':
                    journal.add_update(call, time.time(), args.update_delay)
                else:
                    journal.add(call, time.time(), args.max_age, args.delay, args.text, buy_delay=args.buy_delay, stop_loss=args.stop_loss)
            except queue.Empty:
                pass
            workflow.tick(time.time())
            engine.step(time.time(), entry_pending=lambda: not events.empty())
            engine.manage_updates(time.time(), entry_pending=lambda: not events.empty())
            engine.collect_details(time.time(), entry_pending=lambda: not events.empty())
    finally:
        stop.set()
        workflow.close()
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
