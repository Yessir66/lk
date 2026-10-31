"""One persistent Telegram summary per scout token; no trading operations."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import time

from curl_cffi import requests
from discord_token_analysis import Store, WATCHED, paris


def number(value, decimals=4):
    if value is None:
        return 'indisponible'
    try:
        n = Decimal(str(value))
        if not n.is_finite():
            return 'indisponible'
        text = format(n, f'.{decimals}f').rstrip('0').rstrip('.') if decimals else format(n, '.0f')
        return text if len(text) < 40 else format(n, '.6g')
    except (ValueError, InvalidOperation):
        return 'indisponible'


def amounts(values):
    if not values:
        return 'aucun montant retrouvé'
    parts = [number(v, 6) + ' ' + str(asset) for asset, v in sorted(values.items())[:3]]
    if len(values) > 3:
        parts.append(f'+ {len(values)-3} autres devises (rapport local)')
    return ' + '.join(parts)


def summary(result):
    lines = ['🔎 BILAN DU SIGNAL DISCORD',
             (str(result.get('name') or 'Token')[:70] + ' · $' + str(result.get('symbol') or '?')[:20]),
             str(result['mint']), '', 'Signal : ' + paris(result['signal_at']) + ' (Paris)',
             'Création : ' + (paris(result['created_at']) if result.get('created_at') is not None else 'indisponible')]
    if result.get('created_at') is not None:
        age = max(0, result['signal_at'] - result['created_at'])
        lines.append(f'Âge au signal : {int(age//3600)} h {int(age%3600//60)} min {int(age%60)} s')
    for name in WATCHED:
        wallet = result.get('wallets', {}).get(name, {})
        lines += ['', '👛 ' + name]
        if not wallet.get('available'):
            lines.append('Lecture indisponible : ' + str(wallet.get('reason') or 'données manquantes')[:160])
            continue
        first = wallet.get('first_buy')
        lines.append(f'Achats retrouvés : {wallet.get("buys", 0)} · Ventes : {wallet.get("sells", 0)}')
        if first:
            lines += ['Premier achat retrouvé : ' + paris(first['at']) + ' (Paris)',
                      number(first['quantity']) + ' tokens pour ' + amounts(first.get('quotes', {}))]
        else:
            lines.append('Aucun achat retrouvé dans l’historique couvert.')
        totals = wallet.get('quote_totals', {})
        lines += ['Total payé : ' + amounts(totals.get('buy', {})),
                  'Produit des ventes : ' + amounts(totals.get('sell', {})),
                  'Tokens achetés : ' + number(wallet.get('bought_quantity')),
                  'Tokens vendus : ' + number(wallet.get('sold_quantity'))]
        initial, all_buys = wallet.get('sold_percent_initial_buy'), wallet.get('sold_percent_all_buys')
        lines += ['Vendu / premier achat : ' + (number(initial, 2) + ' %' if initial is not None else 'indisponible'),
                  'Vendu / cumul acheté : ' + (number(all_buys, 2) + ' %' if all_buys is not None else 'indisponible')]
        if wallet.get('history_limited'):
            lines.append('⚠️ Historique incomplet : totaux partiels, ratio sur le cumul potentiellement biaisé.')
        if wallet.get('first_buy_order_ambiguous'):
            lines.append('⚠️ Ordre des premiers achats non établi dans le même slot.')
        if wallet.get('unclassified_movements'):
            lines.append('⚠️ Transferts/mouvements non attribuables exclus des achats et ventes.')
        if wallet.get('same_second_as_signal'):
            lines.append('⚠️ Transaction dans la seconde du signal : ordre exact incertain.')
    callouts = result.get('callouts', {})
    count = callouts.get('count')
    lines += ['', '📣 CALLOUTS : CRÉATION → SIGNAL',
              (str(count) + ' callouts publics retrouvés (minimum)' if count is not None else 'Nombre indisponible')]
    if callouts.get('errors') or any(not c.get('exhausted') for c in callouts.get('coverage', {}).values()):
        lines.append('⚠️ Collecte des callouts partielle ou interrompue.')
    if result.get('metadata_error'):
        lines.append('⚠️ Heure de création non vérifiée : métadonnées indisponibles.')
    lines += ['Réponses/updates/reposts exclus ; publications masquées ou supprimées potentiellement absentes.',
              '', 'Bilan arrêté au signal. Pourcentages = quantités de tokens, pas rendement ni capital récupéré.']
    text = '\n'.join(lines)
    # Telegram counts UTF-16 units. Keep one message even with unusually long metadata.
    if len(text.encode('utf-16-le')) // 2 > 3900:
        text = text.encode('utf-16-le')[:7400].decode('utf-16-le', errors='ignore')
        text += '\n… Détail complet conservé dans le rapport local.'
    return text


class TelegramFailure(Exception):
    def __init__(self, message, ambiguous=False, retry_after=None):
        super().__init__(message)
        self.ambiguous, self.retry_after = ambiguous, retry_after


class TelegramSender:
    def __init__(self, token, chat):
        self.token, self.chat = token, str(chat)
        self.destination = hashlib.sha256((token + ':' + self.chat).encode()).hexdigest()
        self.http = requests.Session(impersonate='chrome')

    def send(self, text, message_id=None):
        method = 'editMessageText' if message_id is not None else 'sendMessage'
        payload = {'chat_id': self.chat, 'text': text, 'link_preview_options': {'is_disabled': True}}
        if message_id is not None:
            payload['message_id'] = message_id
        try:
            response = self.http.post(f'https://api.telegram.org/bot{self.token}/{method}', json=payload, timeout=15, allow_redirects=False)
        except Exception:
            raise TelegramFailure('Telegram : réponse réseau inconnue', ambiguous=True) from None
        if response.status_code >= 500:
            raise TelegramFailure('Telegram : erreur serveur, réception inconnue', ambiguous=True)
        try:
            data = response.json()
        except Exception:
            raise TelegramFailure('Telegram : réponse illisible', ambiguous=True) from None
        if data.get('ok') is True:
            identity = data.get('result', {}).get('message_id')
            if not isinstance(identity, int):
                raise TelegramFailure('Telegram : identifiant de message absent', ambiguous=True)
            return identity
        code = data.get('error_code', response.status_code)
        code = code if isinstance(code, int) else 0
        if code == 429:
            delay = data.get('parameters', {}).get('retry_after', 30)
            delay = float(delay) if isinstance(delay, (int, float)) and math.isfinite(delay) else 30
            raise TelegramFailure('Telegram : quota temporaire', retry_after=max(1, delay))
        if message_id is not None and code == 400 and 'message is not modified' in str(data.get('description', '')).lower():
            return message_id
        raise TelegramFailure('Telegram : requête refusée (code ' + str(code) + ')')


class Outbox:
    def __init__(self, store, destination, include_history=False, retry_failed=False, recover=False):
        self.store, self.db = store, store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS telegram_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS telegram_reports (mint TEXT PRIMARY KEY, text TEXT, state TEXT,
                message_id INTEGER, retry_at REAL DEFAULT 0, error TEXT);
        ''')
        saved = self.db.execute("SELECT value FROM telegram_meta WHERE key='destination'").fetchone()
        if saved and saved[0] != destination:
            raise TelegramFailure('Destination Telegram modifiée : utiliser un autre --state pour éviter de mélanger les historiques')
        if not saved:
            self.db.execute("INSERT INTO telegram_meta VALUES ('destination',?)", (destination,))
            if not include_history:
                self.db.execute("INSERT OR IGNORE INTO telegram_reports (mint,state) SELECT mint,'skipped' FROM signals WHERE state IN ('done','error')")
        if include_history:
            self.db.execute("DELETE FROM telegram_reports WHERE state='skipped'")
        if retry_failed:
            self.db.execute("UPDATE telegram_reports SET state='pending',retry_at=0 WHERE state='failed'")
        # A process could have died after delivery but before saving the response.
        if recover:
            self.db.execute("UPDATE telegram_reports SET state='uncertain',error='Arrêt pendant l’envoi : vérifier Telegram' WHERE state='sending'")
            self.db.execute("UPDATE telegram_reports SET state='pending' WHERE state='editing'")
        self.db.commit()

    def queue(self):
        for mint, data, error, at in self.db.execute("SELECT mint,result,error,signal_at FROM signals WHERE state IN ('done','error')").fetchall():
            text = (summary(json.loads(data)) if data else
                    '\n'.join(['⚠️ ANALYSE DU SIGNAL INDISPONIBLE', mint, 'Signal : ' + paris(at) + ' (Paris)',
                               str(error or 'Données indisponibles')[:250],
                               'Aucun achat ni pourcentage ne peut être confirmé. Détail dans le rapport local.']))
            old = self.db.execute('SELECT text,state FROM telegram_reports WHERE mint=?', (mint,)).fetchone()
            if old is None:
                self.db.execute("INSERT OR IGNORE INTO telegram_reports (mint,text,state) VALUES (?,?,'pending')", (mint, text))
            elif old[1] in ('sent', 'pending') and old[0] != text:
                self.db.execute("UPDATE telegram_reports SET text=?,state='pending',retry_at=0 WHERE mint=?", (text, mint))
        self.db.commit()

    def deliver_one(self, sender, now):
        row = self.db.execute("SELECT mint,text,message_id FROM telegram_reports WHERE state='pending' AND retry_at<=? ORDER BY rowid LIMIT 1", (now,)).fetchone()
        if not row:
            return False
        mint, text, message_id = row
        self.db.execute('UPDATE telegram_reports SET state=? WHERE mint=?', ('editing' if message_id else 'sending', mint))
        self.db.commit()
        try:
            sent_id = sender.send(text, message_id)
            self.db.execute("UPDATE telegram_reports SET state='sent',message_id=?,error=NULL WHERE mint=?", (sent_id, mint))
            print('BILAN TELEGRAM ' + ('MIS À JOUR : ' if message_id else 'ENVOYÉ : ') + mint, flush=True)
        except TelegramFailure as exc:
            if message_id and exc.ambiguous:
                state, retry = 'pending', now + 30
            elif exc.ambiguous:
                state, retry = 'uncertain', 0
            elif exc.retry_after is not None:
                state, retry = 'pending', now + exc.retry_after
            else:
                state, retry = 'failed', 0
            self.db.execute('UPDATE telegram_reports SET state=?,retry_at=?,error=? WHERE mint=?', (state, retry, str(exc), mint))
            print(str(exc) + (' ; livraison incertaine, vérifier Telegram avant tout renvoi.' if state == 'uncertain' else ''), flush=True)
        self.db.commit()
        return True


def notification_worker(args, stop, sender):
    store = Store(args.state)
    try:
        outbox = Outbox(store, sender.destination)
        while not stop.is_set():
            outbox.queue()
            outbox.deliver_one(sender, time.time())
            stop.wait(1.1)
    except Exception as exc:
        print(str(exc) if isinstance(exc, TelegramFailure) else 'Notifications Telegram arrêtées : erreur locale', flush=True)
    finally:
        store.db.close()
