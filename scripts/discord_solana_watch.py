#!/usr/bin/env python3
"""Watch new tokens in one Discord channel; produce local read-only reports."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse

from curl_cffi import requests
from discord_token_analysis import Analyzer, Store, ReadRPC, RemoteError, candidates, write_reports, valid_address

ROOT = Path(__file__).resolve().parents[1]
WATCH_VERSION = '2026.10.31.2'


class StateLock:
    def __init__(self, path):
        self.path = Path(str(path) + '.watch.lock')

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.file.write(b'0'); self.file.flush(); self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RemoteError('Cette analyse Discord est déjà lancée pour ce journal ; fermer son ancien terminal') from None
        return self

    def __exit__(self, *args):
        self.file.close()


def scout_source(path):
    return 'scout:' + str(Path(path).resolve())


def initialize_scout(store, path, from_start=False):
    source = scout_source(path)
    if store.cursor(source) is None:
        offset = 0 if from_start or not path.exists() else path.stat().st_size
        store.advance(source, offset)
    return source


def poll_scout(store, path):
    """Follow alert_scout's append-only journal without changing it or its extension."""
    if not path.is_file():
        return []
    source = scout_source(path)
    offset = int(store.cursor(source) or 0)
    if path.stat().st_size < offset:
        offset = 0
        print('Journal scout tronqué : reprise au début, CA déjà traités dédupliqués.', flush=True)
    added = []
    with path.open('rb') as stream:
        stream.seek(offset)
        for _ in range(200):
            line = stream.readline(1_000_001)
            if not line or not line.endswith(b'\n'):
                break  # A writer may still be finishing this line. Do not advance.
            next_offset = stream.tell()
            try:
                event = json.loads(line)
                if event.get('type') == 'alert' and event.get('source') == 'discord':
                    at = float(event['t_alert'])
                    mint = event['mint']
                    if not math.isfinite(at) or at <= 0 or at > time.time() + 10 or not valid_address(mint):
                        raise ValueError('Signal invalide')
                    if store.enqueue(mint, source, 'scout-offset:' + str(offset), at):
                        added.append(mint)
                        print(f'NOUVEAU TOKEN SCOUT : {mint} ; heure du signal conservée.', flush=True)
            except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
                print('Événement scout incomplet ignoré.', flush=True)
            store.advance(source, next_offset)
            offset = next_offset
    return added


def configured_rpc(config_path):
    # Only select existing local RPC credentials; never print or copy their values.
    config = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        except (ValueError, OSError):
            raise RemoteError('Configuration scout illisible') from None
    key = os.getenv('HELIUS_API_KEY') or config.get('helius_api_key')
    return (os.getenv('SOLANA_RPC_URL') or config.get('rpc_url')
            or ('https://mainnet.helius-rpc.com/?api-key=' + key if key else 'https://api.mainnet-beta.solana.com'))


def configured_telegram(config_path):
    config = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        except (ValueError, OSError):
            raise RemoteError('Configuration scout illisible') from None
    token = os.getenv('TELEGRAM_BOT_TOKEN') or config.get('telegram_bot_token')
    chat = os.getenv('TELEGRAM_CHAT_ID') or config.get('telegram_chat_id')
    if not token and not chat:
        return None
    if not isinstance(token, str) or not token.strip() or not isinstance(chat, (str, int)) or not str(chat).strip():
        raise RemoteError('Telegram : renseigner telegram_bot_token et telegram_chat_id dans scout_config.json')
    from discord_token_telegram import TelegramSender
    return TelegramSender(token.strip(), str(chat).strip())


def channel_id(value):
    match = re.fullmatch(r'https://(?:www\.)?discord\.com/channels/\d{10,22}/(\d{10,22})/?', value)
    result = match[1] if match else value
    if not re.fullmatch(r'\d{10,22}', result):
        raise ValueError('Fournir le lien ou l’identifiant numérique du salon Discord')
    return result


def signal_time(message):
    # Snowflake timestamp is stable across edits and grouped messages without a time tag.
    identity = str(message['id'])
    if not re.fullmatch(r'\d{10,22}', identity):
        raise ValueError('Identifiant de message Discord invalide')
    value = ((int(identity) >> 22) + 1420070400000) / 1000
    if 'timestamp' in message:
        actual = datetime.fromisoformat(message['timestamp'].replace('Z', '+00:00'))
        if actual.tzinfo is None or abs(actual.timestamp() - value) > 5:
            raise ValueError('Horodatage Discord incohérent')
    if value > time.time() + 10:
        raise ValueError('Message daté dans le futur')
    return value


def message_text(message):
    parts = [message.get('content') or '']
    for embed in message.get('embeds', []):
        parts += [embed.get('title') or '', embed.get('description') or '', embed.get('url') or '']
        for field in embed.get('fields', []):
            parts += [field.get('name') or '', field.get('value') or '']
    return '\n'.join(parts)


def ingest(store, source, message):
    at = signal_time(message)
    new = []
    for mint in candidates(message_text(message)):
        if store.enqueue(mint, source, message['id'], at):
            new.append(mint)
            print(f'NOUVEAU CA : {mint} ; signal UTC {datetime.fromtimestamp(at, timezone.utc).isoformat()}', flush=True)
    return new


class DiscordBotReader:
    def __init__(self, channel, token):
        self.channel = channel
        self.http = requests.Session(impersonate='chrome')
        self.headers = {'Authorization': 'Bot ' + token}

    def get(self, route):
        try:
            response = self.http.get('https://discord.com/api/v10' + route, headers=self.headers, timeout=15)
            if response.status_code == 429:
                wait = min(60, max(1, float(response.json().get('retry_after', 5))))
                raise RemoteError('Discord : quota temporaire atteint', retry_after=wait)
            if response.status_code in (401, 403):
                raise RemoteError('Discord : vérifier le jeton du bot et ses permissions Voir le salon / Lire l’historique', status=response.status_code)
            if response.status_code != 200:
                raise RemoteError(f'Discord : HTTP {response.status_code}')
            return response.json()
        except RemoteError:
            raise
        except Exception:
            raise RemoteError('Discord : connexion ou réponse indisponible') from None

    def initialize(self):
        info = self.get('/channels/' + self.channel)
        print('Salon Discord autorisé : ' + str(info.get('name', self.channel)), flush=True)

    def latest(self):
        rows = self.get(f'/channels/{self.channel}/messages?limit=1')
        return str(rows[0]['id']) if rows else '0'

    def messages(self, after):
        rows = self.get(f'/channels/{self.channel}/messages?limit=100&after={after}')
        return sorted(rows, key=lambda row: int(row['id']))


class BrowserReader:
    """Read visible DOM only; no Discord user token, private API or auto-login."""
    def __init__(self, channel, cdp):
        parsed = urlparse(cdp)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('CDP doit pointer vers Chrome sur localhost uniquement')
        self.channel, self.cdp = channel, cdp
        self.page = None

    def initialize(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RemoteError('Installer Playwright : py -m pip install playwright') from None
        self.driver = sync_playwright().start()
        try:
            self.browser = self.driver.chromium.connect_over_cdp(self.cdp)
            self.select_page()
        except Exception:
            self.driver.stop()
            raise RemoteError('Chrome CDP inaccessible ou salon Discord non ouvert ; consulter DISCORD_SOLANA.md') from None
        print('Lecture du salon ouvert dans Chrome. Aucun jeton Discord lu ni message envoyé.', flush=True)

    def select_page(self):
        for context in self.browser.contexts:
            for page in context.pages:
                u = urlparse(page.url)
                if u.hostname in ('discord.com', 'www.discord.com') and re.fullmatch(r'/channels/\d+/' + self.channel + '/?', u.path):
                    self.page = page
                    return
        raise RemoteError('Garder le salon Discord demandé ouvert dans Chrome')

    def visible(self):
        try:
            self.select_page()
            return self.page.evaluate('''channel => Array.from(document.querySelectorAll('[id^="chat-messages-"]'))
          .filter(el => new RegExp('^chat-messages-' + channel + '-[0-9]+$').test(el.id))
          .map(el => ({id: el.id.split('-').at(-1), content: el.innerText + '\\n' +
            Array.from(el.querySelectorAll('a[href]')).map(a => a.href).join('\\n')}))''', self.channel)
        except RemoteError:
            raise
        except Exception:
            raise RemoteError('Lecture Chrome interrompue : vérifier le navigateur et le salon ouvert') from None

    def latest(self):
        rows = self.visible()
        if not rows:
            raise RemoteError('Aucun message Discord lisible dans le DOM ; ouvrir le salon et charger ses messages')
        return max((row['id'] for row in rows), key=int)

    def messages(self, after):
        rows = sorted(self.visible(), key=lambda row: int(row['id']))
        if rows and after != '0' and int(rows[0]['id']) > int(after):
            print('Attention : une portion de l’historique Discord peut manquer entre deux lectures DOM.', flush=True)
        return [row for row in rows if int(row['id']) > int(after)]

    def close(self):
        # Disconnect Playwright without closing the user's browser or tabs.
        if getattr(self, 'driver', None):
            try:
                self.driver.stop()
            except Exception:
                pass


def analyze_worker(args, stop):
    store = Store(args.state)
    rpc = ReadRPC(args.rpc, store, args.rpc_rps)
    analyzer = Analyzer(rpc, max_signatures=args.max_signatures, max_pages=args.max_callout_pages)
    try:
        store.recover()
        while not stop.is_set():
            signal = store.next()
            if not signal:
                stop.wait(.5)
                continue
            print(f'ANALYSE EN COURS : {signal["mint"]}', flush=True)
            write_reports(store, args.output)
            try:
                result = analyzer.analyze(signal)
                store.finish(signal['mint'], result=result)
                c = result['callouts']['count']
                print(f'RAPPORT PRÊT : {signal["mint"]} ; callouts publics retrouvés : {c if c is not None else "indisponible"}', flush=True)
                if not result.get('alert_eligible'):
                    print('AUCUNE ALERTE TELEGRAM : aucun achat antérieur vérifié de DECU ou LE L.', flush=True)
            except Exception as exc:
                message = str(exc) if isinstance(exc, RemoteError) else 'Analyse indisponible (' + type(exc).__name__ + ')'
                store.finish(signal['mint'], error=message)
                print('ANALYSE DIFFÉRÉE/INCOMPLÈTE : ' + message, flush=True)
            write_reports(store, args.output)
    finally:
        store.db.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('scout', 'browser', 'bot', 'jsonl'), default='scout')
    parser.add_argument('--scout-journal', type=Path, default=ROOT/'data/scout/journal.jsonl')
    parser.add_argument('--scout-config', type=Path, default=ROOT/'scout_config.json')
    parser.add_argument('--from-start', action='store_true', help='Rejouer le journal scout déjà présent au premier lancement')
    parser.add_argument('--channel', help='Lien ou identifiant du salon solana')
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--messages-file', type=Path, help='Source JSONL de messages Discord déjà autorisés')
    parser.add_argument('--rpc', default=None)
    parser.add_argument('--rpc-rps', type=float, default=2)
    parser.add_argument('--poll', type=float, default=2)
    parser.add_argument('--max-signatures', type=int, default=300)
    parser.add_argument('--max-callout-pages', type=int, default=40)
    parser.add_argument('--state', type=Path, default=ROOT/'data/discord_solana/state.sqlite3')
    parser.add_argument('--output', type=Path, default=ROOT/'data/discord_solana/reports')
    parser.add_argument('--retry-errors', action='store_true', help='Retenter les analyses en erreur, en lecture seule')
    parser.add_argument('--no-telegram', action='store_true', help='Conserver uniquement les rapports locaux')
    parser.add_argument('--telegram-history', action='store_true', help='Envoyer aussi les bilans déjà terminés avant cette mise à jour')
    parser.add_argument('--retry-telegram', action='store_true', help='Retenter les envois explicitement refusés ; pas les livraisons incertaines')
    args = parser.parse_args(argv)
    if not .1 <= args.rpc_rps <= 50 or not 1 <= args.poll <= 60 or not 1 <= args.max_signatures <= 10000 or not 1 <= args.max_callout_pages <= 200:
        parser.error('Limites RPC/poll/pagination invalides')
    if args.source in ('bot', 'browser') and not args.channel:
        parser.error('--channel est requis ; le nom solana seul ne suffit pas')
    if args.source == 'jsonl' and not args.messages_file:
        parser.error('--messages-file est requis pour la source jsonl')
    if args.channel:
        try:
            args.channel = channel_id(args.channel)
        except ValueError as exc:
            parser.error(str(exc))
    return args


def run(args):
    args.rpc = args.rpc or configured_rpc(args.scout_config)
    args.output.mkdir(parents=True, exist_ok=True)
    store, stop, reader = Store(args.state), threading.Event(), None
    if args.retry_errors:
        store.retry_errors()
    sender = None if args.no_telegram else configured_telegram(args.scout_config)
    if sender:
        from discord_token_telegram import Outbox
        Outbox(store, sender.destination, include_history=args.telegram_history, retry_failed=args.retry_telegram, recover=True)
    print('Analyse en lecture seule sur Solana et Discord : aucun achat ni vente.', flush=True)
    print('Version analyse Discord : ' + WATCH_VERSION, flush=True)
    print('Bilans Telegram activés via la configuration scout.' if sender else 'Bilans Telegram désactivés ou non configurés ; rapports locaux uniquement.', flush=True)
    if sender:
        print('Filtre Telegram : achat antérieur vérifié de DECU ou LE L uniquement. Erreurs et cas ambigus : rapports locaux.', flush=True)
    print('Rapports : ' + str((args.output/'index.html').resolve()), flush=True)
    thread = notifications = None
    try:
        if args.source == 'scout':
            source = initialize_scout(store, args.scout_journal, args.from_start)
            print('Lecture du journal existant : ' + str(args.scout_journal.resolve()), flush=True)
            print('Les événements Discord de type alert sont traités ; updates, messages ultérieurs, replays et entrées Telegram ignorés.', flush=True)
        elif args.source == 'jsonl':
            source = 'jsonl:' + str(args.messages_file.resolve())
        else:
            source = 'discord:' + args.channel
            if args.source == 'bot':
                token = os.getenv('DISCORD_BOT_TOKEN')
                if not token:
                    raise RemoteError('Définir DISCORD_BOT_TOKEN sur ton PC ; ne pas utiliser un jeton de compte utilisateur')
                reader = DiscordBotReader(args.channel, token)
            else:
                reader = BrowserReader(args.channel, args.cdp)
            reader.initialize()
            if store.cursor(source) is None:
                store.advance(source, reader.latest())
                print('Point de départ mémorisé : seuls les prochains messages seront traités.', flush=True)
        thread = threading.Thread(target=analyze_worker, args=(args, stop), daemon=True)
        write_reports(store, args.output)
        thread.start()
        if sender:
            from discord_token_telegram import notification_worker
            notifications = threading.Thread(target=notification_worker, args=(args, stop, sender), daemon=True)
            notifications.start()
        if args.source == 'jsonl':
            # Explicit import processes the supplied file; the mint primary key deduplicates replays.
            with args.messages_file.open(encoding='utf-8-sig') as stream:
                for line in stream:
                    if line.strip():
                        ingest(store, source, json.loads(line))
            while store.db.execute("SELECT 1 FROM signals WHERE state IN ('pending','running') LIMIT 1").fetchone():
                if not thread.is_alive():
                    raise RemoteError('Le worker d’analyse est arrêté ; relancer pour reprendre les CA mémorisés')
                stop.wait(.5)
            if sender:
                from discord_token_telegram import Outbox
                outbox = Outbox(store, sender.destination)
                outbox.queue()
                while store.db.execute("SELECT 1 FROM telegram_reports WHERE state IN ('pending','sending','editing') LIMIT 1").fetchone():
                    if not notifications.is_alive():
                        raise RemoteError('Notifications Telegram arrêtées ; rapports locaux conservés')
                    stop.wait(.5)
        else:
            while not stop.is_set():
                if not thread.is_alive():
                    raise RemoteError('Le worker d’analyse est arrêté ; relancer pour reprendre les CA mémorisés')
                if notifications and not notifications.is_alive():
                    raise RemoteError('Notifications Telegram arrêtées ; rapports locaux conservés')
                try:
                    if args.source == 'scout':
                        if poll_scout(store, args.scout_journal):
                            write_reports(store, args.output)
                    else:
                        after = store.cursor(source) or '0'
                        messages = reader.messages(after)
                        for message in messages:
                            try:
                                ingest(store, source, message)
                            except (ValueError, KeyError, TypeError):
                                print('Message Discord incomplet ignoré.', flush=True)
                            store.advance(source, message['id'])
                except RemoteError as exc:
                    print(str(exc), flush=True)
                    if exc.status in (401, 403):
                        break
                    stop.wait(min(60, max(args.poll, exc.retry_after or 0)))
                stop.wait(args.poll)
    finally:
        stop.set()
        if reader and hasattr(reader, 'close'):
            reader.close()
        if thread:
            thread.join(timeout=20)
        if notifications:
            notifications.join(timeout=17)
        store.db.close()


if __name__ == '__main__':
    try:
        args = parse_args()
        with StateLock(args.state):
            run(args)
    except KeyboardInterrupt:
        print('Arrêt demandé. Les CA déjà enregistrés restent mémorisés.')
    except RemoteError as exc:
        raise SystemExit(str(exc)) from None
