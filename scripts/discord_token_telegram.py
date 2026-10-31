"""One persistent Telegram summary per scout token; no trading operations."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import time

from curl_cffi import requests
from discord_token_analysis import Store, WATCHED, paris, verified_buy_wallets


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
    qualifying = verified_buy_wallets(result)
    lines = ['🔔 ACHAT ANTÉRIEUR CONFIRMÉ : ' + ', '.join(qualifying),
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
    if result.get('analysis_started_at') is not None and result.get('analyzed_at') is not None:
        lines.append('Préparation du bilan après signal : ' + number(max(0, result['analyzed_at'] - result['signal_at']), 1) + ' s (hors envoi Telegram).')
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

    def clone(self):
        return TelegramSender(self.token,self.chat)

    def send(self, text, message_id=None, *, reply_to=None):
        method = 'editMessageText' if message_id is not None else 'sendMessage'
        payload = {'chat_id': self.chat, 'text': text, 'link_preview_options': {'is_disabled': True}}
        if message_id is not None:
            payload['message_id'] = message_id
        if reply_to is not None:
            payload['reply_parameters']={'message_id':reply_to,'allow_sending_without_reply':False}
        return self._request(method,payload,message_id=message_id)

    def send_reply(self,text,reply_to):
        return self.send(text,reply_to=reply_to)

    def send_photo(self,path,caption,reply_to,mime):
        from curl_cffi import CurlMime
        from pathlib import Path
        try:
            data=Path(path).read_bytes()
        except OSError:
            raise TelegramFailure('Image narrative locale indisponible') from None
        if len(data)>1500*1024:raise TelegramFailure('Image narrative trop volumineuse')
        multipart=CurlMime()
        try:
            multipart.addpart(name='photo',filename=Path(path).name,content_type=mime,data=data)
            return self._request('sendPhoto',{'chat_id':self.chat,'caption':caption,
                'reply_parameters':{'message_id':reply_to,'allow_sending_without_reply':False}},multipart=multipart)
        finally:multipart.close()

    def _request(self,method,payload,message_id=None,multipart=None):
        options=({'json':payload} if multipart is None else
                 {'data':{k:json.dumps(v) if isinstance(v,(dict,list)) else str(v) for k,v in payload.items()},'multipart':multipart})
        try:
            response = self.http.post(f'https://api.telegram.org/bot{self.token}/{method}', timeout=15, allow_redirects=False,**options)
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
        for column in ('sent_at', 'signal_to_sent_ms'):
            if column not in {r[1] for r in self.db.execute('PRAGMA table_info(telegram_reports)')}:
                self.db.execute('ALTER TABLE telegram_reports ADD COLUMN ' + column + ' REAL')
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
            result = json.loads(data) if data else None
            old = self.db.execute('SELECT text,state FROM telegram_reports WHERE mint=?', (mint,)).fetchone()
            if not result or not verified_buy_wallets(result):
                if old is None:
                    self.db.execute("INSERT OR IGNORE INTO telegram_reports (mint,state) VALUES (?,'filtered')", (mint,))
                elif old[1] not in ('uncertain', 'sending', 'editing', 'skipped'):
                    self.db.execute("UPDATE telegram_reports SET state='filtered',error='Aucun achat antérieur vérifié' WHERE mint=?", (mint,))
                continue
            text = summary(result)
            if old is None:
                self.db.execute("INSERT OR IGNORE INTO telegram_reports (mint,text,state) VALUES (?,?,'pending')", (mint, text))
            elif old[1] in ('sent', 'pending', 'filtered') and old[0] != text:
                self.db.execute("UPDATE telegram_reports SET text=?,state='pending',retry_at=0 WHERE mint=?", (text, mint))
        self.db.commit()

    def deliver_one(self, sender, now):
        row = self.db.execute("SELECT mint,text,message_id FROM telegram_reports WHERE state='pending' AND retry_at<=? ORDER BY rowid LIMIT 1", (now,)).fetchone()
        if not row:
            return False
        mint, text, message_id = row
        signal = self.db.execute('SELECT result,signal_at FROM signals WHERE mint=?', (mint,)).fetchone()
        if not signal or not signal[0] or not verified_buy_wallets(json.loads(signal[0])):
            self.db.execute("UPDATE telegram_reports SET state='filtered' WHERE mint=?", (mint,))
            self.db.commit()
            return True
        self.db.execute('UPDATE telegram_reports SET state=? WHERE mint=?', ('editing' if message_id else 'sending', mint))
        self.db.commit()
        try:
            sent_id = sender.send(text, message_id)
            delivered = time.time()
            elapsed_ms = round(max(0, delivered - signal[1]) * 1000)
            if message_id is None:
                self.db.execute("UPDATE telegram_reports SET state='sent',message_id=?,sent_at=?,signal_to_sent_ms=?,error=NULL WHERE mint=?", (sent_id, delivered, elapsed_ms, mint))
            else:
                self.db.execute("UPDATE telegram_reports SET state='sent',message_id=?,error=NULL WHERE mint=?", (sent_id, mint))
            print('ALERTE TELEGRAM ' + ('MISE À JOUR : ' if message_id else 'ENVOYÉE : ') + mint
                  + f' ; signal→confirmation Telegram : {elapsed_ms} ms', flush=True)
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
