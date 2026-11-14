import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wallet_alerts_ws as w
import configure_wallet_alerts as configure
from test_discord_v1_regression import FIXTURE, SIGNATURE, MINT

WALLET=w.WATCHED['DECU']


def notice(signature=SIGNATURE,err=None):
    return {'method':'logsNotification','params':{'subscription':1,'result':{
      'context':{'slot':1},'value':{'signature':signature,'err':err,'logs':['Program log: Instruction: BuyExactQuoteInV3']}}}}


class WalletAlertTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.path=self.root/'state.sqlite3';self.journal=w.Journal(self.path)
        self.addCleanup(lambda:self.journal.db.close())
        self.tx=json.loads(FIXTURE.read_text());self.at=self.tx['blockTime']
        self.rpc=MagicMock();self.sender=MagicMock();self.sender.destination='test-destination';self.sender.send.return_value=42
        self.service=w.Service(self.journal,dict(w.WATCHED),self.rpc,'wss://unused.invalid',sender=self.sender)
        self.service.call=AsyncMock(return_value=self.tx)

    def test_example_settings_and_secrets_stay_out_of_public_output(self):
        example=Path(__file__).parents[1]/'wallet_alerts_config.example.json'
        with patch.dict(w.os.environ,{},clear=True):
            config,wallets,http,ws=w.settings(example)
        self.assertEqual(wallets,w.WATCHED);self.assertTrue(ws.startswith('wss://'))
        self.assertIn('mainnet.helius-rpc.com',http)
        text=w.render(dict(w.trades_in(self.tx,WALLET,SIGNATURE)[0],notification_to_decode_ms=100,source='websocket'),'DECU')
        self.assertIn(SIGNATURE,text);self.assertNotIn('api-key',text)

    def test_http_and_websocket_status_diagnostics_never_expose_exception_text(self):
        from types import SimpleNamespace
        for status in (401,403,429):
            error=RuntimeError('wss://provider.example/?api-key=secret')
            error.response=SimpleNamespace(status_code=status)
            self.assertEqual(w.failure_label(error),f'RuntimeError ; HTTP {status}')
        self.assertEqual(w.failure_label(ValueError('private-key')),'ValueError')

    async def test_diagnostic_checks_https_and_websocket_without_loading_telegram(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from types import SimpleNamespace
        error=RuntimeError('wss://provider.example/?api-key=secret')
        error.response=SimpleNamespace(status_code=401)
        output=StringIO()
        with patch.object(w,'ReadRPC') as rpc,patch('websockets.asyncio.client.connect',side_effect=error),redirect_stdout(output):
            rpc.return_value.call.return_value=[]
            await w.diagnose('https://provider.example/?api-key=secret','wss://provider.example/?api-key=secret',WALLET)
            rpc.return_value.call.assert_called_once_with('getSignaturesForAddress',[WALLET,{'limit':1,'commitment':'confirmed'}])
        self.assertIn('HTTPS : OK',output.getvalue());self.assertIn('HTTP 401',output.getvalue())
        self.assertNotIn('secret',output.getvalue());self.sender.send.assert_not_called()

    def test_rejects_invalid_wallets_and_plaintext_rpc(self):
        path=self.root/'config.json'
        for data in ({'helius_api_key':'secret','wallets':{'bad':'not-wallet'}},
                     {'rpc_http_url':'http://provider.example/','rpc_ws_url':'ws://provider.example/'},
                     {'helius_api_key':'secret','wallets':{'a':WALLET,'b':WALLET}}):
            path.write_text(json.dumps(data))
            with patch.dict(w.os.environ,{},clear=True),self.assertRaises(ValueError):w.settings(path)

    def test_telegram_configuration_preserves_helius_and_never_prints_secrets(self):
        from contextlib import redirect_stdout
        from io import StringIO
        path=self.root/'wallet_alerts_config.json'
        data={'helius_api_key':'helius-secret','wallets':dict(w.WATCHED)}
        path.write_text(json.dumps(data));token='123456:abcdefghijklmnopqrs';output=StringIO()
        with patch.object(configure,'__file__',str(self.root/'scripts'/'configure_wallet_alerts.py')),\
             patch.object(configure.getpass,'getpass',return_value=token),\
             patch('builtins.input',return_value='-100123456'),\
             patch.object(sys,'argv',['configure_wallet_alerts.py','--telegram']),redirect_stdout(output):
            configure.main()
        saved=json.loads(path.read_text())
        self.assertEqual(saved,dict(data,telegram_bot_token=token,telegram_chat_id='-100123456'))
        self.assertNotIn(token,output.getvalue());self.assertNotIn('helius-secret',output.getvalue())
        self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_invalid_telegram_token_does_not_modify_existing_configuration(self):
        path=self.root/'wallet_alerts_config.json';original=json.dumps({'helius_api_key':'helius-secret'})
        path.write_text(original)
        with patch.object(configure,'__file__',str(self.root/'scripts'/'configure_wallet_alerts.py')),\
             patch.object(configure.getpass,'getpass',return_value='not-a-bot-token'),\
             patch('builtins.input',return_value='123'),\
             patch.object(sys,'argv',['configure_wallet_alerts.py','--telegram']),self.assertRaises(SystemExit):
            configure.main()
        self.assertEqual(path.read_text(),original)

    def test_processed_never_enters_decoder_without_confirmed_notification(self):
        w.notification(self.journal,notice(),{1:(WALLET,'processed')},self.at+.1)
        self.assertIsNone(self.journal.due(self.at+1))
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at+.5)
        row=self.journal.due(self.at+1)
        self.assertEqual(row[2],self.at+.1);self.assertEqual(row[3],self.at+.5)
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at+1)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM events').fetchone()[0],1)

    async def test_early_notification_is_provisional_and_monotonic_gap_survives_wall_clock_adjustment(self):
        from contextlib import redirect_stdout
        from io import StringIO
        output=StringIO()
        with redirect_stdout(output):
            w.notification(self.journal,notice(),{1:(WALLET,'processed')},self.at+10,monotonic=100,names={WALLET:'DECU'})
        self.assertIn('TRANSACTION PROVISOIRE mentionnant DECU',output.getvalue())
        self.assertNotIn('ACHAT',output.getvalue());self.assertIsNone(self.journal.due(self.at+11))
        self.assertFalse(await self.service.deliver_one(self.at+11));self.sender.send.assert_not_called()
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at+9,monotonic=100.75)
        with patch.object(w.time,'time',return_value=self.at+11),patch.object(w.time,'monotonic',return_value=101):
            await self.service.decode_one(self.at+11)
        trade=json.loads(self.journal.db.execute('SELECT trades FROM events').fetchone()[0])[0]
        self.assertEqual(trade['processed_to_confirmed_ms'],750)
        self.assertEqual(trade['confirmed_to_decode_ms'],250)
        text=w.render(trade,'DECU')
        self.assertIn('0,75 s (même connexion)',text);self.assertIn('0,25 s (horloge monotone)',text)

    def test_reconnect_or_restart_never_pairs_monotonic_clocks_from_different_sessions(self):
        w.notification(self.journal,notice(),{1:(WALLET,'processed')},self.at,monotonic=100)
        self.journal.begin_connection()
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at+2,monotonic=200)
        self.assertIsNone(self.journal.timing(WALLET,SIGNATURE,201)['processed_to_confirmed_ms'])
        other=w.Journal(self.path)
        try:self.assertEqual(other.timings,{})
        finally:other.db.close()

    def test_confirmed_first_is_recorded_as_negative_gap_without_provisional_alert(self):
        from contextlib import redirect_stdout
        from io import StringIO
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at,monotonic=1)
        output=StringIO()
        with redirect_stdout(output):w.notification(self.journal,notice(),{1:(WALLET,'processed')},self.at+.1,monotonic=1.1)
        self.assertEqual(output.getvalue(),'')
        self.assertAlmostEqual(self.journal.timing(WALLET,SIGNATURE,2)['processed_to_confirmed_ms'],-100)

    def test_duplicate_processed_does_not_replace_first_arrival_or_print_twice(self):
        from contextlib import redirect_stdout
        from io import StringIO
        output=StringIO()
        with redirect_stdout(output):
            w.notification(self.journal,notice(),{1:(WALLET,'processed')},self.at,monotonic=1)
            w.notification(self.journal,notice(),{1:(WALLET,'processed')},self.at+.3,monotonic=1.3)
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at+.5,monotonic=1.5)
        self.assertEqual(output.getvalue().count('TRANSACTION PROVISOIRE'),1)
        self.assertEqual(self.journal.timing(WALLET,SIGNATURE,2)['processed_to_confirmed_ms'],500)

    def test_report_is_read_only_and_excludes_old_and_unverified_rows_from_paired_metrics(self):
        from contextlib import redirect_stdout
        from io import StringIO
        self.journal.observe(WALLET,SIGNATURE,self.at,1,'websocket',True)
        self.journal.finish(WALLET,SIGNATURE,[{'kind':'buy','swap_proof_version':1}])
        self.journal.db.execute('UPDATE events SET processed_to_confirmed_ms=750,confirmed_to_decode_ms=125')
        self.journal.db.execute("INSERT INTO alerts (wallet,signature,mint,state) VALUES (?,?,?,'sending')",(WALLET,SIGNATURE,MINT))
        self.journal.db.commit()
        output=StringIO()
        with redirect_stdout(output):w.timing_report(self.path)
        self.assertIn('médiane 0,75 s',output.getvalue());self.assertIn('médiane 0,12 s',output.getvalue())
        self.assertEqual(self.journal.db.execute('SELECT state FROM alerts').fetchone()[0],'sending')

    def test_migrates_existing_state_without_replaying_trades_or_inventing_processed_times(self):
        import sqlite3
        path=self.root/'old.sqlite3';db=sqlite3.connect(path)
        db.execute('CREATE TABLE events (wallet TEXT,signature TEXT,first_seen REAL,confirmed_seen REAL,slot INTEGER,source TEXT,state TEXT,attempts INTEGER DEFAULT 0,retry_at REAL DEFAULT 0,trades TEXT,error TEXT,PRIMARY KEY(wallet,signature))')
        db.execute("INSERT INTO events (wallet,signature,first_seen,state,trades) VALUES (?,?,?,'done','[]')",(WALLET,SIGNATURE,self.at))
        db.commit();db.close()
        migrated=w.Journal(path)
        try:
            self.assertIsNone(migrated.due(self.at+1))
            timing=migrated.timing(WALLET,SIGNATURE,100)
            self.assertTrue(all(v is None for v in timing.values()))
        finally:migrated.db.close()

    def test_measured_rpc_separates_local_rate_sleep_from_http_and_json(self):
        clock={'now':10.0}
        def advance(value):clock['now']+=value
        rpc=w.MeasuredRPC('https://unused.invalid',None,rps=2);rpc.next_at=10.3
        response=MagicMock(status_code=200)
        response.json.side_effect=lambda:(advance(.01),{'result':[]})[1]
        rpc.http.post=MagicMock(side_effect=lambda *a,**k:(advance(.2),response)[1])
        with patch.object(w.time,'monotonic',side_effect=lambda:clock['now']),patch.object(w.time,'sleep',side_effect=advance):
            self.assertEqual(rpc.call('getSignaturesForAddress',[WALLET,{'limit':1}]),[])
        self.assertAlmostEqual(rpc.last_timing['rate_limit_wait_ms'],300)
        self.assertAlmostEqual(rpc.last_timing['http_json_ms'],210)
        self.assertAlmostEqual(rpc.next_at,10.8)
        self.assertEqual(rpc.http.post.call_args.kwargs['json']['method'],'getSignaturesForAddress')

    async def test_call_records_rpc_lock_and_transport_metrics_without_secrets(self):
        rpc=MagicMock();rpc.call.return_value=[]
        rpc.last_timing={'rate_limit_wait_ms':10.0,'http_json_ms':20.0}
        service=w.Service(self.journal,dict(w.WATCHED),rpc,'wss://unused.invalid')
        metrics={}
        self.assertEqual(await service.call('getSignaturesForAddress',[WALLET,{'limit':1}],metrics=metrics),[])
        self.assertEqual(set(metrics),set(w.RPC_PHASES))
        self.assertTrue(all(v>=0 for v in metrics.values()))
        self.assertNotIn('secret',json.dumps(metrics))

    async def test_waiting_backfill_does_not_reserve_quota_ahead_of_new_live_request(self):
        self.service.call=w.Service.call.__get__(self.service)
        self.service.rpc_interval=.02
        self.rpc.call.return_value=[]
        self.service.rpc_next_at=w.time.monotonic()+.05
        background=asyncio.create_task(self.service.call('getSignaturesForAddress',[WALLET]))
        await asyncio.sleep(.01)
        self.assertFalse(self.service.rpc_busy)
        foreground=asyncio.create_task(self.service.call('getTransaction',[SIGNATURE]))
        await asyncio.wait_for(asyncio.gather(background,foreground),2)
        self.assertEqual([c.args[0] for c in self.rpc.call.call_args_list],['getTransaction','getSignaturesForAddress'])

    async def test_background_and_foreground_decoders_do_not_block_each_other(self):
        old='old-sig';self.journal.observe(WALLET,old,self.at,1,'rattrapage',True)
        self.journal.observe(WALLET,SIGNATURE,self.at+1,1,'websocket',True)
        entered=asyncio.Event();release=asyncio.Event()
        async def call(method,params,*,metrics=None,background=None):
            if background:entered.set();await release.wait()
            return self.tx
        self.service.call=call
        task=asyncio.create_task(self.service.decode_one(self.at+2,background=True))
        try:
            await asyncio.wait_for(entered.wait(),1)
            self.assertTrue(await asyncio.wait_for(self.service.decode_one(self.at+2,background=False),1))
            self.assertFalse(task.done())
            states=dict(self.journal.db.execute('SELECT signature,state FROM events'))
            self.assertEqual(states[SIGNATURE],'done');self.assertEqual(states[old],'pending')
        finally:release.set();await task

    async def test_cancelled_priority_waiter_is_removed_and_next_request_can_run(self):
        self.service.call=w.Service.call.__get__(self.service)
        self.service.rpc_next_at=w.time.monotonic()+10
        task=asyncio.create_task(self.service.call('getTransaction',[SIGNATURE]))
        await asyncio.sleep(.01);task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.service.rpc_waiters,[]);self.assertFalse(self.service.rpc_busy)
        self.service.rpc_next_at=0;self.rpc.call.return_value=[]
        self.assertEqual(await asyncio.wait_for(self.service.call('getTransaction',[SIGNATURE]),1),[])

    async def test_rpc_dispatch_is_serialized_and_shared_between_both_priorities(self):
        self.service.call=w.Service.call.__get__(self.service)
        self.service.rpc_interval=.02;seen=[]
        def call(method,params):seen.append(w.time.monotonic());return []
        self.rpc.call.side_effect=call
        await asyncio.wait_for(asyncio.gather(*[
            self.service.call('getTransaction',[str(i)]) for i in range(4)]),2)
        self.assertEqual(len(seen),4)
        self.assertTrue(all(b-a>=.01 for a,b in zip(seen,seen[1:])))
        self.assertFalse(self.service.rpc_busy);self.assertEqual(self.service.rpc_waiters,[])

    async def test_backfill_has_separate_one_per_second_ceiling_without_blocking_foreground(self):
        self.service.rpc_interval=.001
        await self.service.acquire_rpc(background=True)
        await self.service.release_rpc()
        self.assertGreater(self.service.background_next_at-w.time.monotonic(),.8)
        await asyncio.wait_for(self.service.acquire_rpc(background=False),.3)
        await self.service.release_rpc()

    async def test_http_429_pauses_foreground_and_background_instead_of_retrying_faster(self):
        self.service.call=w.Service.call.__get__(self.service)
        from discord_token_analysis import RemoteError
        self.rpc.call.side_effect=RemoteError('quota',status=429)
        with self.assertRaises(RemoteError):await self.service.call('getTransaction',[SIGNATURE])
        self.assertGreater(self.service.pause_until-w.time.monotonic(),59)
        tasks=[asyncio.create_task(self.service.call(method,[WALLET])) for method in ('getTransaction','getSignaturesForAddress')]
        try:
            await asyncio.sleep(.02)
            self.rpc.call.assert_called_once();self.assertFalse(self.service.rpc_busy)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        self.assertEqual(self.service.rpc_waiters,[])

    async def measured_decode(self,retry=False,reconnect=False):
        clock={'now':1000.2};calls=[]
        self.journal.observe(WALLET,SIGNATURE,self.at,1,'websocket',True,stage='confirmed',monotonic=1000)
        async def call(method,params,*,metrics=None,background=None):
            parts=dict(rpc_lock_wait_ms=100.0,cooldown_wait_ms=0.0,scheduler_wait_ms=10.0,
                       rate_limit_wait_ms=300.0,http_json_ms=200.0)
            metrics.update(parts);clock['now']+=sum(parts.values())/1000;calls.append(method)
            if reconnect:self.journal.begin_connection()
            return None if retry and len(calls)==1 else self.tx
        self.service.call=call
        original=w.trades_in
        def decode(*args):
            clock['now']+=.002
            return original(*args)
        with patch.object(w.time,'monotonic',side_effect=lambda:clock['now']),\
             patch.object(w.time,'time',return_value=self.at+1),patch.object(w,'trades_in',side_effect=decode):
            await self.service.decode_one(self.at+1)
            if retry:
                stored=json.loads(self.journal.db.execute('SELECT rpc_profile FROM events').fetchone()[0])
                self.assertEqual(stored['attempts_profiled'],1)
                clock['now']+=2.5
                await self.service.decode_one(self.at+4)
        return json.loads(self.journal.db.execute('SELECT rpc_profile FROM events').fetchone()[0])

    async def test_full_profile_attributes_wait_quota_rpc_and_actual_decoder_cost(self):
        profile=await self.measured_decode()
        self.assertTrue(profile['complete'])
        self.assertAlmostEqual(profile['queue_wait_ms'],200)
        self.assertAlmostEqual(profile['decode_ms'],2)
        self.assertAlmostEqual(profile['total_ms'],812)
        self.assertAlmostEqual(profile['other_ms'],0)
        self.assertEqual(profile['attempts_profiled'],1)
        trade=json.loads(self.journal.db.execute('SELECT trades FROM events').fetchone()[0])[0]
        self.assertIn('quota local 0,30 s',w.render(trade,'DECU'))
        self.assertIn('RPC HTTP+JSON 0,20 s',w.render(trade,'DECU'))
        self.sender.send.assert_not_called()

    async def test_null_transaction_retry_keeps_initial_queue_and_sums_all_reads(self):
        profile=await self.measured_decode(retry=True)
        self.assertTrue(profile['complete']);self.assertEqual(profile['attempts_profiled'],2)
        self.assertAlmostEqual(profile['queue_wait_ms'],200)
        self.assertAlmostEqual(profile['retry_wait_ms'],2500)
        self.assertAlmostEqual(profile['http_json_ms'],400)
        self.assertAlmostEqual(profile['rate_limit_wait_ms'],600)
        self.assertAlmostEqual(profile['total_ms'],3922)
        self.assertAlmostEqual(profile['other_ms'],0)

    async def test_reconnect_during_rpc_does_not_claim_complete_timing_profile(self):
        profile=await self.measured_decode(reconnect=True)
        self.assertFalse(profile['complete']);self.assertIsNone(profile['total_ms'])
        self.assertEqual(self.journal.db.execute('SELECT state FROM events').fetchone()[0],'done')

    async def test_phase_report_uses_only_complete_verified_profiles(self):
        from contextlib import redirect_stdout
        from io import StringIO
        await self.measured_decode()
        self.journal.observe(WALLET,'old',self.at,1,'rattrapage',True)
        self.journal.finish(WALLET,'old',[{'kind':'buy','swap_proof_version':1}],profile={'complete':False,'http_json_ms':9000})
        output=StringIO()
        with redirect_stdout(output):w.timing_report(self.path)
        self.assertIn('Profils détaillés complets : 1',output.getvalue())
        self.assertIn('RPC HTTP + JSON (cumul essais) : médiane 0,20 s',output.getvalue())
        self.assertIn('Total confirmed → décodage : médiane 0,81 s',output.getvalue())
        self.assertNotIn('9,00 s',output.getvalue())

    async def test_report_separates_old_two_rps_and_new_five_rps_profiles(self):
        from contextlib import redirect_stdout
        from io import StringIO
        profile=await self.measured_decode()
        old=dict(profile,version='2026.11.14.5',rpc_rps=2,http_json_ms=1000)
        new=dict(profile,version='2026.11.14.6',rpc_rps=5,http_json_ms=100)
        self.journal.finish(WALLET,SIGNATURE,[{'kind':'buy','swap_proof_version':1}],profile=old)
        self.journal.observe(WALLET,'new',self.at,1,'websocket',True)
        self.journal.finish(WALLET,'new',[{'kind':'buy','swap_proof_version':1}],profile=new)
        output=StringIO()
        with redirect_stdout(output):w.timing_report(self.path)
        self.assertIn('Version 2026.11.14.5 ; plafond 2/s ; profils complets : 1',output.getvalue())
        self.assertIn('Version 2026.11.14.6 ; plafond 5/s ; profils complets : 1',output.getvalue())
        self.assertNotIn('médiane 0,55 s',output.getvalue())

    def test_failed_unknown_subscription_and_non_trade_logs_are_ignored(self):
        w.notification(self.journal,notice(err={'InstructionError':[0,'failed']}),{1:(WALLET,'confirmed')},1)
        w.notification(self.journal,notice(),{},1)
        message=notice();message['params']['result']['value']['logs']=['Transfer only']
        w.notification(self.journal,message,{1:(WALLET,'confirmed')},1)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    async def test_actual_v1_buy_is_decoded_then_sent_once_with_persistent_dedup(self):
        self.journal.observe(WALLET,SIGNATURE,self.at+.1,1,'websocket',True)
        with patch.object(w.time,'time',return_value=self.at+1):
            self.assertTrue(await self.service.decode_one(self.at+1))
            self.assertFalse(await self.service.decode_one(self.at+1))
        self.sender.send.assert_not_called()  # Decoding never waits for Telegram.
        self.service.call.assert_awaited_once()
        params=self.service.call.call_args.args[1][1]
        self.assertEqual(params['commitment'],'confirmed');self.assertEqual(params['maxSupportedTransactionVersion'],1)
        self.assertTrue(await self.service.deliver_one(self.at+2))
        self.sender.send.assert_called_once();self.assertIn('ACHAT VÉRIFIÉ',self.sender.send.call_args.args[0])
        self.journal.observe(WALLET,SIGNATURE,self.at+3,1,'websocket',True)
        self.assertFalse(await self.service.decode_one(self.at+4))
        self.assertFalse(await self.service.deliver_one(self.at+4))

    async def test_transfers_failed_tx_and_non_signer_never_create_alerts(self):
        for mode in ('transfer','failed','not-signer'):
            tx=copy.deepcopy(self.tx)
            if mode=='transfer':tx['meta']['logMessages']=[]
            elif mode=='failed':tx['meta']['err']={'InstructionError':[0,'bad']}
            else:
                for key in tx['transaction']['message']['accountKeys']:
                    if isinstance(key,dict) and key.get('pubkey')==WALLET:key['signer']=False
            self.service.call.return_value=tx
            self.journal.observe(WALLET,mode,self.at+.1,1,'websocket',True)
            with patch.object(w.time,'time',return_value=self.at+1):await self.service.decode_one(self.at+1)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM alerts').fetchone()[0],0)

    async def test_sell_uses_same_strict_decoder_and_reports_tokens_and_proceeds(self):
        tx=copy.deepcopy(self.tx);meta=tx['meta']
        meta['preTokenBalances'],meta['postTokenBalances']=meta['postTokenBalances'],meta['preTokenBalances']
        meta['preBalances'],meta['postBalances']=meta['postBalances'],meta['preBalances']
        # Target transfer direction is part of the swap proof, so reverse it too.
        for group in meta.get('innerInstructions',[]):
            for instruction in group['instructions']:
                info=instruction.get('parsed',{}).get('info',{})
                if info.get('source') and info.get('destination'):
                    info['source'],info['destination']=info['destination'],info['source']
        meta['logMessages']=[s.replace('BuyExactQuoteInV3','Sell') for s in meta['logMessages']]
        trades=w.trades_in(tx,WALLET,SIGNATURE)
        self.assertTrue(any(t['kind']=='sell' for t in trades))

    async def test_transaction_not_ready_retries_without_blocking_other_events(self):
        self.service.call.return_value=None
        self.journal.observe(WALLET,SIGNATURE,self.at,1,'websocket',True)
        with patch.object(w.time,'time',return_value=self.at+1):await self.service.decode_one(self.at+1)
        self.assertIsNone(self.journal.due(self.at+2))
        self.service.call.return_value=self.tx
        with patch.object(w.time,'time',return_value=self.at+4):await self.service.decode_one(self.at+4)
        self.assertEqual(self.journal.db.execute('SELECT state FROM events').fetchone()[0],'done')

    async def test_first_boot_seeds_cursor_without_replaying_old_transactions(self):
        self.service.call.return_value=[{'signature':'baseline','err':None,'slot':1}]
        await self.service.backfill_wallet(WALLET)
        self.assertEqual(self.journal.db.execute('SELECT signature FROM cursors').fetchone()[0],'baseline')
        self.assertIsNone(self.journal.due(self.at+1))
        self.service.call.assert_awaited_once_with('getSignaturesForAddress',[WALLET,{'limit':1,'commitment':'confirmed'}])

    async def test_reconnection_paginates_to_saved_cursor_and_excludes_failed_transactions(self):
        self.journal.db.execute('INSERT INTO cursors VALUES (?,?)',(WALLET,'baseline'));self.journal.db.commit()
        page=[{'signature':f's{i}','err':None,'slot':1} for i in range(1000)]
        page[20]['err']='failed'
        self.service.call.side_effect=[page,[{'signature':'baseline','err':None,'slot':1}]]
        await self.service.backfill_wallet(WALLET)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM events').fetchone()[0],999)
        self.assertEqual(self.service.call.call_args.args[1][1]['before'],'s999')
        self.assertEqual(self.service.call.call_args_list[0].args[1][1]['limit'],1000)
        self.assertEqual(self.journal.db.execute('SELECT signature FROM cursors').fetchone()[0],'s0')

    async def test_initially_empty_wallet_does_not_lose_first_transaction_during_outage(self):
        self.service.call.return_value=[]
        await self.service.backfill_wallet(WALLET)
        self.service.call.return_value=[{'signature':SIGNATURE,'err':None,'slot':1}]
        await self.service.backfill_wallet(WALLET)
        self.assertEqual(self.journal.due(self.at+10**9)[1],SIGNATURE)

    async def test_truncated_backfill_records_persistent_coverage_gap(self):
        self.journal.db.execute('INSERT INTO cursors VALUES (?,?)',(WALLET,'old'));self.journal.db.commit()
        self.service.call.side_effect=[[
            {'signature':f'{page}-{i}','err':None,'slot':1} for i in range(1000)] for page in range(10)]
        await self.service.backfill_wallet(WALLET)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM gaps').fetchone()[0],1)
        self.assertEqual(self.service.call.await_count,10)

    async def test_subscription_handshake_waits_for_acknowledgements_then_accepts_notifications(self):
        class Socket:
            def __init__(self):self.sent=[];self.replies=asyncio.Queue()
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def send(self,raw):
                request=json.loads(raw);self.sent.append(request)
                await self.replies.put(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':request['id']}))
            async def recv(self):return await self.replies.get()
            def __aiter__(self):return self
            async def __anext__(self):
                if self.sent:
                    self.sent_notification=self.sent;self.sent=[]
                    return json.dumps(notice())
                await asyncio.Future()
        socket=Socket();service=w.Service(self.journal,{'DECU':WALLET},self.rpc,'wss://unused.invalid')
        with patch('websockets.asyncio.client.connect',return_value=socket):
            task=asyncio.create_task(service.websocket())
            try:
                await asyncio.wait_for(service.recover.wait(),2)
                for _ in range(30):
                    if self.journal.due(self.at+10**9):break
                    await asyncio.sleep(.01)
                self.assertEqual(self.journal.due(self.at+10**9)[1],SIGNATURE)
                request=socket.sent_notification[0]
                self.assertEqual(request['method'],'logsSubscribe')
                self.assertEqual(request['params'],[{'mentions':[WALLET]},{'commitment':'confirmed'}])
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_early_mode_subscribes_to_both_commitments_for_each_wallet(self):
        class Socket:
            def __init__(self):self.sent=[];self.replies=asyncio.Queue()
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def send(self,raw):
                request=json.loads(raw);self.sent.append(request)
                await self.replies.put(json.dumps({'id':request['id'],'result':request['id']}))
            async def recv(self):return await self.replies.get()
            def __aiter__(self):return self
            async def __anext__(self):await asyncio.Future()
        socket=Socket();self.service.early=True
        with patch('websockets.asyncio.client.connect',return_value=socket):
            task=asyncio.create_task(self.service.websocket())
            try:
                await asyncio.wait_for(self.service.recover.wait(),2)
                pairs={(r['params'][0]['mentions'][0],r['params'][1]['commitment']) for r in socket.sent}
                self.assertEqual(pairs,{(wallet,level) for wallet in w.WATCHED.values() for level in ('processed','confirmed')})
                self.assertEqual(len(socket.sent),4)
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_fresh_websocket_events_have_priority_over_backfill(self):
        self.journal.observe(WALLET,'old',self.at,1,'rattrapage',True)
        self.journal.observe(WALLET,'fresh',self.at+1,1,'websocket',True)
        self.assertEqual(self.journal.due(self.at+2)[1],'fresh')

    async def test_historical_trades_are_local_only_and_expired_alerts_not_sent(self):
        self.journal.observe(WALLET,SIGNATURE,self.at,1,'rattrapage',True)
        with patch.object(w.time,'time',return_value=self.at+300):await self.service.decode_one(self.at+300)
        self.assertFalse(await self.service.deliver_one(self.at+300));self.sender.send.assert_not_called()
        self.journal.finish(WALLET,SIGNATURE,[],[(MINT,'old',self.at+1)])
        self.assertTrue(await self.service.deliver_one(self.at+2));self.sender.send.assert_not_called()

    async def test_timeout_is_uncertain_and_restart_never_resends_ambiguous_alert(self):
        self.journal.finish(WALLET,SIGNATURE,[],[(MINT,'alert',self.at+120)])
        self.sender.send.side_effect=TimeoutError('secret-key must never be stored')
        await self.service.deliver_one(self.at+2)
        row=self.journal.db.execute('SELECT state,error FROM alerts').fetchone()
        self.assertEqual(row,('uncertain','TimeoutError'))
        self.assertFalse(await self.service.deliver_one(self.at+3))

    def test_recovery_marks_interrupted_send_uncertain_and_binds_telegram_destination(self):
        self.journal.db.execute("INSERT INTO alerts (wallet,signature,mint,state) VALUES (?,?,?,'sending')",(WALLET,SIGNATURE,MINT));self.journal.db.commit()
        other=w.Journal(self.path)
        try:self.assertEqual(other.db.execute('SELECT state FROM alerts').fetchone()[0],'uncertain')
        finally:other.db.close()
        changed=MagicMock();changed.destination='different'
        with self.assertRaises(ValueError):w.Service(self.journal,dict(w.WATCHED),self.rpc,'wss://unused.invalid',sender=changed)

    async def test_decoder_failure_is_sanitized_and_eventually_stops_retrying(self):
        self.journal.observe(WALLET,SIGNATURE,self.at,1,'websocket',True)
        self.service.call.side_effect=ValueError('provider URL includes api-key=secret')
        for attempt in range(12):
            with patch.object(w.time,'time',return_value=self.at+attempt*60):await self.service.decode_one(self.at+attempt*60)
        row=self.journal.db.execute('SELECT state,error FROM events').fetchone()
        self.assertEqual(row,('unavailable','ValueError'));self.sender.send.assert_not_called()

