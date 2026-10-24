from pathlib import Path
from decimal import Decimal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import callout_repost as r

MINT = r.SOL
AUTHOR = next(iter(r.CALLERS))


def call(identity='one', mint=MINT, created=1000):
    return dict(calloutId=identity, author=AUTHOR, coinMint=mint, createdAt=created, thesis='Original thesis')


class RepostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'state')
        self.journal = r.Journal(self.path, 990, 'owner')
        self.args = SimpleNamespace(max_age=60, live=True, usd=2)
        self.execution = MagicMock()
        self.execution.prepare.return_value = dict(raw='same-signed-bytes', signature='sig', last_valid_height=100, lamports=10_000_000)
        self.execution.status.return_value = None
        self.execution.expired.return_value = False
        self.execution.holds.return_value = True
        self.execution.already_posted.return_value = False
        self.execution.pump_call.return_value = (201, '{}')
        self.engine = r.Engine(self.journal, self.execution, self.args)

    def tearDown(self):
        self.journal.db.close()
        self.tmp.cleanup()

    def add(self):
        self.journal.add(call(), 1001, 60, 15, '{text}')

    def job(self):
        return self.journal.jobs()[0]

    def confirmed(self):
        self.add()
        self.engine.step(1001)
        self.execution.status.return_value = {'err': None, 'confirmationStatus': 'confirmed'}
        self.engine.step(1002)

    def tp_position(self):
        self.confirmed()
        job = self.job()
        job.update(state='posted', cost_usd='2')
        self.journal.save(job)
        self.args.take_profit, self.args.tp_interval = Decimal('50'), 5
        self.execution.acquired.return_value = 1000
        self.execution.token_amount.return_value = 1000
        self.execution.sol_price.return_value = Decimal('100')
        self.execution.exit_quote.return_value = {'otherAmountThreshold': '30000000'}
        self.execution.sign_quote.return_value = dict(raw='signed-sell', signature='sell-sig', last_valid_height=200)
        self.execution.status.return_value = None

    def test_tp_waits_below_50_and_triggers_at_exact_boundary(self):
        self.tp_position()
        self.execution.exit_quote.return_value = {'otherAmountThreshold': '29999999'}
        self.engine.step(1010)
        self.execution.sign_quote.assert_not_called()
        self.engine.step(1011)
        self.assertEqual(self.execution.exit_quote.call_count, 1)
        self.execution.exit_quote.return_value = {'otherAmountThreshold': '30000000'}
        self.engine.step(1015)
        self.assertEqual(self.job()['exit_state'], 'signed')
        self.execution.sign_quote.assert_called_once()
        self.execution.broadcast.assert_not_called()

    def test_tp_sells_bought_quantity_not_preexisting_tokens(self):
        self.tp_position()
        self.execution.token_amount.return_value = 9000
        self.engine.step(1010)
        self.execution.exit_quote.assert_called_once_with(MINT, 1000)
        self.assertEqual(self.job()['exit_tokens'], 1000)

    def test_sell_restart_resends_same_bytes_and_stops_after_confirmation(self):
        self.tp_position()
        self.engine.step(1010)
        self.journal.db.close()
        self.journal = r.Journal(self.path, 1020, 'owner')
        self.engine = r.Engine(self.journal, self.execution, self.args)
        self.engine.step(1011)
        self.engine.step(1014)
        self.execution.sign_quote.assert_called_once()
        self.assertEqual([c.args for c in self.execution.broadcast.call_args_list], [('signed-sell',), ('signed-sell',)])
        self.execution.status.return_value = {'err': None, 'confirmationStatus': 'confirmed'}
        self.engine.step(1017)
        self.assertEqual(self.job()['exit_state'], 'sold')
        self.engine.step(1100)
        self.assertEqual(self.execution.exit_quote.call_count, 1)
        self.execution.status.assert_called_with('sell-sig')

    def test_failed_sell_can_retry_but_unknown_expiry_requires_review(self):
        self.tp_position()
        self.engine.step(1010)
        self.execution.status.return_value = {'err': 'failed', 'confirmationStatus': 'confirmed'}
        self.engine.step(1011)
        self.assertEqual(self.job()['exit_state'], 'watching')
        self.engine.step(1016)
        self.assertEqual(self.execution.sign_quote.call_count, 2)
        self.execution.status.return_value = None
        self.execution.expired.return_value = True
        self.engine.step(1017)
        self.assertEqual(self.job()['exit_state'], 'review')
        self.engine.step(1200)
        self.assertEqual(self.execution.sign_quote.call_count, 2)

    def test_tp_never_sells_in_simulation(self):
        self.tp_position()
        self.args.live = False
        self.engine.step(1010)
        self.execution.exit_quote.assert_not_called()
        self.execution.broadcast.assert_not_called()

    def test_tp_uses_usd_value_and_takes_priority_over_publication(self):
        self.tp_position()
        self.execution.sol_price.return_value = Decimal('50')
        self.engine.step(1010)
        self.execution.sign_quote.assert_not_called()  # 0.03 SOL is only $1.50 at this price.
        job = self.job()
        job['state'] = 'confirmed'
        self.journal.save(job)
        self.execution.sol_price.return_value = Decimal('100')
        self.engine.step(1015)
        self.execution.pump_call.assert_not_called()
        self.assertEqual(self.job()['exit_state'], 'signed')

    def test_acquired_reads_delta_and_excludes_other_wallet(self):
        execution = object.__new__(r.Execution)
        execution.owner = 'owner'
        def balance(owner, amount, index):
            return {'owner': owner, 'mint': MINT, 'accountIndex': index, 'uiTokenAmount': {'amount': str(amount)}}
        execution.rpc = MagicMock(return_value={'meta': {'err': None,
              'preTokenBalances': [balance('owner', 500, 1), balance('other', 100, 2)],
              'postTokenBalances': [balance('owner', 1500, 1), balance('other', 9999, 2)]}})
        self.assertEqual(execution.acquired('buy-sig', MINT), 1000)

    def test_sol_price_cached_for_five_seconds(self):
        execution = object.__new__(r.Execution)
        execution.jupiter = MagicMock(return_value={MINT: {'usdPrice': 200}})
        with patch.object(r.time, 'monotonic', return_value=10):
            self.assertEqual(execution.sol_price(), 200)
        with patch.object(r.time, 'monotonic', return_value=12):
            self.assertEqual(execution.sol_price(), 200)
        self.assertEqual(execution.jupiter.call_count, 1)
        with patch.object(r.time, 'monotonic', return_value=16):
            execution.sol_price()
        self.assertEqual(execution.jupiter.call_count, 2)

    def test_random_phrase_persisted_through_restart_and_duplicates(self):
        self.assertEqual(len(r.CALLOUT_TEXTS), 10)
        with patch.object(r.random, 'choice', return_value=r.CALLOUT_TEXTS[7]) as choose:
            self.assertTrue(self.journal.add(call(), 1001, 60, 15, None))
            self.assertFalse(self.journal.add(call('same-mint'), 1001, 60, 15, None))
            choose.assert_called_once_with(r.CALLOUT_TEXTS)
        selected = self.job()['text']
        self.journal.db.close()
        self.journal = r.Journal(self.path, 1100, 'owner')
        self.assertEqual(self.job()['text'], selected)
        self.assertEqual(selected, 'chart looks healthy and the community is waking up')

    def test_ten_callers_and_exact_dollar_conversion(self):
        self.assertEqual(len(r.CALLERS), 10)
        self.assertEqual(r.lamports_for(2, 200), 10_000_000)
        self.assertEqual(r.lamports_for(2, 150), 13_333_333)
        for invalid in (0, -1, 'NaN', 'Infinity'):
            with self.assertRaises(r.RemoteError):
                r.lamports_for(2, invalid)

    def test_dedup_by_mint_and_reject_old_unselected(self):
        self.assertFalse(self.journal.add(call(created=900), 1001, 60, 15, '{text}'))
        self.assertFalse(self.journal.add(dict(call(), author='other'), 1001, 60, 15, '{text}'))
        self.assertTrue(self.journal.add(call(), 1001, 60, 15, '{text}'))
        self.assertFalse(self.journal.add(call('different-call-same-coin'), 1001, 60, 15, '{text}'))
        self.assertEqual(len(self.journal.jobs()), 1)

    def test_restart_uses_same_transaction_no_second_buy(self):
        self.add()
        self.engine.step(1001)
        self.assertEqual(self.job()['state'], 'signed')
        self.execution.broadcast.assert_not_called()
        self.journal.db.close()
        self.journal = r.Journal(self.path, 1100, 'owner')
        self.engine = r.Engine(self.journal, self.execution, self.args)
        self.engine.step(1002)
        self.engine.step(1005)
        self.execution.prepare.assert_called_once()
        self.assertEqual(self.execution.broadcast.call_args_list[0].args, ('same-signed-bytes',))
        self.assertEqual(self.execution.broadcast.call_args_list[1].args, ('same-signed-bytes',))
        self.execution.pump_call.assert_not_called()

    def test_publish_only_after_confirmation_and_15_seconds(self):
        self.confirmed()
        self.engine.step(1014)
        self.execution.pump_call.assert_not_called()
        self.engine.step(1015)
        self.execution.pump_call.assert_called_once_with({'coinMint': MINT, 'thesis': 'Original thesis'})
        self.assertEqual(self.job()['state'], 'posted')
        self.engine.step(1100)
        self.execution.pump_call.assert_called_once()

    def test_no_publication_when_transaction_fails(self):
        self.add()
        self.engine.step(1001)
        self.execution.status.return_value = {'err': {'InstructionError': [1, 'error']}, 'confirmationStatus': 'confirmed'}
        self.engine.step(1016)
        self.engine.step(1020)
        self.assertEqual(self.job()['state'], 'failed')
        self.execution.pump_call.assert_not_called()

    def test_no_new_purchase_when_signature_expired(self):
        self.add()
        self.engine.step(1001)
        self.execution.expired.return_value = True
        self.engine.step(1016)
        self.engine.step(1100)
        self.assertEqual(self.job()['state'], 'review')
        self.execution.prepare.assert_called_once()
        self.execution.broadcast.assert_not_called()

    def test_timeout_after_post_reconciles_without_posting_again(self):
        self.confirmed()
        self.execution.pump_call.side_effect = r.RemoteError('timeout')
        self.engine.step(1015)
        self.assertEqual(self.job()['state'], 'publishing')
        self.execution.already_posted.return_value = True
        self.engine.step(1026)
        self.assertEqual(self.job()['state'], 'posted')
        self.execution.pump_call.assert_called_once()
        self.execution.prepare.assert_called_once()

    def test_token_expiry_retry_does_not_buy_again(self):
        self.confirmed()
        self.execution.pump_call.return_value = (401, '{}')
        self.engine.step(1015)
        self.assertEqual(self.job()['state'], 'confirmed')
        self.execution.pump_call.return_value = (201, '{}')
        self.engine.step(1046)
        self.assertEqual(self.job()['state'], 'posted')
        self.execution.prepare.assert_called_once()

    def test_missing_holding_prevents_post(self):
        self.confirmed()
        self.execution.holds.return_value = False
        self.engine.step(1015)
        self.execution.pump_call.assert_not_called()

    def test_simulation_performs_no_trade_or_publish(self):
        self.add()
        self.args.live = False
        self.engine.step(1001)
        self.assertEqual(self.job()['state'], 'simulated')
        self.assertEqual(self.execution.mock_calls, [])

    def test_quote_mismatch_never_reaches_signing(self):
        execution = object.__new__(r.Execution)
        execution.args = SimpleNamespace(usd=2, slippage_bps=100)
        execution.owner = 'owner'
        execution.rpc = MagicMock(return_value={'value': 100_000_000})
        execution.jupiter = MagicMock(side_effect=[{MINT: {'usdPrice': 200}},
                                                 dict(inputMint=MINT, outputMint='wrong', inAmount='10000000')])
        with self.assertRaises(r.RemoteError):
            execution.prepare(MINT)
        self.assertEqual(execution.jupiter.call_count, 2)

    def test_insufficient_sol_prevents_swap_request(self):
        execution = object.__new__(r.Execution)
        execution.args = SimpleNamespace(usd=2, slippage_bps=100)
        execution.owner = 'owner'
        execution.rpc = MagicMock(return_value={'value': 100})
        execution.jupiter = MagicMock(return_value={MINT: {'usdPrice': 200}})
        with self.assertRaises(r.RemoteError):
            execution.prepare(MINT)
        self.assertEqual(execution.jupiter.call_count, 1)

    def test_multipart_publication_and_session_wallet_match(self):
        execution = object.__new__(r.Execution)
        token = Path(self.tmp.name) / 'token'
        token.write_text('test-session')
        execution.args = SimpleNamespace(token=str(token))
        execution.owner = 'owner'
        execution.http = MagicMock()
        response = execution.http.request.return_value
        response.status_code = 200
        response.text = '{"address":"wrong-wallet"}'
        with self.assertRaises(r.RemoteError):
            execution.check_auth()
        response.text = '{"address":"owner"}'
        execution.check_auth()
        status, _ = execution.pump_call({'coinMint': MINT, 'thesis': 'phrase choisie'})
        self.assertEqual(status, 200)
        kwargs = execution.http.request.call_args.kwargs
        self.assertTrue(kwargs['headers']['Content-Type'].startswith('multipart/form-data; boundary='))
        self.assertIn(b'name="thesis"', kwargs['data'])
        self.assertIn(b'phrase choisie', kwargs['data'])
        self.assertEqual(execution.http.request.call_args.args[0], 'POST')

    def test_real_solders_signing_uses_exact_quote_and_amount(self):
        import base64
        from solders.keypair import Keypair
        from solders.message import MessageV0
        from solders.hash import Hash
        from solders.transaction import VersionedTransaction
        kp = Keypair()
        message = MessageV0.try_compile(kp.pubkey(), [], [], Hash.default())
        tx = VersionedTransaction(message, [kp])
        execution = object.__new__(r.Execution)
        execution.args = SimpleNamespace(usd=2, slippage_bps=100)
        execution.owner, execution.keypair = str(kp.pubkey()), kp
        execution.rpc = MagicMock(return_value={'value': 100_000_000})
        quote = dict(inputMint=MINT, outputMint=MINT, inAmount='10000000')
        execution.jupiter = MagicMock(side_effect=[{MINT: {'usdPrice': 200}}, quote,
                                      {'swapTransaction': base64.b64encode(bytes(tx)).decode(), 'lastValidBlockHeight': 123}])
        prepared = execution.prepare(MINT)
        signed = VersionedTransaction.from_bytes(base64.b64decode(prepared['raw']))
        self.assertTrue(all(signed.verify_with_results()))
        self.assertEqual(prepared['lamports'], 10_000_000)
        self.assertEqual(Decimal(prepared['cost_usd']), Decimal('2'))
        self.assertEqual(prepared['signature'], str(signed.signatures[0]))
        self.assertIn('amount=10000000', execution.jupiter.call_args_list[1].args[0])
        self.assertEqual(execution.jupiter.call_args_list[2].args[1]['quoteResponse'], quote)


if __name__ == '__main__':
    unittest.main()
