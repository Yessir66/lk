import json
import asyncio
import queue
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import callout_repost as r
import callout_trojan as t
import callout_workflow as workflow
from callout_alerts import RemoteError

MINT = r.SOL
AUTHOR = next(k for k, v in r.CALLERS.items() if v == 'YourMistyy')


class TrojanTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.journal = r.Journal(Path(temp.name) / 'journal', 999, 'owner')
        self.addCleanup(self.journal.db.close)
        self.args = SimpleNamespace(executor='trojan', live=True, max_age=60, usd=Decimal(2),
                                    take_profit=Decimal(50), tp_interval=5)
        self.execution = MagicMock()
        self.execution.owner = 'owner'
        self.execution.rpc.side_effect = lambda method, params: 100 if method == 'getSlot' else []
        self.execution.token_amount.return_value = 900
        self.execution.find_purchase.return_value = None
        self.execution.holds.return_value = True
        self.execution.pump_call.return_value = (201, json.dumps({'calloutId': '11111111-1111-4111-8111-111111111111'}))
        self.engine = t.TrojanEngine(self.journal, self.execution, self.args)
        clock = patch.object(r.time, 'monotonic', return_value=1)
        clock.start()
        self.addCleanup(clock.stop)

    def add(self):
        self.journal.add(dict(calloutId='source', author=AUTHOR, coinMint=MINT, createdAt=1000),
                         1001, 60, 0, None, buy_delay=12)
        job = self.job()
        job['executor'] = 'trojan'
        self.journal.save(job)

    def job(self):
        return self.journal.jobs()[0]

    def assert_no_local_trade(self):
        for name in ('prepare', 'sign_quote', 'broadcast', 'jupiter', 'exit_quote'):
            getattr(self.execution, name).assert_not_called()

    def test_send_once_after_5_seconds_then_confirm_before_publication(self):
        self.add()
        self.engine.step(1004.99)
        self.execution.relay.send_ca.assert_not_called()
        self.engine.step(1005)
        self.execution.relay.send_ca.assert_called_once()
        self.assertEqual(self.job()['state'], 'trojan_waiting')
        self.execution.pump_call.assert_not_called()
        self.engine.step(1008)
        self.execution.pump_call.assert_not_called()
        self.execution.find_purchase.return_value = ('onchain-buy', 123, 1007)
        self.engine.step(1011)
        job = self.job()
        self.assertTrue(job['buy_confirmed'])
        self.assertEqual(job['position_tokens'], 123)
        self.assertEqual(job['signature'], 'onchain-buy')
        self.assertEqual(job['state'], 'posted')
        self.execution.pump_call.assert_called_once()
        self.assertNotIn('cost_usd', job)  # Dashboard's $2 is not a Trojan fill.
        self.assert_no_local_trade()

    def test_timeout_restart_never_sends_again_or_publishes_from_existing_balance(self):
        self.add()
        self.execution.relay.send_ca.side_effect = RemoteError('timeout')
        self.engine.step(1005)
        self.assertEqual(self.job()['trojan_delivery'], 'uncertain')
        restarted = t.TrojanEngine(self.journal, self.execution, self.args)
        restarted.step(1010)
        restarted.step(1186)
        self.execution.relay.send_ca.assert_called_once()
        self.assertEqual(self.job()['state'], 'review')
        self.execution.pump_call.assert_not_called()
        self.assert_no_local_trade()

    def test_crash_after_persisting_before_dispatch_never_rebuys(self):
        self.add()
        self.execution.relay.send_ca.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.engine.step(1005)
        self.assertEqual(self.job()['state'], 'trojan_waiting')
        t.TrojanEngine(self.journal, self.execution, self.args).step(1010)
        self.execution.relay.send_ca.assert_called_once()

    def test_simulation_and_expired_calls_never_send(self):
        self.add()
        self.args.live = False
        self.engine.step(1005)
        self.assertEqual(self.job()['state'], 'simulated')
        self.execution.relay.send_ca.assert_not_called()
        job = self.job()
        job['state'] = 'new'
        self.journal.save(job)
        self.args.live = True
        self.engine.step(1061)
        self.assertEqual(self.job()['failure_reason'], 'entry_expired')
        self.execution.relay.send_ca.assert_not_called()
        self.assert_no_local_trade()

    def test_old_pending_orders_are_not_dispatched_and_existing_positions_not_sold(self):
        self.add()
        job = self.job()
        job.pop('executor')
        self.journal.save(job)
        t.TrojanEngine(self.journal, self.execution, self.args).step(1005)
        self.assertEqual(self.job()['failure_reason'], 'executor_changed')
        job = self.job()
        job.update(state='posted', buy_confirmed=True, cost_usd='2', exit_state='watching')
        self.journal.save(job)
        self.engine.step(1010)
        self.assertEqual(self.job()['exit_state'], 'external_legacy')
        self.assert_no_local_trade()

    def test_external_balance_zero_does_not_invent_a_tp_sale(self):
        self.add()
        job = self.job()
        job.update(state='posted', buy_confirmed=True, exit_state='external')
        self.journal.save(job)
        self.execution.token_amount.return_value = 0
        self.engine.step(1010)
        self.assertEqual(self.job()['exit_state'], 'empty')
        self.assertNotIn('exit_reason', self.job())
        self.assert_no_local_trade()

    def test_find_purchase_requires_success_signer_and_positive_token_delta(self):
        execution = object.__new__(t.TrojanExecution)
        execution.owner = 'owner'
        rows = [dict(signature=s, slot=100, err=None) for s in ('valid', 'transfer', 'sale', 'baseline')]
        execution.rpc = MagicMock(return_value=rows)
        def tx(signature):
            before, after, signer = (0, 42, True) if signature == 'valid' else (0, 42, False) if signature == 'transfer' else (42, 0, True)
            b = lambda n: [dict(owner='owner', mint=MINT, uiTokenAmount={'amount': str(n)})]
            return dict(blockTime=1006, meta=dict(err=None, preTokenBalances=b(before), postTokenBalances=b(after)),
                        transaction={'message': {'accountKeys': [dict(pubkey='owner', signer=signer)]}})
        execution.transaction = MagicMock(side_effect=tx)
        job = dict(mint=MINT, trojan_baseline_slot=100, trojan_checked=['baseline'])
        self.assertEqual(execution.find_purchase(job), ('valid', 42, 1006))
        # Same-slot new purchases remain eligible, baseline signatures do not.
        self.assertEqual(execution.transaction.call_count, 3)

    def test_local_transactions_are_blocked_without_loading_any_key(self):
        execution = object.__new__(t.TrojanExecution)
        for method in ('prepare', 'sign_quote', 'broadcast', 'jupiter'):
            with self.assertRaises(RemoteError):
                getattr(execution, method)('anything')
        with self.assertRaises(RemoteError):
            execution.rpc('sendTransaction', ['anything'])

    def test_relay_sends_only_the_ca_to_the_configured_bot_with_persistent_id(self):
        peer = SimpleNamespace(bot=True, username='solana_trojanbot', id=123)
        client = AsyncMock()
        client.get_entity.return_value = peer
        telegram = SimpleNamespace(client=client)
        with patch.object(t.TrojanRelay, 'wait', lambda self, coro: asyncio.run(coro)):
            relay = t.TrojanRelay(telegram, '@solana_trojanbot')
            relay.send_ca(MINT, 456)
        request = client.call_args.args[0]
        self.assertEqual(request.message, MINT)
        self.assertEqual(request.random_id, 456)
        self.assertIs(request.peer, peer)
        self.assertTrue(request.no_webpage)
        peer.bot = False
        with patch.object(t.TrojanRelay, 'wait', lambda self, coro: asyncio.run(coro)):
            with self.assertRaises(RemoteError):
                t.TrojanRelay(telegram, '@solana_trojanbot')

    def test_content_prepared_during_trojan_wait_is_not_lost(self):
        self.add()
        job = self.job()
        job['state'] = 'trojan_waiting'
        self.journal.save(job)
        flow = object.__new__(workflow.Workflow)
        flow.journal, flow.args = self.journal, self.args
        flow.update_events = queue.Queue()
        flow.watcher = MagicMock()
        flow.content = MagicMock()
        flow.content.take.return_value = dict(text='Prepared context', image_path='image-path', rewrite_method='local')
        flow.tick(1006)
        self.assertEqual(self.job()['text'], 'Prepared context')
        self.assertEqual(self.job()['image_path'], 'image-path')
        self.assertTrue(self.job()['content_ready'])


if __name__ == '__main__':
    unittest.main()
