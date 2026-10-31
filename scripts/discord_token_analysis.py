"""Read-only Solana wallet snapshots and public pump.fun callout counts."""
from collections import defaultdict
from decimal import Decimal
import html
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from curl_cffi import requests


class RemoteError(RuntimeError):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10**11 else float(value)
    if isinstance(value, str):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('Fuseau horaire requis')
        return result.timestamp()
    raise ValueError('Horodatage absent')


class Pump:
    def __init__(self, mode='browser'):
        self.http = requests.Session(impersonate='chrome')
        self.next_at, self.pause_until = 0, 0

    def get(self, path):
        if not path.startswith(('/coins-v2/', '/callout/coin/', '/mint-positions/')):
            raise ValueError('Lecture pump.fun non autorisée')
        if time.monotonic() < self.pause_until:
            raise RemoteError('pump.fun : fournisseur temporairement indisponible')
        time.sleep(max(0, self.next_at - time.monotonic()))
        self.next_at = time.monotonic() + 1.1
        try:
            response = self.http.get('https://frontend-api-v3.pump.fun' + path,
                                     headers={'Origin': 'https://pump.fun', 'Referer': 'https://pump.fun/',
                                              'Accept': 'application/json'}, timeout=15)
            if response.status_code != 200:
                if response.status_code in (403, 429):
                    self.pause_until = time.monotonic() + 120
                raise RemoteError(f'pump.fun : HTTP {response.status_code}')
            return response.json()
        except RemoteError:
            raise
        except Exception:
            raise RemoteError('pump.fun : réponse indisponible') from None

WATCHED = {
    'DECU': '4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9',
    'LE L': 'CkPFGv2Wv1vwdWjtXioEgb8jhZQfs3eVZez3QCetu7xD',
}
SOL = 'So11111111111111111111111111111111111111112'
QUOTES = {SOL: 'SOL', 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v': 'USDC',
          'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB': 'USDT'}
BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
ADDRESS = re.compile(r'(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])')
TRADE_LOG = re.compile(r'Instruction: (?:Buy\w*|Sell\w*|\w*Swap\w*|\w*Route\w*)', re.I)


def valid_address(value):
    if not isinstance(value, str) or not ADDRESS.fullmatch(value):
        return False
    n = 0
    for char in value:
        n = n * 58 + BASE58.index(char)
    size = (n.bit_length() + 7) // 8 + len(value) - len(value.lstrip('1'))
    return size == 32


def candidates(text):
    skip = set(WATCHED.values()) | set(QUOTES)
    return list(dict.fromkeys(v for v in ADDRESS.findall(text) if v not in skip and valid_address(v)))[:8]


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def paris(value):
    return datetime.fromtimestamp(value, ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M:%S')


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cursor (source TEXT PRIMARY KEY, message_id TEXT);
            CREATE TABLE IF NOT EXISTS signals (mint TEXT PRIMARY KEY, source TEXT, message_id TEXT,
                signal_at REAL, state TEXT, result TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS transactions (signature TEXT PRIMARY KEY, data TEXT);
        ''')
        self.db.commit()

    def cursor(self, source):
        row = self.db.execute('SELECT message_id FROM cursor WHERE source=?', (source,)).fetchone()
        return row[0] if row else None

    def advance(self, source, message_id):
        self.db.execute('INSERT OR REPLACE INTO cursor VALUES (?,?)', (source, str(message_id)))
        self.db.commit()

    def enqueue(self, mint, source, message_id, signal_at):
        if not valid_address(mint):
            return False
        change = self.db.execute('INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,NULL,NULL)',
                                 (mint, source, str(message_id), signal_at, 'pending')).rowcount
        self.db.commit()
        return bool(change)

    def next(self):
        row = self.db.execute("SELECT mint,source,message_id,signal_at FROM signals WHERE state='pending' ORDER BY signal_at LIMIT 1").fetchone()
        if row:
            self.db.execute("UPDATE signals SET state='running' WHERE mint=?", (row[0],))
            self.db.commit()
            return dict(zip(('mint', 'source', 'message_id', 'signal_at'), row))

    def finish(self, mint, result=None, error=None):
        self.db.execute('UPDATE signals SET state=?,result=?,error=? WHERE mint=?',
                        ('done' if result else 'error', json.dumps(result, ensure_ascii=False) if result else None, error, mint))
        self.db.commit()

    def recover(self):
        self.db.execute("UPDATE signals SET state='pending' WHERE state='running'")
        self.db.commit()

    def retry_errors(self):
        self.db.execute("UPDATE signals SET state='pending',error=NULL WHERE state='error'")
        for mint, data in self.db.execute("SELECT mint,result FROM signals WHERE state='done'").fetchall():
            result = json.loads(data)
            incomplete = (result.get('metadata_error') or result.get('callouts', {}).get('errors') or result.get('callouts', {}).get('count') is None
                          or any(not w.get('available') or w.get('history_limited') for w in result.get('wallets', {}).values()))
            if incomplete:
                self.db.execute("UPDATE signals SET state='pending' WHERE mint=?", (mint,))
        self.db.commit()


class ReadRPC:
    def __init__(self, url, store, rps=2):
        self.url, self.store, self.interval = url, store, 1 / rps
        self.http = requests.Session(impersonate='chrome')
        self.next_at = 0

    def call(self, method, params):
        if method not in ('getSignaturesForAddress', 'getTransaction'):
            raise ValueError('RPC autorisé en lecture seule uniquement')
        time.sleep(max(0, self.next_at - time.monotonic()))
        self.next_at = time.monotonic() + self.interval
        try:
            response = self.http.post(self.url, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, timeout=15)
            if response.status_code != 200:
                raise RemoteError(f'RPC {method} : HTTP {response.status_code}')
            data = response.json()
            if data.get('error') is not None:
                raise RemoteError(f'RPC {method} : requête refusée')
            return data['result']
        except RemoteError:
            raise
        except Exception:
            raise RemoteError(f'RPC {method} : réponse indisponible') from None

    def transaction(self, signature):
        row = self.store.db.execute('SELECT data FROM transactions WHERE signature=?', (signature,)).fetchone()
        if row:
            return json.loads(row[0])
        result = self.call('getTransaction', [signature, {'encoding': 'jsonParsed', 'commitment': 'confirmed',
                                                         'maxSupportedTransactionVersion': 0}])
        if result:
            self.store.db.execute('INSERT OR REPLACE INTO transactions VALUES (?,?)', (signature, json.dumps(result)))
            self.store.db.commit()
        return result


def parse_trade(tx, wallet, mint, signature):
    """Require a signed swap with an economic counterpart; transfers remain separate."""
    meta = tx.get('meta') or {}
    if meta.get('err') is not None or not meta:
        return None
    keys = tx.get('transaction', {}).get('message', {}).get('accountKeys', [])
    addresses = [k.get('pubkey') if isinstance(k, dict) else k for k in keys]
    signer = any(isinstance(k, dict) and k.get('pubkey') == wallet and k.get('signer') for k in keys)
    owned_indexes = {b['accountIndex'] for side in ('preTokenBalances', 'postTokenBalances')
                     for b in meta.get(side, []) if b.get('owner') == wallet}
    def balance(side, target):
        return sum(int(b['uiTokenAmount']['amount']) for b in meta.get(side, [])
                   if b.get('mint') == target and (b.get('owner') == wallet or b['accountIndex'] in owned_indexes))
    delta = balance('postTokenBalances', mint) - balance('preTokenBalances', mint)
    if not delta:
        return None
    decimals = next(b['uiTokenAmount']['decimals'] for side in ('postTokenBalances', 'preTokenBalances')
                    for b in meta.get(side, []) if b.get('mint') == mint and b['accountIndex'] in owned_indexes)
    quote_delta = {}
    assets = {b['mint']: b['uiTokenAmount']['decimals'] for side in ('preTokenBalances', 'postTokenBalances')
              for b in meta.get(side, []) if b['accountIndex'] in owned_indexes}
    for asset, qdec in assets.items():
        if asset in (SOL, mint):
            continue
        raw = balance('postTokenBalances', asset) - balance('preTokenBalances', asset)
        if raw:
            quote_delta[QUOTES.get(asset, asset)] = Decimal(raw) / 10 ** qdec
    if wallet in addresses:
        index = addresses.index(wallet)
        native = meta['postBalances'][index] - meta['preBalances'][index]
        # Rent paid/recovered and existing wrapped SOL balances are accounted for.
        native += sum(meta['postBalances'][i] - meta['preBalances'][i] for i in owned_indexes if i != index)
        if addresses[0] == wallet:
            native += meta.get('fee', 0)
        quote_delta['SOL'] = Decimal(native) / 10**9
    direction = -1 if delta > 0 else 1
    quotes = {k: str(abs(v)) for k, v in quote_delta.items() if v * direction > Decimal('0.000001')}
    is_swap = signer and bool(TRADE_LOG.search('\n'.join(meta.get('logMessages') or []))) and bool(quotes)
    kind = ('buy' if delta > 0 else 'sell') if is_swap else ('transfer_in_or_unknown' if delta > 0 else 'transfer_out_or_unknown')
    return {'signature': signature, 'at': tx.get('blockTime'), 'slot': tx.get('slot'),
            'transaction_index': tx.get('transactionIndex'), 'kind': kind,
            'raw': abs(delta), 'decimals': decimals, 'quantity': str(Decimal(abs(delta)) / 10**decimals),
            'quotes': quotes if is_swap else {}}


def wallet_snapshot(rpc, wallet, mint, created, cutoff, max_signatures=300):
    created = int(created)  # Solana blockTime has one-second precision.
    signatures, before, boundary, scanned = [], None, False, 0
    while scanned < max_signatures:
        limit = min(100, max_signatures - scanned)
        options = {'limit': limit, 'commitment': 'confirmed'}
        if before:
            options['before'] = before
        page = rpc.call('getSignaturesForAddress', [wallet, options])
        scanned += len(page)
        if not page:
            boundary = True
            break
        older = False
        for row in page:
            at = row.get('blockTime')
            if at is not None and at < created:
                older = True
            if at is None or created <= at <= cutoff:
                signatures.append(row)
        before = page[-1]['signature']
        if older or len(page) < limit:
            boundary = True
            break
    trades, missing = [], 0
    for row in signatures:
        if row.get('err') is not None:
            continue
        tx = rpc.transaction(row['signature'])
        if not tx:
            missing += 1
            continue
        at = tx.get('blockTime')
        if at is None or not created <= at <= cutoff:
            missing += at is None
            continue
        trade = parse_trade(tx, wallet, mint, row['signature'])
        if trade:
            trades.append(trade)
    trades.sort(key=lambda t: (t['slot'] or 0, t['transaction_index'] if t['transaction_index'] is not None else 10**9, t['signature']))
    buys = [t for t in trades if t['kind'] == 'buy']
    sells = [t for t in trades if t['kind'] == 'sell']
    total_bought = sum(t['raw'] for t in buys)
    total_sold = sum(t['raw'] for t in sells)
    first = buys[0] if buys else None
    first_slot = [t for t in buys if t['slot'] == first['slot']] if first else []
    first_ambiguous = len(first_slot) > 1 and any(t['transaction_index'] is None for t in first_slot)
    totals = {kind: defaultdict(Decimal) for kind in ('buy', 'sell')}
    for t in buys + sells:
        for asset, amount in t['quotes'].items():
            totals[t['kind']][asset] += Decimal(amount)
    return {'wallet': wallet, 'available': True, 'history_boundary_reached': boundary,
            'history_limited': not boundary or missing > 0, 'transactions_missing': missing,
            'signatures_scanned': scanned, 'signatures_examined': len(signatures), 'buys': len(buys), 'sells': len(sells),
            'first_buy_order_ambiguous': first_ambiguous,
            'first_buy': first, 'bought_raw': str(total_bought), 'sold_raw': str(total_sold),
            'bought_quantity': str(Decimal(total_bought) / 10**first['decimals']) if first else None,
            'sold_quantity': str(Decimal(total_sold) / 10**first['decimals']) if first else None,
            'sold_percent_initial_buy': str(Decimal(total_sold) * 100 / first['raw']) if first and boundary and not missing and not first_ambiguous else None,
            'sold_percent_all_buys': str(Decimal(total_sold) * 100 / total_bought) if total_bought else None,
            'quote_totals': {kind: {a: str(v) for a, v in values.items()} for kind, values in totals.items()},
            'unclassified_movements': [t for t in trades if t['kind'].startswith('transfer_')],
            'trades': trades, 'same_second_as_signal': sum(t['at'] == int(cutoff) for t in trades)}


def count_callouts(pump, mint, created, cutoff, max_pages=40):
    found, coverage, errors = {}, {}, []
    def add(call, at, author=None, name=None):
        try:
            if call.get('coinMint', mint) != mint:
                return
            t = timestamp(at)
            identity = str(call['calloutId'])
            if created <= t <= cutoff:
                found[identity] = {'id': identity, 'at': t, 'wallet': author, 'caller': name}
        except (KeyError, TypeError, ValueError):
            pass
    # Include the legacy top list as an independent cross-check (capped at 100).
    try:
        top = pump.get(f'/callout/coin/{mint}?limit=100')
        if not isinstance(top, dict) or not isinstance(top.get('callouts'), list):
            raise RemoteError('Format des callouts inattendu')
        for call in top['callouts']:
            add(call, call.get('createdAt'), call.get('userId'))
    except RemoteError:
        errors.append('Liste des meilleurs callouts indisponible')
    for mode in ('LATEST', 'CLOSED_PNL'):
        coverage[mode] = {'pages': 0, 'rows': 0, 'exhausted': False}
        try:
            for page in range(max_pages):
                data = pump.get(f'/mint-positions/{mint}?sortBy={mode}&page={page}&pageSize=50&withThesis=true&updatesLimit=0')
                if not isinstance(data, dict) or not isinstance(data.get('positions'), list) or not isinstance(data.get('hasMore'), bool):
                    raise RemoteError('Format des positions inattendu')
                coverage[mode]['pages'] += 1
                coverage[mode]['rows'] += len(data['positions'])
                coverage[mode]['reported_total'] = data.get('totalCount')
                for position in data['positions']:
                    call = position.get('callout')
                    if call:
                        add(call, call.get('calloutTimestamp'), position.get('walletAddress'), position.get('userName'))
                if not data['hasMore']:
                    coverage[mode]['exhausted'] = True
                    break
        except RemoteError:
            errors.append(f'Pagination {mode} interrompue')
    available = bool(found) or any(v['exhausted'] for v in coverage.values())
    return {'count': len(found) if available else None, 'kind': 'public_lower_bound', 'coverage': coverage,
            'errors': errors, 'callouts': sorted(found.values(), key=lambda c: (c['at'], c['id'])),
            'note': 'Minimum public retrouvé : suppressions, modération, filtres et historique manquant peuvent réduire ce nombre. Réponses, updates et reposts exclus.'}


class Analyzer:
    def __init__(self, rpc, pump=None, max_signatures=300, max_pages=40):
        self.rpc, self.pump = rpc, pump or Pump('browser')
        self.max_signatures, self.max_pages = max_signatures, max_pages

    def analyze(self, signal):
        mint, cutoff = signal['mint'], signal['signal_at']
        metadata_error = None
        try:
            coin = self.pump.get('/coins-v2/' + mint)
            if not isinstance(coin, dict) or coin.get('mint') != mint or not coin.get('created_timestamp'):
                raise RemoteError('Création du token indisponible')
            created = timestamp(coin['created_timestamp'])
        except RemoteError as exc:
            # Public pump outages must not prevent independent on-chain wallet reads.
            coin, created, metadata_error = {}, None, str(exc)
        if created is not None and created > cutoff:
            raise RemoteError('Le signal précède la création du token : CA ou horodatage à vérifier')
        result = dict(signal, name=coin.get('name'), symbol=coin.get('symbol'), created_at=created,
                      analyzed_at=time.time(), metadata_error=metadata_error, wallets={})
        for name, wallet in WATCHED.items():
            try:
                result['wallets'][name] = wallet_snapshot(self.rpc, wallet, mint, created or 0, cutoff, self.max_signatures)
            except RemoteError as exc:
                result['wallets'][name] = {'wallet': wallet, 'available': False, 'reason': str(exc)}
        result['callouts'] = count_callouts(self.pump, mint, created or 0, cutoff, self.max_pages)
        return result


REPORT_LOCK = threading.Lock()


def write_reports(store, folder):
    with REPORT_LOCK:
        return _write_reports(store, folder)


def _write_reports(store, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    records = store.db.execute('SELECT mint,state,result,error,signal_at FROM signals ORDER BY signal_at DESC').fetchall()
    blocks = []
    esc = lambda value: html.escape(str(value if value is not None else '—'))
    for mint, state, data, error, at in records:
        body = f'<h2>{esc(mint)}</h2><p>Signal : {esc(paris(at))} · {esc(state)}</p>'
        if data:
            result = json.loads(data)
            path = folder / (mint + '.json')
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
            count = result['callouts']['count']
            born = paris(result['created_at']) if result.get('created_at') is not None else 'Indisponible'
            body += f'<h3>{esc(result.get("name"))} · ${esc(result.get("symbol"))}</h3><p>Création : {esc(born)} · Callouts retrouvés : <strong>{esc(count)}</strong> (minimum public).</p>'
            if result.get('metadata_error'):
                body += '<p class="warning">Métadonnées indisponibles : l’heure de création n’a pas été vérifiée ; les wallets restent consultés dans l’historique borné avant le signal.</p>'
            for name, snapshot in result['wallets'].items():
                body += f'<h3>{esc(name)}</h3>'
                if not snapshot.get('available'):
                    body += f'<p>Indisponible : {esc(snapshot.get("reason"))}</p>'
                    continue
                first = snapshot['first_buy']
                body += '<p>' + ('Achat retrouvé' if first else 'Aucun achat retrouvé dans l’historique couvert') + '</p>'
                if first:
                    body += f'<p>Premier achat retrouvé : {esc(first["quantity"])} tokens · {esc(first["quotes"])} · {esc(paris(first["at"]))}</p>'
                if snapshot['first_buy_order_ambiguous']:
                    body += '<p class="warning">Plusieurs achats dans le premier slot, ordre non établi : pourcentage sur la mise initiale indisponible.</p>'
                body += f'<p>Total achats : {esc(snapshot["quote_totals"]["buy"])} · Total ventes : {esc(snapshot["quote_totals"]["sell"])}</p>'
                body += f'<p>Quantités : {esc(snapshot["bought_quantity"])} tokens achetés · {esc(snapshot["sold_quantity"])} tokens vendus.</p>'
                body += f'<p>Quantité vendue / premier achat : <strong>{esc(snapshot["sold_percent_initial_buy"])} %</strong> · / tous les achats : {esc(snapshot["sold_percent_all_buys"])} %</p>'
                if snapshot['history_limited']:
                    body += '<p class="warning">Historique incomplet : achats, ventes et pourcentages peuvent être sous-estimés ou biaisés.</p>'
                if snapshot['unclassified_movements']:
                    body += '<p class="warning">Transferts ou mouvements non attribuables présents ; ils ne sont pas comptés comme des ventes.</p>'
                if snapshot['same_second_as_signal']:
                    body += '<p class="warning">Transaction dans la seconde du signal : ordre exact indéterminable avec les seules heures de bloc.</p>'
            body += f'<p><a href="{esc(mint)}.json">Données et transactions détaillées</a></p><p>{esc(result["callouts"]["note"])}</p>'
            if result['callouts']['errors']:
                body += '<p class="warning">' + esc('; '.join(result['callouts']['errors'])) + '</p>'
        elif error:
            body += f'<p class="warning">{esc(error)}</p>'
        blocks.append('<article>' + body + '</article>')
    page = '''<!doctype html><html lang="fr"><meta charset="utf-8"><meta http-equiv="refresh" content="10"><title>Discord Solana · DECU / LE L</title><style>body{background:#101416;color:#e6ece8;font:15px system-ui;max-width:1100px;margin:32px auto;padding:16px}article{border:1px solid #39453d;padding:20px;margin:20px 0;border-radius:12px}h2{font-size:15px;overflow-wrap:anywhere}a{color:#b5e578}.warning{color:#ffb985}p{overflow-wrap:anywhere}</style><h1>Signaux Discord · DECU / LE L</h1><p>Lecture seule. Bilans arrêtés à l’heure du message Discord, pas à l’heure d’ouverture de cette page. Aucun achat, vente ou message envoyé. Actualisation toutes les 10 secondes.</p>'''
    target = folder / 'index.html'
    temp = folder / 'index.tmp'
    temp.write_text(page + ''.join(blocks) + '</html>', encoding='utf-8')
    temp.replace(target)
