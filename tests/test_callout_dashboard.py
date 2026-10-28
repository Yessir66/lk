import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import callout_dashboard as d
import callout_hub as h
import callout_repost as r


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.control = d.Dashboard(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_validates_only_supported_fields_finite_values_and_configured_callers(self):
        for change in [{'mode':'other'}, {'usd':float('nan')}, {'usd':True}, {'profile':'--live'},
                       {'callers':[]}, {'callers':['unknown']}, {'slippage_bps':1.5}, {'secret':'no'},
                       {'jupiter_rps':0}, {'jupiter_rps':float('inf')}, {'jupiter_rps':151}]:
            with self.assertRaises(ValueError): d.validate_config(dict(d.DEFAULTS, **change))
        self.assertEqual(d.validate_config({'usd':3})['usd'],3)

    def test_simulation_launch_is_argument_list_and_cannot_enable_live(self):
        process = MagicMock()
        process.poll.return_value = None
        with patch.object(d.subprocess,'Popen',return_value=process) as popen, patch.object(d.threading,'Thread'):
            self.control.start(dict(d.DEFAULTS, slippage_bps=100.0))
            command = popen.call_args.args[0]
            self.assertIn('--dry-run', command)
            self.assertNotIn('--live', command)
            self.assertIsInstance(command,list)
            self.assertNotIn('shell',popen.call_args.kwargs)
            self.assertEqual(command[command.index('--slippage-bps')+1],'100')
            self.assertEqual(command[command.index('--jupiter-rps')+1],'0.5')
            with self.assertRaises(ValueError): self.control.start(d.DEFAULTS)
            self.assertEqual(popen.call_count,1)
        self.assertFalse(any('secret' in key.lower() for key in json.loads((self.control.directory/'settings.json').read_text())))

    def test_custom_jupiter_ceiling_is_forwarded_and_retained_after_restart(self):
        process = MagicMock()
        process.poll.return_value = None
        with patch.object(d.subprocess, 'Popen', return_value=process) as popen, patch.object(d.threading, 'Thread'):
            self.control.start(dict(d.DEFAULTS, jupiter_rps=8))
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('--jupiter-rps')+1], '8')
        self.assertEqual(d.Dashboard(self.root).config['jupiter_rps'], 8)
        old = dict(d.DEFAULTS)
        old.pop('jupiter_rps')
        (self.control.directory/'settings.json').write_text(json.dumps(old))
        self.assertEqual(d.Dashboard(self.root).config['jupiter_rps'], .5)

    def test_old_settings_migrate_callers_and_delays_without_losing_quota(self):
        old=dict(d.DEFAULTS, callers=['chairman','Dior'], delay=15, jupiter_rps=8, usd=3)
        old.pop('workflow_version')
        (self.control.directory/'settings.json').write_text(json.dumps(old))
        config=d.Dashboard(self.root).config
        self.assertEqual(config['callers'],list(r.CALLERS.values()))
        self.assertEqual((config['buy_delay'],config['update_delay'],config['stop_loss'],config['delay']),(12,10,50,0))
        self.assertEqual((config['jupiter_rps'],config['usd']),(8,3))

    def test_live_requires_local_wallet_and_session_files(self):
        with patch.object(d.subprocess,'Popen') as popen:
            with self.assertRaises(ValueError): self.control.start(dict(d.DEFAULTS,mode='live'))
            popen.assert_not_called()
        for filename in ('pump_wallet.txt','pump_token.txt','callout_alerts_telegram.session'):
            (self.root/'data'/filename).write_text('test fixture')
        with patch.object(d,'retained_path',return_value=str(self.root/'data/callout_alerts_telegram.session')), \
             patch.object(d.subprocess,'Popen') as popen, patch.object(d.threading,'Thread'):
            self.control.start(dict(d.DEFAULTS,mode='live'))
            self.assertIn('--live',popen.call_args.args[0])
            self.assertNotIn('--dry-run',popen.call_args.args[0])

    def test_stop_is_cooperative_and_only_targets_owned_run(self):
        process = MagicMock()
        process.poll.return_value = None
        self.control.process=process
        self.control.stop_path=self.control.directory/'stop-own'
        self.control.stop()
        self.assertEqual(self.control.phase,'stopping')
        self.assertTrue(self.control.stop_path.is_file())
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_snapshot_reads_positions_without_transaction_or_key_payloads(self):
        journal=r.Journal(str(self.root/'data/repost_simulation.sqlite3'),0,'owner')
        job={'mint':r.SOL,'state':'posted','caller':'Dior','created':1,'buy_confirmed':True,'cost_usd':'2',
             'mark_usd':'2.4','mark_cost_usd':'2','mark_checked_at':time.time(),'exit_state':'watching','raw':'NEVER_EXPOSE_RAW','signature':'NEVER_EXPOSE_SIG'}
        journal.save(job)
        journal.db.close()
        with patch.dict(d.os.environ,{'JUP_API_KEY':'NEVER_EXPOSE_API_KEY'}):
            state=self.control.snapshot()
        self.assertEqual(state['summary']['positions'],1)
        self.assertAlmostEqual(state['summary']['pnl'],.4)
        self.assertTrue(state['credentials']['jupiter'])
        payload=json.dumps(state)
        self.assertNotIn('NEVER_EXPOSE',payload)
        with sqlite3.connect(self.root/'data/repost_simulation.sqlite3') as db:
            self.assertEqual(json.loads(db.execute('SELECT data FROM jobs').fetchone()[0])['raw'],'NEVER_EXPOSE_RAW')

    def test_details_export_only_numeric_allowlist_and_validated_explorer_links(self):
        journal = r.Journal(str(self.root/'data/repost_simulation.sqlite3'), 0, 'owner')
        journal.save(dict(mint=r.SOL, caller='Dior', state='posted', created=1, entry_mc=18000,
                          signature='1' * 88, raw='PRIVATE_BUY_BYTES',
                          exit_tx={'signature': '2' * 88, 'raw': 'PRIVATE_SELL_BYTES'},
                          buy_details={'mc_estimate': '20000', 'slot': 123, 'raw': 'PRIVATE_DETAIL_BYTES'},
                          sell_details={'mc_estimate': {'raw': 'PRIVATE_NESTED_BYTES'}, 'usd_estimate': '3'}))
        journal.db.close()
        job = self.control.snapshot()['jobs'][0]
        self.assertEqual(job['entry_mc'], 18000)
        self.assertEqual(job['buy_details']['mc_estimate'], '20000')
        self.assertNotIn('mc_estimate', job['sell_details'])
        self.assertEqual(job['buy_tx_url'], 'https://solscan.io/tx/' + '1' * 88)
        self.assertEqual(job['sell_tx_url'], 'https://solscan.io/tx/' + '2' * 88)
        self.assertNotIn('PRIVATE_', json.dumps(job))

    def test_old_open_position_is_not_hidden_by_history_limit(self):
        journal=r.Journal(str(self.root/'data/repost_simulation.sqlite3'),0,'owner')
        journal.save({'mint':'open-old','state':'posted','caller':'Dior','cost_usd':'2','exit_state':'watching'})
        for index in range(301):
            journal.save({'mint':f'history-{index}','state':'failed','caller':'Dior'})
        journal.db.close()
        state=self.control.snapshot()
        self.assertEqual(state['summary']['positions'],1)
        self.assertEqual(len(state['jobs']),301)
        self.assertTrue(any(job['mint']=='open-old' for job in state['jobs']))

    def test_ui_telemetry_failure_never_interrupts_trade_routing(self):
        import queue
        sink=h.EventSink(self.root/'events.jsonl')
        reader=h.SharedReader(MagicMock(),'owner',0,0,queue.Queue(),queue.Queue(),event_sink=sink)
        author=next(iter(h.CALLERS));reader.targets=reader.follows={author:'caller'}
        call={'calloutId':'one','coinMint':r.SOL,'createdAt':1,'author':author,'name':'Token','mcap_now':2}
        with patch.object(Path,'open',side_effect=OSError('disk unavailable')):
            reader.route([call])
        self.assertTrue(sink.warned)
        self.assertEqual(reader.repost_queue.qsize(),1)
        self.assertEqual(reader.alerts_queue.qsize(),1)

    def test_runner_lock_blocks_duplicate_processes_and_releases_on_exit(self):
        path=self.root/'data/runner.lock'
        with r.RunnerLock(path):
            with self.assertRaises(h.RemoteError):
                with r.RunnerLock(path):pass
        with r.RunnerLock(path):pass

    def test_stale_or_blocked_quotes_do_not_appear_as_current_pnl(self):
        path=self.root/'data/repost_simulation.sqlite3'
        journal=r.Journal(str(path),0,'owner')
        job={'mint':'position','state':'posted','caller':'Dior','cost_usd':'2','mark_usd':'3','exit_state':'watching','mark_checked_at':1}
        journal.save(job)
        self.assertIsNone(self.control.snapshot()['summary']['pnl'])
        job.update(mark_checked_at=time.time(),tp_error='Jupiter : HTTP 429')
        journal.save(job)
        self.assertIsNone(self.control.snapshot()['summary']['pnl'])
        self.assertIn('tp_error',self.control.snapshot()['jobs'][0])
        journal.db.close()

    def test_diagnostic_messages_redact_local_access_values(self):
        journal=r.Journal(str(self.root/'data/repost_simulation.sqlite3'),0,'owner')
        journal.save({'mint':'position','state':'posted','caller':'Dior','tp_error':'RPC refused SECRET-LOCAL-KEY'})
        journal.db.close()
        with patch.dict(d.os.environ,{'JUP_API_KEY':'SECRET-LOCAL-KEY'}):
            self.assertNotIn('SECRET-LOCAL-KEY',json.dumps(self.control.snapshot()))

    def test_event_tailing_handles_partial_lines_and_deduplicates(self):
        self.control.event_path=self.control.directory/'events-test.jsonl'
        event={'type':'callout','at':1,'data':{'calloutId':'one','coinMint':r.SOL,'thesis':'hello'}}
        raw=json.dumps(event)+'\n'
        self.control.event_path.write_text(raw+raw+raw[:20])
        self.control.ingest()
        self.assertEqual(len(self.control.feed),1)
        self.assertEqual(self.control.offset,len((raw+raw).encode()))
        with self.control.event_path.open('a') as f:f.write(raw[20:])
        self.control.ingest()
        self.assertEqual(len(self.control.feed),1)

    def test_caller_history_survives_global_feed_overflow_and_repeated_events(self):
        self.control.event_path = self.control.directory/'events-retention.jsonl'
        caller = next(iter(d.CALLERS))
        observed = {'calloutId': 'caller-one', 'author': caller, 'author_name': 'renamed-profile', 'coinMint': r.SOL}
        events = [{'type': 'callout', 'at': 1, 'data': observed}]
        events += [{'type': 'callout', 'at': i+2, 'data': {'calloutId': str(i), 'author': 'other', 'coinMint': r.SOL}} for i in range(300)]
        events.append(events[0])
        self.control.event_path.write_text(''.join(json.dumps(e)+'\n' for e in events))
        state = self.control.snapshot()
        self.assertEqual(len(state['feed']), 240)
        self.assertFalse(any(c['calloutId'] == 'caller-one' for c in state['feed']))
        self.assertEqual([c['calloutId'] for c in state['caller_feed']], ['caller-one'])
        self.assertEqual(state['feed_received'], 301)
        self.assertEqual(state['caller_wallets'][caller], d.CALLERS[caller])

    def test_ui_event_observer_does_not_change_trade_filters_or_request_metadata(self):
        import queue
        pump=MagicMock()
        sink=MagicMock()
        reader=h.SharedReader(pump,'owner',0,0,queue.Queue(),queue.Queue(),event_sink=sink)
        reader.targets={}
        reader.route([{'calloutId':'one','coinMint':r.SOL,'createdAt':1,'author':'unwatched','author_name':'Public caller'}])
        sink.assert_called_once()
        self.assertEqual(sink.call_args.args[0],'callout')
        self.assertTrue(reader.repost_queue.empty())
        self.assertTrue(reader.alerts_queue.empty())
        pump.enrich.assert_not_called()

    def test_caller_selection_reaches_existing_engine_mapping(self):
        original=dict(r.CALLERS)
        with patch.dict(r.CALLERS, original):
            r.parse_args(['--callers','lynk0x_,narc'])
            self.assertEqual(set(r.CALLERS.values()),{'lynk0x_','narc'})
        self.assertEqual(r.CALLERS,original)


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.control=d.Dashboard(self.temp.name)
        self.server=d.make_server(self.control,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse();status=response.status;raw=response.read();connection.close()
        return status,raw

    def test_state_and_assets_are_served_without_private_files(self):
        status,raw=self.request('GET','/')
        self.assertEqual(status,200)
        self.assertIn(self.control.token.encode(),raw)
        self.assertEqual(self.request('GET','/app.js')[0],200)
        self.assertEqual(self.request('GET','/api/state')[0],200)
        self.assertEqual(self.request('GET','/../data/pump_wallet.txt')[0],404)

    def test_controls_require_local_origin_host_and_request_token(self):
        self.assertEqual(self.request('POST','/api/stop','{}',{'Content-Type':'application/json'})[0],403)
        headers={'Content-Type':'application/json','X-Desk-Token':self.control.token}
        self.assertEqual(self.request('POST','/api/stop','{}',dict(headers,Origin='https://external.example'))[0],403)
        self.assertEqual(self.request('POST','/api/stop','{}',dict(headers,Host='evil.example'))[0],403)
        self.assertEqual(self.request('POST','/api/stop','{}',headers)[0],200)
        self.assertEqual(self.request('POST','/api/start','{"usd":-1}',headers)[0],400)


if __name__=='__main__':unittest.main()
