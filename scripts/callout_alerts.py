#!/usr/bin/env python3
"""Alertes pump.fun → Telegram, via Bot API ou compte Telegram (Telethon)."""
import argparse
import datetime as dt
from email.utils import parsedate_to_datetime
import html
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

TG_API_ID = 30461091
TG_API_HASH = '1ba7f769c94596baaf675447c02bb478'

API = 'https://frontend-api-v3.pump.fun'
WALLET = re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}\Z')


class RemoteError(RuntimeError):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def retry_seconds(value, now=None):
    """Retry-After may be seconds or an HTTP date. Never shorten the server's wait."""
    if not isinstance(value, (str, int, float)):
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            seconds = when.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class RateBackoff:
    def __init__(self):
        self.failures = 0

    def delay(self, error):
        if error.status == 429:
            delay = min(1800, 120 * 2 ** min(self.failures, 4))
            self.failures += 1
        else:
            delay = 120 if error.status == 403 else 30
        return max(delay, error.retry_after or 0)

    def success(self):
        self.failures = 0


def request(url, body=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'PumpCalloutAlerts/1.0'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        retry = retry_seconds(exc.headers.get('Retry-After'))
        raise RemoteError(f'HTTP {exc.code}', status=exc.code, retry_after=retry) from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        # Never log request URLs: Telegram URLs contain the bot token.
        raise RemoteError('Connexion impossible ou réponse JSON invalide') from None


class Pump:
    def __init__(self, http_client='auto'):
        self.next_request = 0
        self.cooldowns = {}
        self.session = None
        if http_client != 'urllib':
            try:
                from curl_cffi import requests
                self.session = requests.Session(impersonate='chrome')
            except ImportError:
                if http_client == 'browser':
                    raise RemoteError('Installer le client navigateur : py -m pip install curl_cffi') from None
        self.http_client = 'browser' if self.session else 'urllib'

    def get(self, path):
        route = path.split('?')[0]
        family = next((prefix for prefix in ('/callout/list/', '/coins-v2/', '/following/v3/following/')
                       if route.startswith(prefix)), route)
        until, code = self.cooldowns.get(family, (0, None))
        remaining = until - time.monotonic()
        if remaining > 0:
            raise RemoteError(f'pump.fun {family} : pause serveur ({remaining:.0f}s)', status=code, retry_after=remaining)
        time.sleep(max(0, self.next_request - time.monotonic()))
        self.next_request = time.monotonic() + 1.1
        try:
            if self.session is None:
                return request(API + path)
            response = self.session.get(API + path, timeout=20, headers={
                'Accept': 'application/json', 'Origin': 'https://pump.fun', 'Referer': 'https://pump.fun/'})
            if response.status_code != 200:
                raise RemoteError(f'HTTP {response.status_code}', status=response.status_code,
                                  retry_after=retry_seconds(response.headers.get('Retry-After')))
            return response.json()
        except RemoteError as exc:
            if exc.retry_after is not None and exc.retry_after > 0:
                self.cooldowns[family] = (time.monotonic() + exc.retry_after, exc.status)
            raise RemoteError(f'pump.fun {route} : {exc}', status=exc.status, retry_after=exc.retry_after) from None
        except Exception:
            raise RemoteError('pump.fun : connexion impossible ou réponse JSON invalide') from None

    def resolve(self, profile):
        name = profile.rstrip('/').split('/')[-1].lstrip('@')
        if WALLET.fullmatch(name):
            return name, name[:8]
        rows = self.get('/users/search-v2?' + urllib.parse.urlencode({'searchTerm': name, 'limit': 10}))
        if not isinstance(rows, list):
            raise RemoteError('Format de recherche pump.fun inattendu')
        matches = [r for r in rows if (r.get('username') or '').casefold() == name.casefold()]
        if len(matches) != 1:
            raise RemoteError('Profil introuvable ou ambigu ; utiliser son adresse de wallet')
        return matches[0]['address'], matches[0]['username']

    def following(self, owner):
        users = {}
        offset = 0
        while True:
            rows = self.get(f'/following/v3/following/{owner}?limit=100&offset={offset}')
            if not isinstance(rows, list):
                raise RemoteError('Format des abonnements inattendu')
            before = len(users)
            users.update({r['address']: r.get('username') or r['address'][:8] for r in rows})
            if len(rows) < 100:
                return users
            if len(users) == before:
                raise RemoteError('Pagination des abonnements non prise en charge par cette API')
            offset += len(rows)

    def enrich(self, call):
        try:
            coin = self.get('/coins-v2/' + call['coinMint'])
            if isinstance(coin, dict):
                call['name'] = call.get('name') or coin.get('name')
                call['symbol'] = call.get('symbol') or coin.get('symbol')
                call['mcap_now'] = coin.get('usd_market_cap')
        except RemoteError:
            pass  # The original call remains useful when metadata is unavailable.
        return call

    def callouts(self, wallet):
        result = self.get(f'/callout/list/{wallet}?limit=100&sortBy=TIMESTAMP&sortOrder=DESC')
        if not isinstance(result, dict) or not isinstance(result.get('callouts'), list):
            raise RemoteError('Format des callouts inattendu')
        return result['callouts']


    def feed(self):
        result = self.get('/home-feed/new')
        if not isinstance(result, dict) or not isinstance(result.get('coins'), list):
            raise RemoteError('Format du flux pump.fun inattendu')
        calls = []
        for row in result['coins']:
            position = row.get('position') or {}
            call = position.get('callout') or {}
            if not call.get('calloutId') or not call.get('calloutTimestamp'):
                continue
            calls.append(dict(calloutId=call['calloutId'], createdAt=call['calloutTimestamp'],
                              author=position.get('walletAddress'), coinMint=row.get('coinMint'),
                              name=row.get('coinName'), symbol=row.get('symbol'),
                              marketCap=call.get('calledOutAtMcap'), mcap_now=row.get('marketCap'),
                              thesis=call.get('thesis')))
        return calls


def usd(value):
    try:
        n = float(value)
        if not math.isfinite(n) or n < 0:
            return '?'
    except (TypeError, ValueError):
        return '?'
    return f'${n / 1e6:.2f}M' if n >= 1e6 else f'${n / 1000:.1f}k' if n >= 1000 else f'${n:.0f}'


def timestamp(value):
    if isinstance(value, str) and not value.replace('.', '', 1).isdigit():
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    n = float(value)
    return n / 1000 if n > 1e11 else n


def format_alert(call):
    mint = call['coinMint']
    if not WALLET.fullmatch(mint):
        raise ValueError('Adresse Solana invalide')
    esc = html.escape
    name = str(call.get('name') or mint[:8] + '…')[:200]
    symbol = str(call.get('symbol') or '')[:40]
    if symbol:
        name += f' (${symbol})'
    lines = [f'📣 <b>{esc(str(call["author_name"])[:100])}</b> a callé <b>{esc(name)}</b>']
    if call.get('thesis'):
        lines.append(f'💬 « {esc(str(call["thesis"])[:1000])} »')
    mc = f'💰 MC au call : <b>{usd(call.get("marketCap"))}</b>'
    if call.get('mcap_now') is not None:
        mc += f' · maintenant {usd(call["mcap_now"])}'
    lines += [mc, f'📄 CA : <code>{mint}</code>',
              f'<a href="https://pump.fun/coin/{mint}">pump.fun</a> · '
              f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a> · '
              + dt.datetime.fromtimestamp(timestamp(call['createdAt']), dt.timezone.utc).strftime('%H:%M:%S UTC')]
    return '\n'.join(lines)


def send(token, chat, text):
    result = request(f'https://api.telegram.org/bot{token}/sendMessage', {
        'chat_id': chat, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True})
    if not result.get('ok'):
        raise RemoteError('Telegram a refusé le message')


class TelegramUserSender:
    """A user session with a running event loop throughout slow HTTP polling."""
    def __init__(self, target, session):
        self.target, self.session = target, session
        self.loop = self.client = self.thread = None
        self.identity = None

    def connect(self):
        import asyncio
        import threading
        try:
            from telethon import TelegramClient
        except ImportError:
            raise RemoteError('Installer Telethon : python -m pip install Telethon==1.45.0') from None
        Path(self.session).parent.mkdir(parents=True, exist_ok=True)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.client = TelegramClient(self.session, int(os.getenv('TG_API_ID') or TG_API_ID),
                                     os.getenv('TG_API_HASH') or TG_API_HASH, loop=self.loop)

        async def login():
            await self.client.start()
            self.entity = await self.client.get_entity(self.target)
            self.identity = (await self.client.get_me()).id

        try:
            self.loop.run_until_complete(login())
        except (Exception, KeyboardInterrupt):
            self.loop.run_until_complete(self.client.disconnect())
            self.loop.close()
            raise
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        return self

    def __call__(self, text):
        import asyncio

        async def deliver():
            return await self.client.send_message(self.entity, text, parse_mode='html', link_preview=False)

        future = asyncio.run_coroutine_threadsafe(deliver(), self.loop)
        try:
            future.result(timeout=60)
        except Exception as exc:
            future.cancel()
            raise RemoteError(f'Envoi Telegram différé ({type(exc).__name__})') from None

    def close(self):
        import asyncio
        if self.thread is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop).result(timeout=10)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(timeout=10)
                if not self.thread.is_alive():
                    self.loop.close()
                self.thread = None

    async def _disconnect(self):
        await self.client.disconnect()


class Store:
    def __init__(self, path, now):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, payload TEXT, sent INTEGER DEFAULT 0)')
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('since', ?)", (now,))
        self.db.commit()
        self.since = self.db.execute("SELECT value FROM meta WHERE key='since'").fetchone()[0]

    def known(self, call):
        return self.db.execute('SELECT 1 FROM alerts WHERE id=?', (str(call['calloutId']),)).fetchone() is not None

    def enqueue(self, call):
        if timestamp(call['createdAt']) < self.since:
            return
        format_alert(call)  # Validate before persistence, isolate malformed upstream entries.
        self.db.execute('INSERT OR IGNORE INTO alerts (id,payload) VALUES (?,?)',
                        (str(call['calloutId']), json.dumps(call)))
        self.db.commit()

    def deliver(self, sender):
        for identity, payload in self.db.execute('SELECT id,payload FROM alerts WHERE sent=0 ORDER BY rowid').fetchall():
            sender(format_alert(json.loads(payload)))
            self.db.execute('UPDATE alerts SET sent=1 WHERE id=?', (identity,))
            self.db.commit()
            print('Alerte envoyée', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='Lelouch_0x', help='Nom, wallet ou URL du profil pump.fun')
    parser.add_argument('--http-client', choices=['auto', 'browser', 'urllib'], default='auto')
    parser.add_argument('--transport', choices=['user', 'bot'], default='user')
    parser.add_argument('--send-to', default='@callout44_bot', help='Bot destinataire (défaut : @callout44_bot)')
    parser.add_argument('--session', default=str(Path(__file__).resolve().parents[1] / 'data' / 'callout_alerts_telegram'), help='Session Telethon persistante')
    parser.add_argument('--mode', choices=['following', 'self'], default='following')
    parser.add_argument('--interval', type=float, default=5, help='Pause entre deux tours, en secondes')
    parser.add_argument('--dry-run', action='store_true', help='Afficher les alertes sans Telegram ; utiliser un état distinct')
    parser.add_argument('--state', help='SQLite : un fichier distinct par profil, mode et destinataire')
    args = parser.parse_args()
    if args.interval < 1:
        parser.error('--interval doit être >= 1')
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not args.dry_run and args.transport == 'user' and not args.send_to:
        parser.error('Indiquer --send-to @nom_du_bot')
    if not args.dry_run and args.transport == 'bot' and not (token and chat):
        parser.error('Définir TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans les variables d’environnement')
    pump = Pump(args.http_client)
    print(f'Client pump.fun : {pump.http_client}', flush=True)
    owner, label = pump.resolve(args.profile)
    # A default database belongs to one owner/mode/destination, avoiding cross-run suppression.
    import hashlib
    user_sender = None
    if not args.dry_run and args.transport == 'user':
        user_sender = TelegramUserSender(args.send_to, args.session).connect()
    recipient = f'user:{user_sender.identity}:{user_sender.entity.id}' if user_sender else f'bot:{chat}'
    destination = 'dry-run' if args.dry_run else hashlib.sha256(recipient.encode()).hexdigest()[:16]
    path = args.state or str(Path(__file__).resolve().parents[1] / 'data' / f'alerts_{owner}_{args.mode}_{destination}.sqlite3')
    store = Store(path, time.time())
    sender = print if args.dry_run else user_sender if user_sender else lambda text: send(token, chat, text)
    try:
        monitor(pump, owner, label, store, sender, args)
    finally:
        store.db.close()
        if user_sender:
            user_sender.close()


def collect(store, pump, rows, targets):
    for call in reversed(rows):
        try:
            author = call.get('author')
            if author not in targets:
                continue
            if timestamp(call['createdAt']) >= store.since and not store.known(call):
                if not WALLET.fullmatch(call.get('coinMint', '')):
                    continue
                enriched = dict(call, author_name=targets[author])
                if not enriched.get('name') or enriched.get('mcap_now') is None:
                    enriched = pump.enrich(enriched)
                store.enqueue(enriched)
        except (KeyError, TypeError, ValueError, OverflowError):
            print('Callout incomplet ignoré', flush=True)


def monitor(pump, owner, label, store, sender, args):
    targets, refresh_at, index = {}, 0, 0
    feed_after, lists_after = 0, 0
    print(f'Surveillance : {label} / {args.mode}. Aucun ancien callout au premier démarrage.', flush=True)
    while True:
        now = time.monotonic()
        if now >= refresh_at:
            try:
                targets = {owner: label} if args.mode == 'self' else pump.following(owner)
                refresh_at = time.monotonic() + 600
                print(f'{len(targets)} profil(s) surveillé(s)', flush=True)
            except RemoteError as exc:
                print(f'Abonnements différés : {exc}', flush=True)
                refresh_at = time.monotonic() + 60
        if targets and now >= feed_after:
            try:
                collect(store, pump, pump.feed(), targets)
            except RemoteError as exc:
                wait = 120 if exc.status in (403, 429) else 30
                print(f'Flux différé ({wait}s) : {exc}', flush=True)
                feed_after = time.monotonic() + wait
        if targets and now >= lists_after:
            wallet = list(targets)[index % len(targets)]
            index += 1
            try:
                rows = pump.callouts(wallet)
                # A full page only indicates a gap if even its oldest callout is newer than our cursor.
                if len(rows) >= 100 and all(timestamp(c['createdAt']) >= store.since for c in rows):
                    print(f'{targets[wallet]} : plus de 100 callouts depuis le démarrage, rattrapage incomplet.', flush=True)
                collect(store, pump, [dict(c, author=wallet) for c in rows], targets)
            except RemoteError as exc:
                wait = 120 if exc.status in (403, 429) else 30
                print(f'Profils différés ({wait}s), le flux reste actif : {exc}', flush=True)
                lists_after = time.monotonic() + wait
        try:
            store.deliver(sender)
        except RemoteError as exc:
            print(f'Envoi Telegram différé : {exc}', flush=True)
        time.sleep(args.interval)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nArrêt')
    except RemoteError as exc:
        raise SystemExit(str(exc)) from None
