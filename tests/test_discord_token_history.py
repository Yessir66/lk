import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import discord_token_analysis as a

WALLET=a.WATCHED['DECU']
MINT='YdnQNdhWq76CYpx8UVA5W6DYD7yur91D9NAZLoCpump'
PROGRAM='TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
ATA='6eEWQuE5LszMFLFCewUfa8ahoBEy4stWuv6TtmT5uy33'
SIG='5oiP9vYXXGZFdE9NAMQWPA5mig3EFSdBmTzQAaxJVbBcgdWpvkYBLXrHWs28Ker8KpKRh6PVYz1vEbX1pWUN3eGi'


class TargetHistoryTest(unittest.TestCase):
 def setUp(self):
  self.tx=json.loads((Path(__file__).parent/'fixtures/decu_v1_buy.json').read_text())
  self.at=self.tx['blockTime']

 def test_public_derived_account_matches_actual_reported_purchase(self):
  self.assertEqual(a.associated_accounts(WALLET,MINT,PROGRAM),[ATA])
  entry=next(b for b in self.tx['meta']['postTokenBalances'] if b.get('owner')==WALLET and b['mint']==MINT)
  self.assertEqual(self.tx['transaction']['message']['accountKeys'][entry['accountIndex']]['pubkey'],ATA)

 def test_closed_associated_account_history_finds_buy_without_recent_wallet_scan(self):
  rpc=MagicMock()
  def call(method,params):
   if method=='getTokenAccountsByOwner':return {'value':[]}
   self.assertEqual(method,'getSignaturesForAddress')
   self.assertEqual(params[0],ATA)
   return [{'signature':SIG,'blockTime':self.at,'err':None}]
  rpc.call.side_effect=call;rpc.transaction.return_value=self.tx
  result=a.wallet_snapshot(rpc,WALLET,MINT,self.at-60,self.at+10,token_history=True,token_program=PROGRAM)
  self.assertEqual(result['buys'],1)
  self.assertEqual(result['history_scope'],'token_accounts')
  self.assertEqual(a.verified_buy_wallets({'mint':MINT,'signal_at':self.at+10,'wallets':{'DECU':result}}),['DECU'])
  self.assertIsNone(result['sold_percent_initial_buy'])
  self.assertEqual(result['sold_percent_first_observed_buy'],'0')
  rpc.transaction.assert_called_once_with(SIG)

 def test_post_signal_and_failed_signatures_do_not_consume_transaction_budget(self):
  rpc=MagicMock()
  future=[{'signature':'later'+str(i),'blockTime':2100,'err':None} for i in range(400)]
  failed=[{'signature':'failed'+str(i),'blockTime':1200,'err':{'InstructionError':[0,'failed']}} for i in range(400)]
  rpc.call.return_value=future+failed+[{'signature':'buy','blockTime':1200,'err':None}]
  rows,stats=a.signature_window(rpc,['account'],1100,2000,1,max_history=2000,page_size=1000)
  self.assertEqual([r['signature'] for r in rows],['buy'])
  self.assertEqual(stats['signatures_scanned'],801)
  self.assertEqual(stats['signatures_after_signal'],400)
  self.assertEqual(stats['signatures_failed_skipped'],400)
  self.assertTrue(stats['history_boundary_reached'])

 def test_duplicate_signatures_across_token_accounts_are_read_once(self):
  rpc=MagicMock();rpc.call.return_value=[{'signature':'buy','blockTime':1200,'err':None}]
  rows,stats=a.signature_window(rpc,['first','second'],1100,2000,300)
  self.assertEqual(len(rows),1)
  self.assertTrue(stats['history_boundary_reached'])

 def test_separate_metadata_limit_remains_bounded_and_exposes_missed_window(self):
  rpc=MagicMock()
  def page(method,params):
   offset=0 if not params[1].get('before') else 1000
   return [{'signature':str(offset+i),'blockTime':2100,'err':None} for i in range(1000)]
  rpc.call.side_effect=page
  rows,stats=a.signature_window(rpc,['account'],1100,2000,300,max_history=2000,page_size=1000)
  self.assertEqual(rows,[])
  self.assertEqual(rpc.call.call_count,2)
  self.assertFalse(stats['history_boundary_reached'])
  result={'signal_at':2000,'mint':MINT,'wallets':{'DECU':dict(stats,available=True,history_limited=True)}}
  self.assertIn('FENÊTRE DU SIGNAL NON ATTEINTE',a.wallet_diagnostic(result,'DECU'))

 def test_eligible_transaction_limit_does_not_claim_complete_history(self):
  rpc=MagicMock();rpc.call.return_value=[{'signature':str(i),'blockTime':1200,'err':None} for i in range(500)]
  rows,stats=a.signature_window(rpc,['account'],1100,2000,10,max_history=1000,page_size=1000)
  self.assertEqual(len(rows),10)
  self.assertFalse(stats['history_boundary_reached'])


if __name__=='__main__':unittest.main()
