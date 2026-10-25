from pathlib import Path
import io
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

    def http_execution(self):
        execution = object.__new__(r.Execution)
        execution.args = SimpleNamespace(rpc='https://rpc.example/?api-key=PRIVATE', jupiter='https://api.jup.ag')
        execution.owner = 'owner'
        execution.http = MagicMock()
        return execution

    def test_rpc_429_labels_method_redacts_url_and_blocks_rpc_but_not_jupiter(self):
        execution = self.http_execution()
        execution.http.request.return_value = SimpleNamespace(status_code=429, headers={'Retry-After': '120'})
        with patch.object(r.time, 'monotonic', return_value=10), patch.object(r.time, 'sleep'):
            with self.assertRaises(r.RemoteError) as caught:
                execution.rpc('getTokenAccountsByOwner', [])
        self.assertIn('RPC Solana/getTokenAccountsByOwner', str(caught.exception))
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(caught.exception.retry_after, 120)
        with patch.object(r.time, 'monotonic', return_value=11):
            with self.assertRaises(r.RemoteError): execution.rpc('getBalance', [])
        self.assertEqual(execution.http.request.call_count, 1)
        execution.http.request.return_value = SimpleNamespace(status_code=200, headers={}, json=lambda: {'result': 1})
        with patch.object(r.time, 'monotonic', return_value=12), patch.object(r.time, 'sleep'):
            execution.jupiter('/price/v3?ids=PUBLIC')
        with patch.object(r.time, 'monotonic', return_value=131), patch.object(r.time, 'sleep'):
            self.assertEqual(execution.rpc('getBalance', []), 1)
        self.assertEqual(execution.http.request.call_count, 3)

    def test_jupiter_rate_limit_progresses_and_does_not_expose_key(self):
        execution = self.http_execution()
        execution.http.request.return_value = SimpleNamespace(status_code=429, headers={})
        with patch.dict(r.os.environ, {'JUP_API_KEY': 'SECRET'}), patch.object(r.time, 'sleep'):
            for now, expected in [(10,15), (25,30), (55,60)]:
                with patch.object(r.time, 'monotonic', return_value=now):
                    with self.assertRaises(r.RemoteError) as caught:
                        execution.jupiter('/swap/v1/quote?inputMint=TOKEN')
                self.assertEqual(caught.exception.retry_after, expected)
                self.assertIn('Jupiter/swap/v1/quote', str(caught.exception))
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertNotIn('inputMint', str(caught.exception))

    def test_json_rpc_rate_error_and_preflight_error_are_distinguished(self):
        execution = self.http_execution()
        response = SimpleNamespace(status_code=200, headers={}, json=lambda: {
            'error': {'code': -32005, 'message': 'Too many requests SECRET'}})
        execution.http.request.return_value = response
        with patch.object(r.time, 'monotonic', return_value=10), patch.object(r.time, 'sleep'):
            with self.assertRaises(r.RemoteError) as limited: execution.rpc('getTransaction', [])
        self.assertEqual(limited.exception.status, 429)
        self.assertNotIn('SECRET', str(limited.exception))
        response.json = lambda: {'error': {'code': -32002, 'message': 'simulation failed'}}
        with patch.object(r.time, 'monotonic', return_value=30), patch.object(r.time, 'sleep'):
            with self.assertRaises(r.RemoteError) as preflight: execution.rpc('sendTransaction', [])
        self.assertIsNone(preflight.exception.status)
        self.assertIn('RPC Solana/sendTransaction', str(preflight.exception))
        self.assertIn('-32002', str(preflight.exception))

    def test_jupiter_request_pacing_is_shared_between_quote_and_price(self):
        execution = self.http_execution()
        execution.http.request.return_value = SimpleNamespace(status_code=200, headers={}, json=lambda: {})
        with patch.object(r.time, 'monotonic', return_value=10), patch.object(r.time, 'sleep') as sleep:
            execution.jupiter('/swap/v1/quote')
            execution.jupiter('/price/v3')
        self.assertEqual(sleep.call_args_list[-1].args, (1.0,))

    def test_wallet_balance_cache_and_fresh_check_before_sell(self):
        execution = self.http_execution()
        data = {'account': {'data': {'parsed': {'info': {'tokenAmount': {'amount': '1000'}}}}}}
        execution.rpc = MagicMock(return_value={'value': [data]})
        with patch.object(r.time, 'monotonic', return_value=10): self.assertEqual(execution.token_amount(MINT), 1000)
        with patch.object(r.time, 'monotonic', return_value=15): self.assertEqual(execution.token_amount(MINT), 1000)
        self.assertEqual(execution.rpc.call_count, 1)
        with patch.object(r.time, 'monotonic', return_value=15): execution.token_amount(MINT, fresh=True)
        self.assertEqual(execution.rpc.call_count, 2)
        with patch.object(r.time, 'monotonic', return_value=31): execution.token_amount(MINT)
        self.assertEqual(execution.rpc.call_count, 3)

    def test_tp_rate_limit_delays_job_without_rebuy_or_resign(self):
        self.tp_position()
        self.execution.exit_quote.side_effect = r.RemoteError('Jupiter 429', status=429, retry_after=120)
        self.engine.step(1010)
        self.engine.step(1015)
        self.assertGreaterEqual(self.job()['exit_retry_at'], 1130)
        self.execution.exit_quote.assert_called_once()
        self.execution.sign_quote.assert_not_called()
        self.execution.prepare.assert_called_once()  # Only the original purchase.

    def test_tp_retry_delay_starts_after_slow_failed_request(self):
        self.tp_position()
        self.execution.exit_quote.side_effect = r.RemoteError('Jupiter 429', status=429, retry_after=120)
        with patch.object(r.time, 'monotonic', side_effect=[10,40]):
            self.engine.step(1010)
        self.assertEqual(self.job()['exit_retry_at'], 1160)

    def test_tp_rechecks_quantity_before_signing(self):
        self.tp_position()
        self.execution.token_amount.side_effect = [1000, 500]
        self.engine.step(1010)
        self.execution.sign_quote.assert_not_called()
        self.execution.token_amount.assert_called_with(MINT, fresh=True)
        self.execution.token_amount.side_effect = None
        self.execution.token_amount.return_value = 500
        self.execution.exit_quote.return_value = {'otherAmountThreshold': '15000000'}
        self.engine.step(1015)
        self.assertEqual(self.job()['exit_tokens'], 500)
        self.execution.sign_quote.assert_called_once()

    def test_500_retries_only_before_age_limit_and_preserves_failure_reason(self):
        self.add()
        self.execution.prepare.side_effect = r.RemoteError('Jupiter/swap/v1/swap : HTTP 500', status=500)
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.engine.step(1010)
            self.engine.step(1015)
            self.assertEqual(self.execution.prepare.call_count, 1)
            self.engine.step(1021)
            self.assertEqual(self.execution.prepare.call_count, 2)
            self.engine.step(1055)
            self.assertEqual(self.job()['state'], 'failed')
            self.assertEqual(self.job()['failure_reason'], 'entry_expired')
            self.assertIn('HTTP 500', self.job()['last_error'])
            self.assertIn('ACHAT ABANDONNÉ', output.getvalue())
            self.assertIn('nouvelle tentative planifiée', output.getvalue())
            count = self.execution.prepare.call_count
            self.engine.step(1200)
            self.assertEqual(self.execution.prepare.call_count, count)
        self.execution.broadcast.assert_not_called()

    def test_expired_entry_is_reported_before_any_preparation(self):
        self.add()
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.engine.step(1061)
        self.assertIn('ACHAT ABANDONNÉ', output.getvalue())
        self.execution.prepare.assert_not_called()
        self.assertEqual(self.job()['failure_reason'], 'entry_expired')

    def test_status_is_read_only_and_hides_transaction_payloads(self):
        self.add()
        job = self.job()
        job.update(state='failed', failure_reason='entry_expired', raw='NEVER_PRINT_RAW', text='NEVER_PRINT_TEXT',
                   last_error='Jupiter/swap/v1/swap : HTTP 500', attempts=2)
        self.journal.save(job)
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            r.show_status(self.path)
        text = output.getvalue()
        self.assertIn('Aucune transaction d’achat signée enregistrée', text)
        self.assertIn('limite d’âge', text)
        self.assertIn('HTTP 500', text)
        self.assertNotIn('NEVER_PRINT', text)
        self.assertEqual(self.job(), job)
        missing = str(Path(self.tmp.name) / 'absent')
        with patch('sys.stdout', new_callable=io.StringIO): r.show_status(missing)
        self.assertFalse(Path(missing).exists())

    def test_swap_error_details_are_classified_without_printing_raw_body(self):
        execution = self.http_execution()
        response = SimpleNamespace(status_code=500, headers={}, json=lambda: {'error': 'Failed to simulate transaction SECRET'})
        execution.http.request.return_value = response
        with patch.object(r.time, 'sleep'):
            with self.assertRaises(r.RemoteError) as caught:
                execution.jupiter('/swap/v1/swap', {'quoteResponse': {}})
        self.assertIn('échec de simulation signalé', str(caught.exception))
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(caught.exception.status, 500)

    def test_check_swap_ignores_live_flag_and_never_signs_or_broadcasts(self):
        args = SimpleNamespace(live=True, check_swap=MINT, usd=Decimal('2'), slippage_bps=100)
        execution = MagicMock()
        execution.sol_price.return_value = Decimal('100')
        execution.jupiter.return_value = {'inputMint': r.SOL, 'outputMint': MINT, 'inAmount': '20000000'}
        with patch.object(r, 'Execution', return_value=execution) as factory, patch('sys.stdout', new_callable=io.StringIO):
            r.check_swap(args)
        self.assertFalse(factory.call_args.args[0].live)
        self.assertTrue(args.live)
        execution.build_swap.assert_called_once()
        execution.sign_quote.assert_not_called()
        execution.prepare.assert_not_called()
        execution.broadcast.assert_not_called()
        execution.pump_call.assert_not_called()

    def test_simulation_error_in_200_builder_response_prevents_signing(self):
        execution = object.__new__(r.Execution)
        execution.owner = 'owner'
        execution.jupiter = MagicMock(return_value={'swapTransaction': 'not-a-tx',
                     'lastValidBlockHeight': 123, 'simulationError': {'error': 'secret'}})
        with self.assertRaises(r.RemoteError) as error:
            execution.sign_quote({})
        self.assertIn('échec de simulation', str(error.exception))
        self.assertNotIn('secret', str(error.exception))

    def test_successful_tp_quote_clears_active_error_for_dashboard(self):
        self.tp_position()
        job=self.job();job['tp_error']='Jupiter : HTTP 429';self.journal.save(job)
        self.execution.exit_quote.return_value={'otherAmountThreshold':'29000000'}
        self.engine.step(1010)
        self.assertNotIn('tp_error',self.job())
        self.assertEqual(self.job()['exit_state'],'watching')

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
