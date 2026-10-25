#!/usr/bin/env python3
"""Un seul lecteur pump.fun pour les alertes Telegram et les achats/republications."""
from collections import deque
import hashlib
import json
from pathlib import Path
import queue
import threading
import time

from callout_alerts import Pump, RemoteError, RateBackoff, Store, TelegramUserSender, WALLET, timestamp
from callout_repost import CALLERS, ROOT, Execution, Journal, Engine, parse_args, show_status, check_swap, RunnerLock


class SharedReader:
    """Only this object polls the public feed and monitored profiles."""
    def __init__(self, pump, owner, alert_since, repost_since, alerts_queue, repost_queue,
                 repost=True, interval=2, backup_interval=15, event_sink=None):
        self.pump, self.owner = pump, owner
        self.alert_since, self.repost_since = alert_since, repost_since
        self.alerts_queue, self.repost_queue = alerts_queue, repost_queue
        self.repost, self.interval, self.backup_interval = repost, interval, backup_interval
        self.follows, self.targets = {}, {}
        self.next_feed = self.next_backup = self.next_refresh = 0
        self.feed_ok = False
        self.feed_backoff, self.backup_backoff, self.refresh_backoff = RateBackoff(), RateBackoff(), RateBackoff()
        self.priority, self.others = deque(), deque()
        self.backup_count = 0
        self.seen_alerts, self.seen_reposts = set(), set()
        self.metadata = {}
        self.event_sink, self.seen_ui = event_sink, set()
        self.last_health = 0

    def refresh(self, now):
        try:
            follows = self.pump.following(self.owner)
            self.follows = follows
            self.targets = dict(follows)
            if self.repost:
                self.targets.update(CALLERS)
            high = CALLERS if self.repost else self.targets
            self.priority = deque(w for w in high if w in self.targets)
            self.others = deque(w for w in self.targets if w not in high)
            self.next_refresh = now + 600
            self.refresh_backoff.success()
            print(f'Lecture partagée : {len(follows)} abonnements Telegram, {len(CALLERS) if self.repost else 0} callers de republication.', flush=True)
        except RemoteError as exc:
            # Trading targets are independent of a temporarily unavailable following list.
            if self.repost:
                self.targets.update(CALLERS)
                if not self.priority:
                    self.priority = deque(CALLERS)
            self.next_refresh = now + max(60, self.refresh_backoff.delay(exc))
            print(f'Abonnements différés : {exc}', flush=True)

    def route(self, rows):
        for call in reversed(rows):
            try:
                identity = str(call['calloutId'])
                author, mint = call.get('author'), call.get('coinMint', '')
                if not WALLET.fullmatch(mint):
                    continue
                if self.event_sink and identity not in self.seen_ui:
                    public = {key: call.get(key) for key in ('calloutId', 'coinMint', 'createdAt', 'name', 'symbol', 'thesis', 'marketCap', 'mcap_now', 'author')}
                    public['author_name'] = self.targets.get(author) or call.get('author_name') or (author or '?')[:8]
                    public['selected'] = author in CALLERS and self.repost
                    self.event_sink('callout', public)
                    self.seen_ui.add(identity)
                if author not in self.targets:
                    continue
                created = timestamp(call['createdAt'])
                # Send to the trade worker first: metadata and Telegram cannot delay an order.
                if self.repost and author in CALLERS and created >= self.repost_since and identity not in self.seen_reposts:
                    self.repost_queue.put(dict(call))
                    self.seen_reposts.add(identity)
                if author not in self.follows or created < self.alert_since or identity in self.seen_alerts:
                    continue
                enriched = dict(call, author_name=self.follows[author])
                cached = self.metadata.get(mint)
                if cached and time.monotonic() - cached[0] < 30:
                    for key in ('name', 'symbol', 'mcap_now'):
                        if enriched.get(key) is None:
                            enriched[key] = cached[1].get(key)
                if not enriched.get('name') or enriched.get('mcap_now') is None:
                    enriched = self.pump.enrich(enriched)
                self.metadata[mint] = (time.monotonic(), enriched)
                self.alerts_queue.put(enriched)
                self.seen_alerts.add(identity)
            except (KeyError, TypeError, ValueError, OverflowError):
                print('Callout incomplet ignoré', flush=True)
        # Historical identifiers are also persisted by consumers; bound the session cache.
        if len(self.seen_alerts) > 20000:
            self.seen_alerts.clear()
        if len(self.seen_reposts) > 20000:
            self.seen_reposts.clear()
        if len(self.seen_ui) > 20000:
            self.seen_ui.clear()
        if len(self.metadata) > 1000:
            cutoff = time.monotonic() - 30
            self.metadata = {k: v for k, v in self.metadata.items() if v[0] >= cutoff}

    def backup_wallet(self):
        # One shared profile read, even when that caller belongs to both features.
        self.backup_count += 1
        cycle = self.others if self.others and self.backup_count % 6 == 0 else self.priority or self.others
        if not cycle:
            return None
        wallet = cycle[0]
        cycle.rotate(-1)
        return wallet

    def step(self, now=None):
        now = time.monotonic() if now is None else now
        if now >= self.next_refresh:
            self.refresh(now)
        if self.targets and now >= self.next_feed:
            feed_started = time.monotonic()
            try:
                self.route(self.pump.feed())
                self.feed_ok = True
                self.feed_backoff.success()
                self.next_feed = feed_started + self.interval
            except RemoteError as exc:
                self.feed_ok = False
                wait = self.feed_backoff.delay(exc)
                self.next_feed = time.monotonic() + wait
                # Accelerate profile fallback as soon as the feed fails.
                self.next_backup = min(self.next_backup, now)
                print(f'Flux partagé différé ({wait:.0f}s) : {exc}', flush=True)
        if self.targets and now >= self.next_backup:
            wallet = self.backup_wallet()
            if wallet:
                try:
                    self.route([dict(c, author=wallet) for c in self.pump.callouts(wallet)])
                    if self.backup_backoff.failures:
                        print('Lectures des profils reprises.', flush=True)
                    self.backup_backoff.success()
                    self.next_backup = time.monotonic() + (self.backup_interval if self.feed_ok else 2)
                except RemoteError as exc:
                    wait = self.backup_backoff.delay(exc)
                    self.next_backup = time.monotonic() + wait
                    print(f'Profils partagés en pause ({wait:.0f}s), flux global conservé : {exc}', flush=True)

        if self.event_sink and time.monotonic() - self.last_health >= 5:
            self.event_sink('health', {'feed_ok': self.feed_ok, 'follows': len(self.follows),
                                      'callers': len(CALLERS) if self.repost else 0,
                                      'feed_retry_in': max(0, self.next_feed - time.monotonic()),
                                      'profiles_retry_in': max(0, self.next_backup - time.monotonic())})
            self.last_health = time.monotonic()


class EventSink:
    """Local, bounded UI telemetry; failure must never interrupt trading."""
    def __init__(self, path):
        self.path, self.warned = Path(path), False
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, kind, data):
        try:
            mode = 'w' if self.path.exists() and self.path.stat().st_size > 5_000_000 else 'a'
            with self.path.open(mode, encoding='utf-8') as stream:
                stream.write(json.dumps({'type': kind, 'at': time.time(), 'data': data}, ensure_ascii=False) + '\n')
        except OSError:
            if not self.warned:
                print('Interface : événement local non enregistré.', flush=True)
                self.warned = True


class AlertWorker:
    def __init__(self, events, path, sender, stop):
        self.events, self.path, self.sender, self.stop = events, path, sender, stop
        self.ready, self.since, self.error = threading.Event(), None, None
        self.known = set()

    def run(self):
        store = None
        try:
            store = Store(self.path, time.time())
            self.since = store.since
            self.known = {row[0] for row in store.db.execute('SELECT id FROM alerts')}
            self.ready.set()
            retry_at = 0
            while not self.stop.is_set():
                try:
                    call = self.events.get(timeout=.25)
                    if not store.known(call):
                        store.enqueue(call)
                except queue.Empty:
                    pass
                except (KeyError, ValueError, TypeError, OverflowError):
                    print('Alerte incomplète ignorée', flush=True)
                if time.monotonic() >= retry_at:
                    try:
                        store.deliver(self.sender)
                    except RemoteError as exc:
                        retry_at = time.monotonic() + 5
                        print(f'Telegram différé : {exc}', flush=True)
        except Exception as exc:
            self.error = type(exc).__name__
            self.stop.set()
        finally:
            self.ready.set()
            if store:
                store.db.close()


class RepostWorker:
    def __init__(self, events, path, execution, args, stop):
        self.events, self.path, self.execution, self.args, self.stop = events, path, execution, args, stop
        self.ready, self.since, self.error = threading.Event(), None, None
        self.known = set()

    def run(self):
        journal = None
        try:
            journal = Journal(self.path, time.time(), self.execution.owner)
            self.since = journal.since
            self.known = {job['call_id'] for job in journal.jobs()}
            engine = Engine(journal, self.execution, self.args)
            self.ready.set()
            while not self.stop.is_set():
                try:
                    call = self.events.get(timeout=.25)
                    journal.add(call, time.time(), self.args.max_age, self.args.delay, self.args.text)
                except queue.Empty:
                    pass
                except (KeyError, TypeError, ValueError, OverflowError):
                    print('Signal de republication incomplet ignoré', flush=True)
                engine.step(time.time())
        except Exception as exc:
            self.error = type(exc).__name__
            self.stop.set()
        finally:
            self.ready.set()
            if journal:
                journal.db.close()


def data_roots():
    # The first standalone download stored its Telegram session in ~/data.
    roots = [ROOT / 'data', Path.cwd() / 'data', Path.cwd().parent / 'data', Path.home() / 'data']
    return list(dict.fromkeys(roots))


def retained_path(filename):
    for root in data_roots():
        candidate = root / filename
        if candidate.is_file():
            return str(candidate)
    return str(ROOT / 'data' / filename)


def configure(parser):
    parser.description = __doc__
    parser.add_argument('--events-file', help='Fichier local d’événements pour le tableau de bord')
    parser.add_argument('--stop-file', help='Arrêter proprement quand ce fichier local existe')
    parser.add_argument('--send-to', default='@callout44_bot')
    parser.add_argument('--session', help='Session Telegram existante ; détectée automatiquement sinon')
    parser.add_argument('--alerts-state', help='Journal SQLite des alertes existantes')
    parser.add_argument('--alerts-only', action='store_true', help='Alertes seules, sans le worker de republication')
    parser.add_argument('--dry-run', action='store_true', help='Aucun envoi Telegram, achat ni publication')
    parser.add_argument('--feed-interval', type=float, default=2)
    parser.add_argument('--backup-interval', type=float, default=15)


def main():
    args = parse_args(configure=configure)
    if args.status:
        show_status(args.state or str(ROOT / 'data' / 'repost_live.sqlite3'))
        return
    if args.check_swap:
        check_swap(args)
        return
    with RunnerLock(ROOT / 'data' / '.callout-runner.lock'):
        run_hub(args)


def run_hub(args):
    if args.live and (args.dry_run or args.alerts_only):
        raise RemoteError('--live ne peut pas être combiné avec --dry-run ou --alerts-only')
    if not 1.1 <= args.feed_interval <= 60 or not 1.1 <= args.backup_interval <= 120:
        raise RemoteError('Intervalles invalides (minimum 1,1 seconde)')
    pump = Pump('browser')
    owner, label = pump.resolve(args.profile)
    sender, user = print, None
    stop = threading.Event()
    threads, workers = [], []
    try:
        if not args.dry_run:
            session = args.session or retained_path('callout_alerts_telegram.session')
            user = TelegramUserSender(args.send_to, session).connect()
            sender = user
            recipient = f'user:{user.identity}:{user.entity.id}'
            destination = hashlib.sha256(recipient.encode()).hexdigest()[:16]
        else:
            destination = 'dry-run'
        alert_path = args.alerts_state or retained_path(f'alerts_{owner}_following_{destination}.sqlite3')
        alerts_queue, repost_queue = queue.Queue(), queue.Queue()
        alerts = AlertWorker(alerts_queue, alert_path, sender, stop)
        workers.append(alerts)
        repost = None
        if not args.alerts_only:
            execution = Execution(args, owner=owner)
            repost_path = args.state or str(ROOT / 'data' / ('repost_live.sqlite3' if args.live else 'repost_simulation.sqlite3'))
            repost = RepostWorker(repost_queue, repost_path, execution, args, stop)
            workers.append(repost)
        for worker in workers:
            thread = threading.Thread(target=worker.run, daemon=True)
            threads.append(thread)
            thread.start()
        for worker in workers:
            while not worker.ready.wait(.25):
                if stop.is_set():
                    break
            if worker.error:
                raise RemoteError(f'Initialisation impossible : {worker.error}')
        reader = SharedReader(pump, owner, alerts.since, repost.since if repost else 0,
                              alerts_queue, repost_queue, repost=bool(repost),
                              interval=args.feed_interval, backup_interval=args.backup_interval,
                              event_sink=EventSink(args.events_file) if args.events_file else None)
        reader.seen_alerts.update(alerts.known)
        if repost:
            reader.seen_reposts.update(repost.known)
        mode = 'achats réels' if args.live else 'alertes seules' if args.alerts_only else 'achats simulés'
        tp = '' if args.alerts_only else f'TP +{args.take_profit}%, vente totale, '
        print(f'HUB : {label}, {mode}, {tp}flux unique toutes les {args.feed_interval}s minimum.', flush=True)
        while not stop.is_set() and not (args.stop_file and Path(args.stop_file).exists()):
            reader.step()
            stop.wait(.1)
        errors = [worker.error for worker in workers if worker.error]
        if errors:
            raise RemoteError(f'Un worker a été arrêté : {", ".join(errors)}')
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=45)
        if user:
            user.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nArrêt du hub. États conservés.')
    except RemoteError as exc:
        raise SystemExit(str(exc)) from None
