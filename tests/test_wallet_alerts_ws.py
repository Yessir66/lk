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

    def test_processed_never_enters_decoder_without_confirmed_notification(self):
        w.notification(self.journal,notice(),{1:(WALLET,'processed')},self.at+.1)
        self.assertIsNone(self.journal.due(self.at+1))
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at+.5)
        row=self.journal.due(self.at+1)
        self.assertEqual(row[2],self.at+.1);self.assertEqual(row[3],self.at+.5)
        w.notification(self.journal,notice(),{1:(WALLET,'confirmed')},self.at+1)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM events').fetchone()[0],1)

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
        page=[{'signature':f's{i}','err':None,'slot':1} for i in range(100)]
        page[20]['err']='failed'
        self.service.call.side_effect=[page,[{'signature':'baseline','err':None,'slot':1}]]
        await self.service.backfill_wallet(WALLET)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM events').fetchone()[0],99)
        self.assertEqual(self.service.call.call_args.args[1][1]['before'],'s99')
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
            {'signature':f'{page}-{i}','err':None,'slot':1} for i in range(100)] for page in range(10)]
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

