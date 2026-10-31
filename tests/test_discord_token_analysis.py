import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import discord_token_analysis as a
import discord_solana_watch as w
from discord_token_analysis import RemoteError

MINT = '3w2TvVmTrtq4kSkoFVrTFQgz6MX7ThzDVrx9r5c3pump'
OWNER = a.WATCHED['DECU']


def tx(delta, quote, at=1200, slot=10, signer=True, swap=True, rent=0):
    old, new = (0, delta) if delta>0 else (-delta, 0)
    b=lambda raw: [dict(accountIndex=1, owner=OWNER, mint=MINT, uiTokenAmount={'amount':str(raw),'decimals':6})]
    return dict(blockTime=at, slot=slot, transaction={'message':{'accountKeys':[dict(pubkey=OWNER,signer=signer),dict(pubkey='token-account',signer=False)]}},
                meta=dict(err=None,fee=5000,preBalances=[10**12,2039280-rent],postBalances=[10**12+int(quote*10**9)-5000-rent,2039280],
                          preTokenBalances=b(old),postTokenBalances=b(new),logMessages=['Program log: Instruction: Swap'] if swap else []))


class AnalysisTest(unittest.TestCase):
    def test_wallet_addresses_and_extraction_skip_watched_wallets_quotes_and_duplicates(self):
        self.assertEqual(list(a.WATCHED),['DECU','LE L'])
        text=f'https://pump.fun/coin/{MINT} {MINT} {OWNER} {a.SOL}'
        self.assertEqual(a.candidates(text),[MINT])
        self.assertFalse(a.valid_address('a'*32))

    def test_trade_parser_excludes_rent_and_network_fee_from_native_swap_cost(self):
        result=a.parse_trade(tx(100000000,-2,rent=2039280),OWNER,MINT,'buy')
        self.assertEqual(result['kind'],'buy')
        self.assertEqual(result['quantity'],'100')
        self.assertEqual(result['quotes'],{'SOL':'2'})
        sale=a.parse_trade(tx(-50000000,1),OWNER,MINT,'sell')
        self.assertEqual(sale['kind'],'sell')
        self.assertEqual(sale['quotes'],{'SOL':'1'})

    def test_incoming_or_outgoing_transfers_are_not_buys_or_sells(self):
        for delta,quote in [(100,-1),(-100,1)]:
            result=a.parse_trade(tx(delta,quote,swap=False),OWNER,MINT,'transfer')
            self.assertTrue(result['kind'].startswith('transfer_'))
            self.assertFalse(result['quotes'])
        result=a.parse_trade(tx(100,-1,signer=False),OWNER,MINT,'gift')
        self.assertEqual(result['kind'],'transfer_in_or_unknown')

    def snapshot(self, transactions, missing=None, max_signatures=300):
        rpc=MagicMock()
        rpc.call.return_value=[dict(signature=str(i),blockTime=t['blockTime'],err=None) for i,t in reversed(list(enumerate(transactions)))]
        rpc.transaction.side_effect=lambda s: None if missing==s else transactions[int(s)]
        return a.wallet_snapshot(rpc,OWNER,MINT,1100,2000,max_signatures)

    def test_first_buy_percentage_is_distinct_from_percentage_of_all_buys(self):
        result=self.snapshot([tx(100000000,-2,slot=10),tx(100000000,-3,slot=11),tx(-50000000,1,slot=12)])
        self.assertEqual(result['sold_percent_initial_buy'],'50')
        self.assertEqual(result['sold_percent_all_buys'],'25')
        self.assertEqual(result['quote_totals']['buy'],{'SOL':'5'})
        self.assertEqual(result['quote_totals']['sell'],{'SOL':'1'})
        self.assertFalse(result['history_limited'])

    def test_failed_future_and_unavailable_transactions_do_not_become_zero_or_valid_trades(self):
        failed=tx(100,-1);failed['meta']['err']={'InstructionError':[0,'failed']}
        result=self.snapshot([failed,tx(100,-1,at=2100),tx(100,-1)],missing='2')
        self.assertEqual(result['buys'],0)
        self.assertTrue(result['history_limited'])
        self.assertIsNone(result['sold_percent_initial_buy'])

    def test_partial_history_does_not_claim_an_initial_buy_percentage(self):
        result=self.snapshot([tx(100,-2)],max_signatures=1)
        self.assertTrue(result['history_limited'])
        self.assertIsNone(result['sold_percent_initial_buy'])

    def test_same_slot_buys_without_order_do_not_invent_the_first_buy(self):
        result=self.snapshot([tx(100,-1),tx(200,-2),tx(-50,1,slot=12)])
        self.assertTrue(result['first_buy_order_ambiguous'])
        self.assertIsNone(result['sold_percent_initial_buy'])

    def test_scan_pages_past_transactions_after_the_signal_without_counting_them(self):
        rpc=MagicMock()
        recent=[dict(signature='recent'+str(i),blockTime=2500,err=None) for i in range(100)]
        rpc.call.side_effect=[recent,[dict(signature='buy',blockTime=1200,err=None)]]
        rpc.transaction.return_value=tx(100,-2)
        result=a.wallet_snapshot(rpc,OWNER,MINT,1100,2000,200)
        self.assertEqual(result['buys'],1)
        self.assertEqual(result['signatures_scanned'],101)
        rpc.transaction.assert_called_once_with('buy')

    def test_callout_count_deduplicates_open_closed_top_and_respects_signal_time(self):
        pump=MagicMock()
        first=dict(calloutId='a',createdAt=1200,coinMint=MINT)
        def get(path):
            if path.startswith('/callout/coin'):return {'callouts':[first,dict(first,calloutId='future',createdAt=2100)]}
            mode='LATEST' if 'LATEST' in path else 'CLOSED_PNL'
            return {'positions':[{'callout':{'calloutId': 'a' if mode=='LATEST' else 'b','calloutTimestamp':datetime.fromtimestamp(1200,timezone.utc).isoformat()},'walletAddress':OWNER}], 'hasMore':False,'totalCount':900}
        pump.get.side_effect=get
        result=a.count_callouts(pump,MINT,1100,2000)
        self.assertEqual(result['count'],2)
        self.assertEqual(result['kind'],'public_lower_bound')
        self.assertTrue(result['coverage']['CLOSED_PNL']['exhausted'])

    def test_unavailable_callout_api_does_not_report_zero(self):
        pump=MagicMock();pump.get.side_effect=RemoteError('HTTP 403')
        result=a.count_callouts(pump,MINT,1100,2000)
        self.assertIsNone(result['count'])
        self.assertEqual(len(result['errors']),3)

    def test_storage_deduplicates_mints_across_restarts_and_recovers_pending_work(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'state'
            store=a.Store(p)
            self.assertTrue(store.enqueue(MINT,'channel','1',1200))
            self.assertFalse(store.enqueue(MINT,'channel','2',1300))
            self.assertEqual(store.next()['signal_at'],1200)
            store.db.close()
            store=a.Store(p);store.recover()
            self.assertEqual(store.next()['mint'],MINT)
            store.db.close()

    def test_discord_embeds_and_snowflake_signal_time_use_original_message_not_edit(self):
        at=1793296320.123
        identity=str((int(at*1000)-1420070400000)<<22)
        message={'id':identity,'timestamp':datetime.fromtimestamp(at,timezone.utc).isoformat(),'content':'',
                 'edited_timestamp':datetime.now(timezone.utc).isoformat(),'embeds':[{'fields':[{'name':'CA','value':MINT}]}]}
        self.assertAlmostEqual(w.signal_time(message),at,places=3)
        self.assertEqual(a.candidates(w.message_text(message)),[MINT])
        message['timestamp']=datetime.fromtimestamp(at+10,timezone.utc).isoformat()
        with self.assertRaises(ValueError):w.signal_time(message)

    def test_channel_link_and_browser_cdp_do_not_accept_arbitrary_remote_hosts(self):
        self.assertEqual(w.channel_id('https://discord.com/channels/111111111111111111/222222222222222222'),'222222222222222222')
        with self.assertRaises(ValueError):w.channel_id('solana')
        with self.assertRaises(ValueError):w.BrowserReader('222222222222222222','https://example.com')

    def test_reports_escape_external_text_and_do_not_display_unknown_values_as_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            store=a.Store(Path(folder)/'state')
            store.enqueue(MINT,'channel','1',1200)
            result=dict(mint=MINT,source='channel',signal_at=1200,created_at=1100,name='<script>alert(1)</script>',symbol='HI',
                        wallets={'DECU':{'available':False,'reason':'RPC unavailable'}},callouts={'count':None,'note':'Minimum public','errors':['HTTP 403']})
            store.finish(MINT,result=result)
            a.write_reports(store,Path(folder)/'reports')
            text=(Path(folder)/'reports/index.html').read_text()
            self.assertNotIn('<script>',text)
            self.assertIn('&lt;script&gt;',text)
            self.assertIn('RPC unavailable',text)
            self.assertIn('—',text)
            store.db.close()

    def test_rpc_rejects_transaction_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            store=a.Store(Path(folder)/'state')
            rpc=a.ReadRPC('http://localhost',store)
            with self.assertRaises(ValueError):rpc.call('sendTransaction',[])
            store.db.close()

    def test_scout_journal_tail_skips_existing_history_and_only_reads_discord_alerts(self):
        with tempfile.TemporaryDirectory() as folder:
            store=a.Store(Path(folder)/'state');journal=Path(folder)/'journal.jsonl'
            event={'type':'alert','source':'discord','mint':MINT,'t_alert':1793296320.123}
            journal.write_text(json.dumps(event)+'\n')
            w.initialize_scout(store,journal)
            self.assertEqual(w.poll_scout(store,journal),[])
            events=[dict(event,type='later_message'),dict(event,source='telegram'),dict(event,source='rejeu'),event]
            with journal.open('a') as f:
                for row in events:f.write(json.dumps(row)+'\n')
            self.assertEqual(w.poll_scout(store,journal),[MINT])
            signal=store.next()
            self.assertEqual(signal['signal_at'],1793296320.123)
            self.assertEqual(w.poll_scout(store,journal),[])
            store.db.close()

    def test_scout_tail_preserves_incomplete_lines_and_restarts_without_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state';store=a.Store(path);journal=Path(folder)/'journal.jsonl'
            w.initialize_scout(store,journal)
            event=json.dumps({'type':'alert','source':'discord','mint':MINT,'t_alert':1793296320})
            journal.write_text(event[:20])
            self.assertFalse(w.poll_scout(store,journal))
            self.assertEqual(store.cursor(w.scout_source(journal)),'0')
            journal.write_text(event+'\n')
            self.assertEqual(w.poll_scout(store,journal),[MINT])
            store.db.close();store=a.Store(path)
            self.assertFalse(w.poll_scout(store,journal))
            with journal.open('a') as f:f.write(event+'\n')
            self.assertFalse(w.poll_scout(store,journal))
            store.db.close()

    def test_scout_mode_does_not_require_a_discord_token_or_channel(self):
        args=w.parse_args([])
        self.assertEqual(args.source,'scout')
        self.assertTrue(str(args.scout_journal).endswith('data/scout/journal.jsonl'))

    def test_non_sol_quote_assets_are_reported_separately(self):
        trade=tx(100000000,0)
        quote='98sMhvDwXj1RQi5c5Mndm3vPe9cBqPrbLaufMXFNMh5g'
        trade['meta']['preTokenBalances'].append(dict(accountIndex=2,owner=OWNER,mint=quote,uiTokenAmount={'amount':'2000000000','decimals':9}))
        trade['meta']['postTokenBalances'].append(dict(accountIndex=2,owner=OWNER,mint=quote,uiTokenAmount={'amount':'1000000000','decimals':9}))
        trade['meta']['preBalances'].append(2039280);trade['meta']['postBalances'].append(2039280)
        result=a.parse_trade(trade,OWNER,MINT,'swap')
        self.assertEqual(result['kind'],'buy')
        self.assertEqual(result['quotes'],{quote:'1'})

    def test_pump_outage_does_not_prevent_independent_wallet_analysis(self):
        pump=MagicMock();pump.get.side_effect=RemoteError('HTTP 403')
        rpc=MagicMock();rpc.call.return_value=[]
        result=a.Analyzer(rpc,pump).analyze(dict(mint=MINT,signal_at=1793296320,source='scout'))
        self.assertEqual(set(result['wallets']),{'DECU','LE L'})
        self.assertTrue(result['wallets']['DECU']['available'])
        self.assertEqual(result['wallets']['DECU']['buys'],0)
        self.assertIsNone(result['created_at'])
        self.assertIsNone(result['callouts']['count'])
        self.assertEqual(result['metadata_error'],'HTTP 403')


if __name__=='__main__':unittest.main()
