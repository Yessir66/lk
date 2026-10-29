"""Telegram CA relay; Trojan alone executes trades. Solana is read-only here."""
import asyncio
import copy
import re
import secrets
import time

from callout_alerts import RemoteError, WALLET
from callout_repost import Execution, Engine


class TrojanRelay:
    def __init__(self, telegram, target):
        if not re.fullmatch(r'@[A-Za-z0-9_]{5,32}', target):
            raise RemoteError('Identifiant Telegram Trojan invalide')
        self.telegram, self.target = telegram, target
        async def resolve():
            peer = await telegram.client.get_entity(target)
            if not getattr(peer, 'bot', False) or ('@' + (getattr(peer, 'username', '') or '')).lower() != target.lower():
                raise RemoteError('La destination Trojan doit être le bot Telegram configuré')
            return peer
        self.peer = self.wait(resolve())

    def wait(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.telegram.loop)
        try:
            return future.result(timeout=15)
        except Exception:
            future.cancel()
            raise RemoteError('Telegram Trojan indisponible ; vérifier le dialogue et la session') from None

    def send_ca(self, mint, random_id):
        if not WALLET.fullmatch(mint):
            raise RemoteError('CA invalide')
        async def send():
            from telethon.tl.functions.messages import SendMessageRequest
            # The persistent MTProto random_id also protects against transport retries.
            await self.telegram.client(SendMessageRequest(peer=self.peer, message=mint,
                                                         random_id=random_id, no_webpage=True))
        self.wait(send())


class TrojanExecution(Execution):
    def __init__(self, args, owner, relay):
        readonly = copy.copy(args)
        readonly.live = False
        super().__init__(readonly, owner)
        self.args, self.relay = args, relay
        if args.live:
            self.check_auth()

    def rpc(self, method, params):
        if not method.startswith('get'):
            raise RemoteError('RPC en lecture seule en mode Trojan')
        return super().rpc(method, params)

    def prepare(self, *args, **kwargs):
        raise RemoteError('Achats locaux désactivés : exécution par Trojan')

    def sign_quote(self, *args, **kwargs):
        raise RemoteError('Ventes locales désactivées : exécution par Trojan')

    def broadcast(self, *args, **kwargs):
        raise RemoteError('Diffusion locale désactivée : exécution par Trojan')

    def jupiter(self, *args, **kwargs):
        raise RemoteError('Jupiter désactivé en mode Trojan')

    def find_purchase(self, job):
        rows = self.rpc('getSignaturesForAddress', [self.owner, {'limit': 30, 'commitment': 'confirmed'}])
        checked = set(job.get('trojan_checked', []))
        inspected = 0
        for row in reversed(rows):
            signature = row['signature']
            if row.get('err') is not None or row['slot'] < job['trojan_baseline_slot'] or signature in checked:
                continue
            if inspected >= 3:
                break
            inspected += 1
            tx = self.transaction(signature)
            if not tx or not tx.get('meta'):
                continue
            keys = tx['transaction']['message']['accountKeys']
            signer = any(k.get('pubkey') == self.owner and k.get('signer') for k in keys)
            def total(side):
                return sum(int(b['uiTokenAmount']['amount']) for b in tx['meta'].get(side, [])
                           if b.get('owner') == self.owner and b.get('mint') == job['mint'])
            gained = total('postTokenBalances') - total('preTokenBalances')
            if signer and tx['meta'].get('err') is None and gained > 0:
                return signature, gained, tx.get('blockTime')
            checked.add(signature)
        job['trojan_checked'] = list(checked)[-100:]
        return None


class TrojanEngine(Engine):
    """Never sign or send a Solana transaction, including for old local jobs."""
    def __init__(self, journal, execution, args):
        super().__init__(journal, execution, args)
        for job in journal.jobs():
            if job.get('executor') == 'trojan':
                continue
            if job['state'] == 'new':
                job.update(state='failed', failure_reason='executor_changed')
            elif job['state'] == 'signed':
                job.update(state='review', failure_reason='executor_changed_signed')
            if job.get('exit_state') == 'signed':
                job['exit_state'] = 'review'
            journal.save(job)

    def advance(self, job, now):
        if job['state'] == 'new' and job.get('executor') == 'trojan':
            if now < job['buy_at']:
                return
            if now - job['created'] > self.args.max_age:
                self.expire_entry(job)
                self.journal.save(job)
                return
            if not self.args.live:
                job['state'] = 'simulated'
                self.journal.save(job)
                print(f'SIMULATION TROJAN : CA non envoyé, {job["mint"]}', flush=True)
                return
            # Snapshot BEFORE dispatch. A pre-existing holding is never purchase proof.
            started = time.monotonic()
            slot = self.execution.rpc('getSlot', [{'commitment': 'confirmed'}])
            balance = self.execution.token_amount(job['mint'], fresh=True)
            history = self.execution.rpc('getSignaturesForAddress', [self.execution.owner, {'limit': 30, 'commitment': 'confirmed'}])
            now += time.monotonic() - started
            if now - job['created'] > self.args.max_age:
                self.expire_entry(job)
                self.journal.save(job)
                return
            job.update(state='trojan_waiting', trojan_sent_at=now, trojan_baseline_slot=slot,
                       trojan_baseline_balance=balance, trojan_random_id=secrets.randbits(63) or 1,
                       trojan_checked=[r['signature'] for r in history], first_send_at=now, retry_at=now + 2)
            self.journal.save(job)  # Crash/timeout: never send a second CA automatically.
            try:
                self.execution.relay.send_ca(job['mint'], job['trojan_random_id'])
                job['trojan_delivery'] = 'accepted'
                print(f'CA ENVOYÉ À TROJAN : {job["caller"]} → {job["mint"]} ; achat non encore confirmé.', flush=True)
            except RemoteError:
                job['trojan_delivery'] = 'uncertain'
                print(f'ENVOI TROJAN INCERTAIN : {job["mint"]} ; aucun renvoi automatique.', flush=True)
            self.journal.save(job)
            return
        if job['state'] == 'trojan_waiting':
            # A Telegram acknowledgement is NOT proof of an on-chain purchase.
            if now - job['trojan_sent_at'] > 180:
                job.update(state='review', failure_reason='trojan_purchase_unconfirmed')
                print(f'ACHAT TROJAN À VÉRIFIER : {job["mint"]} ; aucun nouvel achat automatique.', flush=True)
            else:
                found = self.execution.find_purchase(job)
                if found:
                    signature, gained, block_time = found
                    job.update(state='confirmed', signature=signature, buy_confirmed=True,
                               confirmed_at=now, position_tokens=gained, trojan_buy_time=block_time,
                               exit_state='external', retry_at=0)
                    self.journal.save(job)
                    print(f'Achat Trojan confirmé sur Solana : {job["mint"]}', flush=True)
                    super().advance(job, now)
                else:
                    job['retry_at'] = now + 3
            self.journal.save(job)
            return
        return super().advance(job, now)

    def manage_exit(self, job, now):
        # Trojan owns exits. Local RPC reads only, never local quotes/signing/selling.
        if not job.get('buy_confirmed') or job.get('exit_state') in ('sold', 'empty', 'review'):
            return
        if job.get('executor') != 'trojan':
            job['exit_state'] = 'external_legacy'
            self.journal.save(job)
            return
        if now < job.get('external_check_at', 0):
            return
        if self.execution.token_amount(job['mint'], fresh=True) <= 0:
            job['exit_state'] = 'empty'  # No invented TP/SL or sale signature.
        else:
            job['exit_state'] = 'external'
        job['external_check_at'] = now + 15
        self.journal.save(job)

    def collect_details(self, now, entry_pending=None):
        # The amount is set in Trojan. Never present the dashboard USD setting as a fill.
        return
