"""Regression for the public DECU purchase reported by the user on 31 Oct 2026."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import discord_token_analysis as a

SIGNATURE='5oiP9vYXXGZFdE9NAMQWPA5mig3EFSdBmTzQAaxJVbBcgdWpvkYBLXrHWs28Ker8KpKRh6PVYz1vEbX1pWUN3eGi'
MINT='YdnQNdhWq76CYpx8UVA5W6DYD7yur91D9NAZLoCpump'
FIXTURE=Path(__file__).parent/'fixtures/decu_v1_buy.json'


class VersionOneTest(unittest.TestCase):
 def setUp(self):
  self.tx=json.loads(FIXTURE.read_text())
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=a.Store(Path(self.tmp.name)/'state');self.addCleanup(self.store.db.close)

 def test_actual_reported_v1_purchase_satisfies_unchanged_strict_proof(self):
  self.assertEqual(self.tx['version'],1)
  trade=a.parse_trade(self.tx,a.WATCHED['DECU'],MINT,SIGNATURE)
  self.assertEqual(trade['kind'],'buy')
  self.assertEqual(trade['swap_proof_version'],1)
  self.assertEqual(trade['quantity'],'22741914.376767')
  self.assertEqual(trade['quotes'],{'SOL':'5.01'})
  self.assertFalse(trade['unverified_swap_candidate'])

 def test_rpc_requests_v1_and_still_caches_confirmed_transactions(self):
  rpc=a.ReadRPC('https://unused.invalid',self.store)
  rpc.call=MagicMock(return_value=self.tx)
  self.assertEqual(rpc.transaction(SIGNATURE)['version'],1)
  self.assertEqual(rpc.call.call_args.args[1][1]['maxSupportedTransactionVersion'],1)
  rpc.transaction(SIGNATURE)
  rpc.call.assert_called_once()

 def test_real_purchase_qualifies_only_when_before_original_signal(self):
  rpc=MagicMock();at=self.tx['blockTime']
  rpc.call.return_value=[{'signature':SIGNATURE,'blockTime':at,'err':None}]
  rpc.transaction.return_value=self.tx
  snapshot=a.wallet_snapshot(rpc,a.WATCHED['DECU'],MINT,at-60,at+10)
  result={'mint':MINT,'created_at':at-60,'signal_at':at+10,'wallets':{'DECU':snapshot}}
  self.assertEqual(a.verified_buy_wallets(result),['DECU'])
  result['signal_at']=at
  self.assertEqual(a.verified_buy_wallets(result),[])

 def test_unsupported_individual_tx_does_not_erase_another_verified_buy(self):
  rpc=MagicMock();at=self.tx['blockTime']
  rpc.call.return_value=[{'signature':'newer-unsupported','blockTime':at+1,'err':None},
                         {'signature':SIGNATURE,'blockTime':at,'err':None}]
  rpc.transaction.side_effect=[a.RemoteError('Version inconnue',rpc_code=-32015),self.tx]
  snapshot=a.wallet_snapshot(rpc,a.WATCHED['DECU'],MINT,at-60,at+10)
  self.assertTrue(snapshot['available'])
  self.assertTrue(snapshot['history_limited'])
  self.assertEqual(snapshot['transactions_missing'],1)
  self.assertEqual(snapshot['buys'],1)
  self.assertEqual(snapshot['read_errors'][0]['rpc_code'],-32015)

 def test_transport_failure_is_not_reported_as_a_conclusive_absence(self):
  rpc=MagicMock();at=self.tx['blockTime']
  rpc.call.return_value=[{'signature':SIGNATURE,'blockTime':at,'err':None}]
  rpc.transaction.side_effect=a.RemoteError('RPC indisponible')
  snapshot=a.wallet_snapshot(rpc,a.WATCHED['DECU'],MINT,at-60,at+10)
  result={'mint':MINT,'signal_at':at+10,'wallets':{'DECU':snapshot}}
  self.assertFalse(snapshot['available'])
  self.assertIn('LECTURE INDISPONIBLE',a.wallet_diagnostic(result,'DECU'))
  self.assertEqual(a.verified_buy_wallets(result),[])

 def test_rpc_error_code_is_visible_without_provider_message_or_secrets(self):
  rpc=a.ReadRPC('https://unused.invalid',self.store)
  rpc.http=MagicMock();rpc.http.post.return_value.status_code=200
  rpc.http.post.return_value.json.return_value={'error':{'code':-32015,'message':'provider text with private-key'}}
  with self.assertRaises(a.RemoteError) as caught:rpc.call('getTransaction',[])
  self.assertEqual(caught.exception.rpc_code,-32015)
  self.assertIn('-32015',str(caught.exception));self.assertNotIn('private-key',str(caught.exception))

 def test_targeted_retry_preserves_discord_signal_and_deduplication(self):
  at=self.tx['blockTime']+10
  self.store.enqueue(MINT,'scout','original-message',at)
  self.store.finish(MINT,result={'mint':MINT})
  self.store.retry_mint(MINT)
  signal=self.store.next()
  self.assertEqual(signal['signal_at'],at)
  self.assertEqual(signal['message_id'],'original-message')
  self.assertFalse(self.store.enqueue(MINT,'scout','later-message',at+50))


if __name__=='__main__':unittest.main()
