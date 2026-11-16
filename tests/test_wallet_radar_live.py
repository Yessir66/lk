"""Offline tests: no real keys, signatures submitted, RPC or financial operations."""
import asyncio
import base64
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import urllib.error
from io import StringIO
from unittest.mock import AsyncMock,Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wallet_radar_live as live
from wallet_radar_paper import Prices,public_key

MINT=public_key(bytes([2])*32)


def quote(source=live.SOL,dest=MINT,amount=10000000):
    return dict(inputMint=source,outputMint=dest,inAmount=str(amount),outAmount='100000',
                otherAmountThreshold='80000',slippageBps=2000,swapMode='ExactIn',routePlan=[{'percent':100}])


def account(key,signer=False,writable=False):return dict(pubkey=key,isSigner=signer,isWritable=writable)


def payload(q=None):
    q=q or quote()
    keys=[live.TOKEN,live.OWNER,live.associated(q['inputMint']),live.associated(q['outputMint']),
          live.JUP,q['outputMint'],live.JUP,live.ATA,live.JUP]
    data=hashlib.sha256(b'global:route').digest()[:8]+struct.pack('<I',0)+struct.pack('<QQHB',int(q['inAmount']),int(q['outAmount']),2000,0)
    result={'setupInstructions':[],'swapInstruction':dict(programId=live.JUP,accounts=[account(k,i==1) for i,k in enumerate(keys)],data=base64.b64encode(data).decode())}
    if q['inputMint']==live.SOL:
        data=struct.pack('<IQ',2,int(q['inAmount']))
        result['setupInstructions'].append(dict(programId=live.SYSTEM,accounts=[account(live.OWNER,True,True),account(live.associated(live.SOL),False,True)],data=base64.b64encode(data).decode()))
    return result


class BudgetTest(unittest.TestCase):
    def test_persistent_budget_never_recycles_sales_or_failed_fees(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'live.sqlite3';ledger=live.Ledger(path)
            for i in range(6):
                mint=str(i);ledger.reserve(mint,{'buy':{'signature':'fake'}})
                with self.assertRaises(live.TradeError):ledger.reserve('parallel',{})
                ledger.save(mint,'closed',{})
            self.assertEqual(ledger.used(),Decimal('14.40'))
            with self.assertRaises(live.TradeError):ledger.reserve('too-much',{})
            with self.assertRaises(live.TradeError):ledger.reserve('0',{})
            ledger.db.close();ledger=live.Ledger(path)
            self.assertEqual(ledger.used(),Decimal('14.40'))
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            ledger.db.close()

    def test_private_file_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'key';path.write_text('fake-secret');path.chmod(0o644)
            with self.assertRaises(live.TradeError):live.secure_read(path)
            path.chmod(0o600);self.assertEqual(live.secure_read(path),'fake-secret')
            link=Path(tmp)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):live.secure_read(link)


class InstructionTest(unittest.TestCase):
    def test_jupiter_get_uses_curl_without_get_content_type_and_never_sends_order(self):
        transport=live.Transport('https://unused.invalid','FAKE_TEST_KEY')
        response=Mock(status_code=200);response.json.return_value=quote()
        with patch.object(transport.jupiter_http,'request',return_value=response) as client,patch('urllib.request.urlopen') as standard:
            result=transport.jup(live.quote_path(live.SOL,MINT,1000))
        self.assertEqual(result,quote());standard.assert_not_called()
        self.assertEqual(client.call_args.args[0],'GET')
        headers=client.call_args.kwargs['headers']
        self.assertNotIn('Content-Type',headers);self.assertEqual(headers['x-api-key'],'FAKE_TEST_KEY')
        self.assertFalse(client.call_args.kwargs['allow_redirects'])

    def test_curl_http_400_html_is_reported_without_echoing_body(self):
        transport=live.Transport('https://unused.invalid','FAKE_TEST_KEY')
        response=Mock(status_code=400,headers={'Content-Type':'text/html'},content=b'<html>PRIVATE_SECRET</html>')
        with patch.object(transport.jupiter_http,'request',return_value=response):
            with self.assertRaises(live.TradeError) as caught:transport.jup(live.quote_path(live.SOL,MINT,1000))
        self.assertIn('HTTP 400',str(caught.exception));self.assertIn('HTML',str(caught.exception))
        self.assertNotIn('PRIVATE',str(caught.exception))
        self.assertEqual(live.jupiter_reason({'message':'API key invalid PRIVATE_SECRET'}),'cle API Jupiter absente ou refusee')

    def test_curl_dns_errno_is_not_misreported_as_http_status(self):
        transport=live.Transport('https://unused.invalid','FAKE_TEST_KEY')
        class CurlFailure(Exception):code=6
        with patch.object(transport.jupiter_http,'request',side_effect=CurlFailure('PRIVATE_URL')):
            with self.assertRaises(live.TradeError) as caught:transport.jup(live.quote_path(live.SOL,MINT,1000))
        self.assertIn('DNS',str(caught.exception));self.assertNotIn('HTTP 6',str(caught.exception))
        self.assertNotIn('PRIVATE',str(caught.exception))

    def test_known_jupiter_error_code_is_safe_but_arbitrary_body_is_never_printed(self):
        transport=live.Transport('https://unused.invalid','FAKE')
        data={'errorCode':'COULD_NOT_FIND_ANY_ROUTE','error':'PRIVATE_KEY_AND_BODY'}
        exc=urllib.error.HTTPError('https://private.invalid/SECRET_URL',400,'SECRET_REASON',{},StringIO(json.dumps(data)))
        with patch.object(transport.jupiter_http,'request',side_effect=exc):
            with self.assertRaises(live.TradeError) as caught:transport.jup(live.quote_path(live.SOL,MINT,1000))
        message=str(caught.exception)
        self.assertIn('HTTP 400',message);self.assertIn('COULD_NOT_FIND_ANY_ROUTE',message)
        self.assertNotIn('PRIVATE',message);self.assertNotIn('SECRET',message)
        self.assertIsNone(live.jupiter_reason({'errorCode':'PRIVATE_FAKE_CODE','error':'PRIVATE_BODY'}))
        self.assertIsNone(live.jupiter_reason({'errorCode':[],'error':'PRIVATE_BODY'}))
        self.assertEqual(live.jupiter_reason({'error':'PRIVATE: restrict_intermediate_tokens cannot be false'}),
                         'parametre restrictIntermediateTokens refuse ou requis')

    def test_quote_parameters_are_shared_and_keep_direct_routes_and_slippage(self):
        from urllib.parse import parse_qs,urlsplit
        values=parse_qs(urlsplit(live.quote_path(live.SOL,MINT,1000)).query)
        for name in ('onlyDirectRoutes','restrictIntermediateTokens','asLegacyTransaction'):
            self.assertEqual(values[name],['true'])
        self.assertEqual(values['slippageBps'],['2000']);self.assertEqual(values['amount'],['1000'])

    def test_provider_error_identifies_stage_and_status_without_secret(self):
        transport=live.Transport('https://unused.invalid/?api-key=PRIVATE_RPC','PRIVATE_JUPITER')
        exc=urllib.error.HTTPError('https://private.invalid/PRIVATE_URL',403,'PRIVATE_BODY',{},None)
        with patch.object(transport.jupiter_http,'request',side_effect=exc):
            with self.assertRaises(live.TradeError) as caught:transport.jup('/swap/v1/swap-instructions',{})
        self.assertIn('Jupiter/construction : HTTP 403',str(caught.exception))
        self.assertNotIn('PRIVATE',str(caught.exception))

    def test_rpc_error_reports_only_safe_code_and_sets_quota_pause(self):
        transport=live.Transport('https://unused.invalid/?api-key=PRIVATE_RPC','PRIVATE_JUPITER')
        response=Mock();response.__enter__=Mock(return_value=StringIO(json.dumps({'error':{'code':429,'message':'PRIVATE_BODY'}})));response.__exit__=Mock(return_value=False)
        with patch('urllib.request.urlopen',return_value=response):
            with self.assertRaises(live.TradeError) as caught:transport.rpc('getBalance',[])
        self.assertIn('RPC/getBalance',str(caught.exception));self.assertIn('429',str(caught.exception))
        self.assertNotIn('PRIVATE',str(caught.exception));self.assertGreater(transport.pause_rpc,time.monotonic())

    def test_network_error_does_not_print_url_or_exception_text(self):
        transport=live.Transport('https://unused.invalid/PRIVATE_RPC','PRIVATE_JUPITER')
        with patch('urllib.request.urlopen',side_effect=urllib.error.URLError('PRIVATE_BODY')):
            with self.assertRaises(live.TradeError) as caught:transport.rpc('getBalance',[])
        self.assertIn('RPC/getBalance',str(caught.exception));self.assertNotIn('PRIVATE',str(caught.exception))

    def test_quote_mint_amount_slippage_and_minimum_are_bound(self):
        q=quote();live.quote_check(q,live.SOL,MINT,10000000)
        for key,value in [('outputMint',live.OWNER),('inAmount','9'),('slippageBps',7500),('otherAmountThreshold','1'),('swapMode','ExactOut'),('platformFee',{'feeBps':1})]:
            with self.subTest(key=key),self.assertRaises(live.TradeError):
                live.quote_check(dict(q,**{key:value}),live.SOL,MINT,10000000)

    def test_own_route_builds_without_provider_transaction_or_alt(self):
        result=live.checked_instructions(payload(),quote(),1)
        self.assertEqual(len(result),4)

    def test_external_transfer_delegate_route_amount_and_signer_are_rejected(self):
        import copy
        baseline=payload()
        mutations=[]
        p=copy.deepcopy(baseline);p['setupInstructions'][0]['accounts'][1]['pubkey']=MINT;mutations.append(p)
        p=copy.deepcopy(baseline);p['swapInstruction']['accounts'][1]['pubkey']=MINT;mutations.append(p)
        p=copy.deepcopy(baseline);p['swapInstruction']['accounts'][3]['pubkey']=live.OWNER;mutations.append(p)
        p=copy.deepcopy(baseline);p['otherInstructions']=[{}];mutations.append(p)
        p=copy.deepcopy(baseline);p['setupInstructions'][0]['programId']=live.TOKEN;mutations.append(p)
        p=copy.deepcopy(baseline);raw=base64.b64decode(p['swapInstruction']['data']);p['swapInstruction']['data']=base64.b64encode(raw[:-19]+struct.pack('<QQHB',999,100000,2000,0)).decode();mutations.append(p)
        for p in mutations:
            with self.subTest(payload=p),self.assertRaises(live.TradeError):live.checked_instructions(p,quote(),1)

    def test_no_network_submission_when_deadline_passed_inside_transport(self):
        transport=live.Transport('https://unused.invalid','fake')
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(live.TradeError):transport.send('fake',time.monotonic()-1)
            network.assert_not_called()


class ExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.e=live.Executor.__new__(live.Executor)
        self.e.ledger=live.Ledger(Path(self.tmp.name)/'live.sqlite3')
        self.e.prices=Prices();self.e.prices.usd=Decimal(100);self.e.prices.published=time.time()
        self.e.priority=1;self.e.priority_at=time.monotonic()
        self.e.jobs=asyncio.Queue(maxsize=1)
        self.e.preparing=None;self.e.prepared_mint=None
        self.e.transport=Mock();self.e.rpc=AsyncMock();self.e.jup=AsyncMock()
        self.e.last_notice={}

    async def asyncTearDown(self):
        if self.e.preparing and not self.e.preparing.done():
            self.e.preparing.cancel();await asyncio.gather(self.e.preparing,return_exceptions=True)
        self.e.ledger.db.close();self.tmp.cleanup()

    async def test_condition_does_not_submit_without_prior_preparation(self):
        self.e.signal(dict(mint=MINT,code='ACHAT_SIMULE',elapsed_s=1))
        task=asyncio.create_task(self.e.buy_loop())
        await asyncio.sleep(.01);task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.e.rpc.assert_not_awaited();self.e.transport.send.assert_not_called()
        self.assertFalse(self.e.ledger.active())

    async def test_first_signal_only_prepares_unsigned_no_reservation_or_send(self):
        self.e.prepare=AsyncMock(return_value={'fake':'unsigned'})
        self.e.signal(dict(mint=MINT,code='SUIVI'))
        await self.e.preparing
        self.e.prepare.assert_awaited_once_with(MINT,'buy')
        self.assertEqual(self.e.ledger.used(),0);self.e.transport.send.assert_not_called()

    async def test_send_timeout_keeps_pending_signature_and_budget_across_restart(self):
        data={'buy':{'raw':'fake','signature':'fake-signature','height':100},'decision_wall':time.time()}
        self.e.ledger.reserve(MINT,data)
        self.e.transport.send.side_effect=TimeoutError('secret URL must never print')
        await self.e.send(MINT,data,'buy')
        row=self.e.ledger.get(MINT)
        self.assertEqual(row['state'],'buy_pending');self.assertEqual(Decimal(row['reserved']),live.ENVELOPE)
        self.assertEqual(json.loads(row['data'])['buy']['signature'],'fake-signature')
        self.e.signal(dict(mint=MINT,code='SUIVI'))
        self.assertIsNone(self.e.preparing)

    async def test_fast_confirmation_is_not_overwritten_by_late_send_response(self):
        import threading
        data={'buy':{'raw':'fake','signature':'fake','height':100},'sell_attempts':0,'decision_wall':time.time()}
        self.e.ledger.reserve(MINT,data)
        started=threading.Event();release=threading.Event()
        def submit(raw,deadline,capture):
            capture['request_at']=time.time();started.set()
            if not release.wait(2):raise TimeoutError()
            return 'fake'
        self.e.transport.send=submit
        task=asyncio.create_task(self.e.send(MINT,data,'buy'))
        try:
            while not started.is_set():await asyncio.sleep(.001)
            self.e.rpc.side_effect=[{'value':[{'confirmationStatus':'confirmed'}]}, {'meta':{'err':None,'postTokenBalances':[
                {'owner':live.OWNER,'mint':MINT,'uiTokenAmount':{'amount':'42'}}]}}]
            await self.e.reconcile(self.e.ledger.get(MINT))
            self.assertEqual(self.e.ledger.get(MINT)['state'],'open')
        finally:release.set();await task
        row=self.e.ledger.get(MINT);saved=json.loads(row['data'])
        self.assertEqual(row['state'],'open');self.assertEqual(saved['quantity'],42)
        self.assertIn('send_at',saved['buy'])

    async def test_confirmed_buy_opens_only_on_actual_token_delta(self):
        data={'buy':{'signature':'fake','send_at':time.time(),'height':100},'sell_attempts':0}
        self.e.ledger.reserve(MINT,data)
        self.e.rpc.side_effect=[{'value':[{'confirmationStatus':'confirmed'}]}, {'meta':{'err':None,'postTokenBalances':[
            {'owner':live.OWNER,'mint':MINT,'uiTokenAmount':{'amount':'42'}}]}}]
        await self.e.reconcile(self.e.ledger.get(MINT))
        row=self.e.ledger.get(MINT)
        self.assertEqual(row['state'],'open');self.assertEqual(json.loads(row['data'])['quantity'],42)

    async def test_expired_buy_is_never_replaced_and_race_is_rechecked(self):
        data={'buy':{'signature':'fake','height':100}}
        self.e.ledger.reserve(MINT,data)
        self.e.rpc.side_effect=[{'value':[None]},101,{'value':[{'confirmationStatus':'processed'}]}]
        await self.e.reconcile(self.e.ledger.get(MINT))
        self.assertEqual(self.e.ledger.get(MINT)['state'],'buy_pending')
        self.e.rpc.side_effect=[{'value':[None]},101,{'value':[None]}]
        await self.e.reconcile(self.e.ledger.get(MINT))
        self.assertEqual(self.e.ledger.get(MINT)['state'],'expired');self.e.transport.send.assert_not_called()

    async def test_failed_sell_stops_after_three_attempts(self):
        data={'sell':{'signature':'fake','height':100},'sell_attempts':3}
        self.e.ledger.save(MINT,'sell_pending',data,reserve=live.ENVELOPE)
        self.e.rpc.side_effect=[{'value':[{'confirmationStatus':'confirmed'}]}, {'meta':{'err':{'InstructionError':[0,'Custom']}}}]
        await self.e.reconcile(self.e.ledger.get(MINT))
        self.assertEqual(self.e.ledger.get(MINT)['state'],'manual')
        self.assertTrue(self.e.ledger.active())

    async def test_tp_triggers_sell_of_actual_position(self):
        data={'quantity':42,'entry_usd':'1','sell_attempts':0}
        self.e.ledger.save(MINT,'open',data,reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=42)
        self.e.quote=AsyncMock(return_value={'outAmount':'17500000'})
        self.e.prepare=AsyncMock(return_value={'fake':'unsigned'})
        self.e.signed=Mock(return_value={'signature':'fake','raw':'fake','height':100})
        async def send(*args):raise asyncio.CancelledError()
        self.e.send=AsyncMock(side_effect=send)
        with self.assertRaises(asyncio.CancelledError):await self.e.positions()
        row=self.e.ledger.get(MINT);saved=json.loads(row['data'])
        self.assertEqual(row['state'],'sell_pending');self.assertEqual(saved['exit_reason'],'TP')
        self.assertEqual(saved['sell_attempts'],1)

    async def test_sl_triggers_at_twenty_percent_remaining(self):
        data={'quantity':42,'entry_usd':'1','sell_attempts':0}
        self.e.ledger.save(MINT,'open',data,reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=42)
        self.e.quote=AsyncMock(return_value={'outAmount':'2000000'})
        self.e.prepare=AsyncMock(return_value={'fake':'unsigned'})
        self.e.signed=Mock(return_value={'signature':'fake','raw':'fake','height':100})
        self.e.send=AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):await self.e.positions()
        self.assertEqual(json.loads(self.e.ledger.get(MINT)['data'])['exit_reason'],'SL')

    async def test_manual_balance_change_blocks_new_orders(self):
        data={'quantity':42,'entry_usd':'1','sell_attempts':0}
        self.e.ledger.save(MINT,'open',data,reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=41)
        task=asyncio.create_task(self.e.positions());await asyncio.sleep(.01)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.e.ledger.get(MINT)['state'],'manual')
        self.e.jup.assert_not_awaited();self.e.transport.send.assert_not_called()

    async def test_preparation_checks_fees_and_simulation_before_local_signing(self):
        from solders.keypair import Keypair
        from solders.hash import Hash
        from solders.transaction import VersionedTransaction
        key=Keypair.from_seed(bytes([3])*32)
        with patch.object(live,'OWNER',str(key.pubkey())):
            q=quote();p=payload(q)
            self.e.key=key;self.e.holdings=AsyncMock(return_value=0)
            self.e.jup.side_effect=[q,p]
            async def rpc(method,params):
                if method=='getLatestBlockhash':return {'value':{'blockhash':str(Hash.default()),'lastValidBlockHeight':100}}
                if method=='getFeeForMessage':return {'value':5000}
                if method=='getBalance':return {'value':100000000}
                if method=='simulateTransaction':return {'value':{'err':None,'accounts':[{'owner':live.SYSTEM,'lamports':90000000}]}}
                raise AssertionError('Unexpected method: '+method)
            self.e.rpc.side_effect=rpc
            prepared=await self.e.prepare(MINT,'buy')
            self.e.transport.send.assert_not_called()
            self.e.prices.usd=Decimal(200)
            with self.assertRaises(live.TradeError):self.e.signed(prepared)
            self.e.prices.usd=Decimal(100)
            signed=self.e.signed(prepared)
            tx=VersionedTransaction.from_bytes(base64.b64decode(signed['raw']))
            self.assertEqual(tx.verify_with_results(),[True])
            self.assertEqual(str(tx.message.account_keys[0]),str(key.pubkey()))
            self.assertEqual(self.e.ledger.used(),0)

    async def test_buy_is_committed_before_any_send_attempt(self):
        self.e.prepared_mint=MINT
        self.e.preparing=asyncio.create_task(asyncio.sleep(0,result=dict(amount=10000000,price='100',prepared=time.monotonic(),prepare_ms=1)))
        self.e.signed=Mock(return_value={'signature':'fake','raw':'fake','height':100})
        async def submit(mint,data,side,deadline):
            row=self.e.ledger.get(mint)
            self.assertEqual(row['state'],'buy_pending')
            self.assertEqual(Decimal(row['reserved']),live.ENVELOPE)
            self.assertEqual(json.loads(row['data'])['buy']['signature'],'fake')
            raise asyncio.CancelledError()
        self.e.send=AsyncMock(side_effect=submit)
        self.e.signal(dict(mint=MINT,code='ACHAT_SIMULE',elapsed_s=1.5))
        with self.assertRaises(asyncio.CancelledError):await self.e.buy_loop()
        self.e.send.assert_awaited_once()

    async def test_high_priority_fee_blocks_before_any_quote(self):
        self.e.priority=10**12
        with self.assertRaises(live.TradeError):await self.e.prepare(MINT,'buy')
        self.e.jup.assert_not_awaited();self.e.rpc.assert_not_awaited()

    async def test_old_preparation_cannot_be_submitted_after_three_seconds(self):
        self.e.prepared_mint=MINT
        self.e.preparing=asyncio.create_task(asyncio.sleep(0,result={'prepared':time.monotonic()-3}))
        await self.e.preparing
        self.e.signal(dict(mint=MINT,code='ACHAT_SIMULE',elapsed_s=3.1))
        task=asyncio.create_task(self.e.buy_loop());await asyncio.sleep(.01)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.e.transport.send.assert_not_called();self.assertEqual(self.e.ledger.used(),0)

    async def test_condition_completion_does_not_stop_price_or_exit_monitor(self):
        observer=live.LiveObserver.__new__(live.LiveObserver)
        observer.prices=Mock();observer.executor=Mock()
        async def forever():await asyncio.Future()
        observer.prices.run=AsyncMock(side_effect=forever)
        observer.prices.run_doge=AsyncMock(side_effect=forever)
        observer.executor.run=AsyncMock(side_effect=forever)
        with patch.object(live.PaperObserver,'run',new_callable=AsyncMock) as conditions:
            task=asyncio.create_task(observer.run());await asyncio.sleep(.01)
            self.assertFalse(task.done());conditions.assert_awaited_once()
            observer.prices.run.assert_awaited_once();observer.executor.run.assert_awaited_once()
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            observer.executor.ledger.db.close.assert_called_once()

    async def test_stale_price_prevents_preparation(self):
        self.e.prices.published=time.time()-100
        with self.assertRaises(live.TradeError):await self.e.prepare(MINT,'buy')
        self.e.rpc.assert_not_awaited();self.e.jup.assert_not_awaited()


class SetupAndCheckTest(unittest.TestCase):
    def test_setup_reuses_existing_keys_without_prompt_or_overwrite(self):
        from solders.keypair import Keypair
        key=Keypair.from_seed(bytes([4])*32)
        with tempfile.TemporaryDirectory() as tmp,patch.object(live,'PRIVATE',Path(tmp)),patch.object(live,'OWNER',str(key.pubkey())):
            wallet=Path(tmp)/'wallet.json';wallet.write_text(json.dumps(list(bytes(key))));wallet.chmod(0o600)
            api=Path(tmp)/'jupiter_api_key.txt';api.write_text('FAKE_TEST_KEY');api.chmod(0o600)
            before=[(p.read_bytes(),p.stat().st_mtime_ns) for p in (wallet,api)]
            with patch.object(live.getpass,'getpass',side_effect=AssertionError('No prompt allowed')),patch('builtins.print'):
                live.setup(reuse=True)
            self.assertEqual(before,[(p.read_bytes(),p.stat().st_mtime_ns) for p in (wallet,api)])

    def test_check_is_read_only_and_announces_all_three_steps(self):
        q=quote(live.SOL,live.USDC,1000000)
        transport=Mock();transport.rpc.return_value={'value':0};transport.jup.side_effect=[q,payload(q)]
        key=Mock();output=StringIO()
        with patch.object(sys,'argv',['wallet_radar_live.py','--check']),patch.object(live,'load_signer',return_value=key),patch.object(live,'secure_read',return_value='FAKE_TEST_KEY'),patch.object(live,'Transport',return_value=transport),patch('wallet_alerts_ws.settings',return_value=({}, {'BBIG BOSS':live.BBIG},'https://unused.invalid','wss://unused.invalid')),patch('sys.stdout',output),patch.object(live,'setup') as setup,patch.object(live,'Ledger') as ledger:
            live.main()
        for step in ('1/3','2/3','3/3'):self.assertIn('CONTROLE '+step,output.getvalue())
        key.sign_message.assert_not_called();transport.send.assert_not_called();setup.assert_not_called();ledger.assert_not_called()
        self.assertNotIn('FAKE_TEST_KEY',output.getvalue())


if __name__=='__main__':unittest.main()
