#!/usr/bin/env python3
"""Tableau de bord local du hub callouts. Lance le serveur sur 127.0.0.1."""
import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from callout_repost import CALLERS, ROOT
from callout_hub import retained_path

DEFAULTS = {'profile': 'Lelouch_0x', 'mode': 'simulation', 'usd': 2, 'delay': 15,
            'take_profit': 50, 'slippage_bps': 100, 'max_age': 60, 'jupiter_rps': .5, 'callers': list(CALLERS.values())}
RANGES = {'usd': (.01, 10000), 'delay': (0, 300), 'take_profit': (.01, 10000),
          'slippage_bps': (1, 5000), 'max_age': (1, 600), 'jupiter_rps': (.1, 150)}


def validate_config(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Configuration inconnue.')
    result = dict(DEFAULTS, **value)
    profile = result['profile']
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_@./:-]{1,180}', profile) or profile.startswith('-'):
        raise ValueError('Profil pump.fun invalide.')
    if result['mode'] not in ('simulation', 'live'):
        raise ValueError('Mode invalide.')
    for key, (low, high) in RANGES.items():
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f'Valeur invalide : {key}.')
    if result['slippage_bps'] != int(result['slippage_bps']):
        raise ValueError('Le slippage doit être un nombre entier de points de base.')
    selected = result['callers']
    if not isinstance(selected, list) or not selected or any(not isinstance(n, str) or n not in CALLERS.values() for n in selected) or len(set(selected)) != len(selected):
        raise ValueError('Sélectionne au moins un des callers configurés.')
    return result


def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def safe_message(value):
    text = str(value)
    for key in ('JUP_API_KEY', 'SOLANA_RPC_URL', 'TELEGRAM_BOT_TOKEN', 'TG_API_HASH'):
        secret = os.getenv(key)
        if secret:
            text = text.replace(secret, '[masqué]')
    return text[:1500]


def read_jobs(path):
    if not path.is_file():
        return [], None
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
            rows = db.execute('SELECT data FROM jobs ORDER BY rowid DESC').fetchall()
        # Never return signed bytes or raw exception bodies. Public signatures
        # are exposed only as validated explorer links, not arbitrary payloads.
        fields = ('mint', 'caller', 'name', 'symbol', 'state', 'created', 'buy_confirmed', 'cost_usd',
                  'position_tokens', 'entry_mc', 'mark_usd', 'mark_cost_usd', 'mark_checked_at', 'exit_state', 'sold_at',
                  'attempts', 'failure_reason', 'tp_missing_basis', 'tp_error', 'tp_error_at',
                  'confirmed_at', 'first_send_at', 'detect_to_send_ms', 'prepare_ms', 'broadcast_ms', 'post_started')
        jobs, historical = [], 0
        for row in rows:
            job = json.loads(row[0])
            active = (job.get('buy_confirmed') or job['state'] in ('confirmed', 'publishing', 'uncertain', 'posted')) and job.get('exit_state') not in ('sold', 'empty')
            if active or historical < 300:
                public = {k: job[k] for k in fields if k in job}
                if public.get('tp_error'):
                    public['tp_error'] = safe_message(public['tp_error'])
                for side in ('buy', 'sell'):
                    detail = job.get(side + '_details')
                    if isinstance(detail, dict):
                        allowed = ('mc_estimate', 'usd_estimate', 'sol_amount', 'token_raw', 'token_decimals',
                                   'block_time', 'slot', 'supply_raw', 'supply_slot', 'supply_observed_at')
                        public[side + '_details'] = {k: detail[k] for k in allowed if k in detail
                                                     and (detail[k] is None or number(detail[k]) is not None)}
                    if job.get(side + '_details_error'):
                        public[side + '_details_error'] = safe_message(job[side + '_details_error'])
                    signature = job.get('signature') if side == 'buy' else job.get('exit_tx', {}).get('signature')
                    if isinstance(signature, str) and re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,88}', signature):
                        public[side + '_tx_url'] = 'https://solscan.io/tx/' + signature
                jobs.append(public)
                if not active:
                    historical += 1
        return jobs, None
    except (sqlite3.Error, ValueError, OSError):
        return [], 'Le journal des positions est momentanément indisponible.'


class Dashboard:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.directory = self.root / 'data' / 'dashboard'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config = dict(DEFAULTS)
        config_file = self.directory / 'settings.json'
        if config_file.is_file():
            try:
                self.config = validate_config(json.loads(config_file.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                pass
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.process = None
        self.phase = 'stopped'
        self.logs = deque(maxlen=400)
        self.feed = deque(maxlen=240)
        self.caller_feed = deque(maxlen=240)
        self.ids = set()
        self.feed_count = 0
        self.health = {}
        self.event_path = self.stop_path = None
        self.offset = 0
        self.started_at = None
        self.run_id = None
        self.log('info', 'Interface prête. Choisis tes paramètres, puis démarre le hub.')

    def log(self, level, message):
        with self.lock:
            self.logs.append({'at': time.time(), 'level': level, 'message': message})

    def start(self, value):
        config = validate_config(value)
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError('Le hub est déjà en cours. Arrête-le avant de modifier ses paramètres.')
            if config['mode'] == 'live':
                missing = [name for name in ('pump_wallet.txt', 'pump_token.txt') if not (self.root / 'data' / name).is_file()]
                if missing:
                    raise ValueError('Fichiers locaux manquants dans data : ' + ', '.join(missing))
                if not Path(retained_path('callout_alerts_telegram.session')).is_file():
                    raise ValueError('Initialise la connexion Telegram dans le terminal avec le hub avant le premier démarrage réel depuis l’interface.')
            self.config = config
            self.run_id = secrets.token_hex(8)
            self.event_path = self.directory / f'events-{self.run_id}.jsonl'
            self.stop_path = self.directory / f'stop-{self.run_id}'
            self.offset = 0
            self.health = {}
            mode_file = 'repost_live.sqlite3' if config['mode'] == 'live' else 'repost_simulation.sqlite3'
            command = [sys.executable, '-u', str(self.root / 'scripts' / 'callout_hub.py'),
                       '--events-file', str(self.event_path), '--stop-file', str(self.stop_path),
                       '--state', str(self.root / 'data' / mode_file), '--profile=' + config['profile'],
                       '--callers=' + ','.join(config['callers'])]
            for key in RANGES:
                command += ['--' + key.replace('_', '-'), str(int(config[key])) if key == 'slippage_bps' else str(config[key])]
            command += ['--live'] if config['mode'] == 'live' else ['--dry-run']
            environment = os.environ.copy()
            environment['PYTHONIOENCODING'] = 'utf-8'
            environment['PYTHONUNBUFFERED'] = '1'
            self.process = subprocess.Popen(command, cwd=str(self.root), stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding='utf-8', errors='replace', env=environment)
            self.started_at = time.time()
            self.phase = 'starting'
            (self.directory / 'settings.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            threading.Thread(target=self.capture, args=(self.process, self.run_id), daemon=True).start()
            self.log('info', 'Démarrage demandé en ' + ('réel.' if config['mode'] == 'live' else 'simulation. Aucun envoi ni transaction en simulation.'))

    def capture(self, process, run_id):
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            line = safe_message(line)
            level = 'error' if any(word in line.lower() for word in ('erreur', 'refus', '429', '500', 'impossible', 'traceback', 'error')) else 'success' if any(word in line for word in ('CONFIRMÉ', 'PUBLIÉ', 'Achat confirmé')) else 'info'
            with self.lock:
                if run_id != self.run_id:
                    break
                if line.startswith('HUB :') and self.phase != 'stopping':
                    self.phase = 'running'
                self.log(level, line[:1500])
        code = process.wait()
        with self.lock:
            if run_id == self.run_id:
                self.phase = 'stopped' if code == 0 or self.phase == 'stopping' else 'error'
                self.log('info' if self.phase == 'stopped' else 'error', f'Processus terminé (code {code}). États conservés.')

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.stop_path.write_text('stop', encoding='utf-8')
                self.phase = 'stopping'
                self.log('info', 'Arrêt demandé. Finalisation des opérations en cours…')

    def ingest(self):
        if not self.event_path or not self.event_path.is_file():
            return
        if self.event_path.stat().st_size < self.offset:
            self.offset = 0
        with self.event_path.open('rb') as stream:
            stream.seek(self.offset)
            while True:
                position = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b'\n'):
                    stream.seek(position)
                    break
                try:
                    event = json.loads(line)
                    if event['type'] == 'health':
                        self.health = dict(event['data'], at=event['at'])
                    elif event['type'] == 'callout':
                        call = event['data']
                        identity = str(call['calloutId'])
                        if identity not in self.ids:
                            self.ids.add(identity)
                            self.feed_count += 1
                            observed = dict(call, observed_at=event['at'])
                            self.feed.appendleft(observed)
                            if call.get('author') in CALLERS or call.get('author_name') in CALLERS.values():
                                self.caller_feed.appendleft(observed)
                except (ValueError, KeyError, TypeError):
                    pass
            self.offset = stream.tell()
        if len(self.ids) > 20000:
            self.ids = {str(call['calloutId']) for call in list(self.feed) + list(self.caller_feed)}

    def snapshot(self):
        with self.lock:
            try:
                self.ingest()
            except OSError:
                pass
            config = dict(self.config)
            journal = self.root / 'data' / ('repost_live.sqlite3' if config['mode'] == 'live' else 'repost_simulation.sqlite3')
            jobs, error = read_jobs(journal)
            confirmed = [j for j in jobs if j.get('buy_confirmed') or j['state'] in ('confirmed', 'publishing', 'uncertain', 'posted')]
            positions = [j for j in confirmed if j.get('exit_state') not in ('sold', 'empty')]
            known_costs = all(number(j.get('mark_cost_usd', j.get('cost_usd'))) is not None for j in positions)
            cost = sum(number(j.get('mark_cost_usd', j.get('cost_usd'))) for j in positions) if known_costs else None
            marked = [j for j in positions if not j.get('tp_error') and time.time() - (number(j.get('mark_checked_at')) or 0) < 60 and number(j.get('mark_usd')) is not None and number(j.get('mark_cost_usd', j.get('cost_usd'))) is not None]
            pnl = sum(number(j['mark_usd']) - number(j.get('mark_cost_usd', j.get('cost_usd'))) for j in marked) if marked and len(marked) == len(positions) else None
            return {'phase': self.phase, 'running': bool(self.process and self.process.poll() is None),
                    'config': config, 'callers': list(CALLERS.values()), 'started_at': self.started_at,
                    'health': self.health, 'feed_received': self.feed_count, 'feed': list(self.feed), 'logs': list(self.logs),
                    'caller_feed': list(self.caller_feed), 'caller_wallets': dict(CALLERS),
                    'jobs': jobs, 'journal_error': error,
                    'summary': {'positions': len(positions), 'buys': len(confirmed), 'cost': cost, 'pnl': pnl},
                    'credentials': {'wallet': (self.root/'data/pump_wallet.txt').is_file(),
                                    'pump_session': (self.root/'data/pump_token.txt').is_file(),
                                    'telegram': Path(retained_path('callout_alerts_telegram.session')).is_file(),
                                    'jupiter': bool(os.getenv('JUP_API_KEY')),
                                    'rpc_personal': bool(os.getenv('SOLANA_RPC_URL'))}}

    def close(self):
        self.stop()
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=50)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)


def make_server(controller, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, body, content_type='application/json; charset=utf-8'):
            raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self, mutation=False):
            port = self.server.server_port
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if self.headers.get('Host') not in hosts:
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + host for host in hosts}:
                return False
            return not mutation or secrets.compare_digest(self.headers.get('X-Desk-Token', ''), controller.token)

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error': 'Accès local uniquement.'})
            if self.path == '/api/state':
                return self.reply(200, controller.snapshot())
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if self.path not in assets:
                return self.reply(404, {'error': 'Introuvable.'})
            name, mime = assets[self.path]
            raw = (ROOT/'web'/name).read_bytes()
            if name == 'index.html':
                raw = raw.replace(b'__DESK_TOKEN__', controller.token.encode())
            self.reply(200, raw, mime)

        def do_POST(self):
            if not self.allowed(mutation=True):
                return self.reply(403, {'error': 'Requête locale non autorisée.'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size < 16000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('Requête invalide.')
                data = json.loads(self.rfile.read(size))
                if self.path == '/api/start':
                    controller.start(data)
                elif self.path == '/api/stop':
                    controller.stop()
                else:
                    return self.reply(404, {'error': 'Introuvable.'})
                self.reply(200, {'ok': True})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except OSError:
                self.reply(500, {'error': 'Impossible de lancer le processus local.'})
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    controller = Dashboard()
    try:
        server = make_server(controller, args.port)
    except OSError:
        raise SystemExit('Port local indisponible. Ferme l’autre interface ou choisis --port 8766.') from None
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Tableau de bord : {url}\nGarde ce terminal ouvert. Ctrl+C pour fermer.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.server_close()


if __name__ == '__main__':
    main()
