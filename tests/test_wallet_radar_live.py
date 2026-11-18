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


def indirect_quote():
    q=quote()
    q['routePlan']=[{'percent':100,'swapInfo':{'inputMint':live.SOL,'outputMint':live.USDC}},
                    {'percent':100,'swapInfo':{'inputMint':live.USDC,'outputMint':MINT}}]
    return q


def lookup_result(addresses,deactivation=2**64-1):
    from solders.pubkey import Pubkey
    raw=struct.pack('<IQQBB',1,deactivation,1,0,1)+bytes(32)+bytes(2)
    raw+=b''.join(bytes(Pubkey.from_string(key)) for key in addresses)
    return {'context':{'slot':10},'value':[{'owner':live.ALT_PROGRAM,'executable':False,
        'data':[base64.b64encode(raw).decode(),'base64']}]}


def quote(source=live.SOL,dest=MINT,amount=10000000):
    return dict(inputMint=source,outputMint=dest,inAmount=str(amount),outAmount='100000',
                otherAmountThreshold='80000',slippageBps=2000,swapMode='ExactIn',routePlan=[{'percent':100,'swapInfo':{'inputMint':source,'outputMint':dest}}])


def account(key,signer=False,writable=False):return dict(pubkey=key,isSigner=signer,isWritable=writable)


def token_snapshot(amount,mint=MINT,owner=None,program=None):
    from solders.pubkey import Pubkey
    raw=bytearray(165);raw[:32]=bytes(Pubkey.from_string(mint));raw[32:64]=bytes(Pubkey.from_string(owner or live.OWNER))
    struct.pack_into('<Q',raw,64,amount);raw[108]=1
    return {'owner':program or live.TOKEN,'lamports':1513840,'data':[base64.b64encode(raw).decode(),'base64']}


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
            for i in range(18):
                mint=str(i);ledger.reserve(mint,{'buy':{'signature':'fake'}})
                with self.assertRaises(live.TradeError):ledger.reserve('parallel',{})
                ledger.save(mint,'closed',{})
            self.assertEqual(ledger.used(),Decimal('43.20'))
            with self.assertRaises(live.TradeError):ledger.reserve('too-much',{})
            with self.assertRaises(live.TradeError):ledger.reserve('0',{})
            ledger.db.close();ledger=live.Ledger(path)
            self.assertEqual(ledger.used(),Decimal('43.20'))
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            ledger.db.close()

    def test_private_file_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'key';path.write_text('fake-secret');path.chmod(0o644)
            with self.assertRaises(live.TradeError):live.secure_read(path)
            path.chmod(0o600);self.assertEqual(live.secure_read(path),'fake-secret')
            link=Path(tmp)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):live.secure_read(link)

    def test_budget_increase_preserves_old_reserved_fees_and_signatures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'live.sqlite3';ledger=live.Ledger(path)
            with patch.object(live,'BUDGET',Decimal('15')):
                for i in range(6):
                    mint='old-'+str(i);ledger.reserve(mint,{'buy':{'signature':'old-signature-'+str(i)}})
                    ledger.save(mint,'expired' if i<2 else 'closed',{'buy':{'signature':'old-signature-'+str(i)}})
                with self.assertRaises(live.TradeError):ledger.reserve('old-over-budget',{})
            old=ledger.rows();ledger.db.close();ledger=live.Ledger(path)
            self.assertEqual(ledger.used(),Decimal('14.40'));self.assertEqual(ledger.rows(),old)
            for i in range(12):
                mint='new-'+str(i);ledger.reserve(mint,{'buy':{'signature':'new-signature-'+str(i)}});ledger.save(mint,'closed',{'buy':{'signature':'new-signature-'+str(i)}})
            self.assertEqual(ledger.used(),Decimal('43.20'));self.assertEqual(ledger.rows()[:6],old)
            with self.assertRaises(live.TradeError):ledger.reserve('over-45',{})
            ledger.db.close()


class InstructionTest(unittest.TestCase):
    def test_simulated_token_account_requires_exact_mint_owner_and_initialized_layout(self):
        self.assertEqual(live.token_amount(token_snapshot(123),MINT),123)
        self.assertEqual(live.token_amount(token_snapshot(123,program=live.TOKEN22),MINT),123)
        bad=[token_snapshot(123,mint=live.SOL),token_snapshot(123,owner=MINT),token_snapshot(123,program=live.SYSTEM),
             {'owner':live.TOKEN,'data':['SECRET_INVALID','base64']},dict(token_snapshot(123),executable=True)]
        frozen=token_snapshot(123);raw=bytearray(base64.b64decode(frozen['data'][0]));raw[108]=2;frozen['data'][0]=base64.b64encode(raw).decode();bad.append(frozen)
        for account_data in bad:
            with self.subTest(account=account_data),self.assertRaises(live.TradeError) as error:live.token_amount(account_data,MINT)
            self.assertNotIn('SECRET',str(error.exception))

    def test_send_priority_precedes_waiting_read_without_bypassing_rpc_quota(self):
        from concurrent.futures import ThreadPoolExecutor
        transport=live.Transport('https://unused.invalid','FAKE')
        transport.next_rpc=time.monotonic()+2
        order=[]
        def use_slot(priority):
            with transport.slot(False,priority,time.monotonic()+2,{},'RPC'):
                order.append((priority,time.monotonic()))
        with ThreadPoolExecutor(max_workers=2) as pool:
            low=pool.submit(use_slot,2)
            with transport.rpc_condition:
                self.assertTrue(transport.rpc_condition.wait_for(lambda:len(transport.rpc_waiters)==1,timeout=1))
            high=pool.submit(use_slot,0)
            with transport.rpc_condition:
                self.assertTrue(transport.rpc_condition.wait_for(lambda:len(transport.rpc_waiters)==2,timeout=1))
                transport.next_rpc=0;transport.rpc_condition.notify_all()
            high.result(timeout=1);low.result(timeout=1)
        self.assertEqual([priority for priority,_ in order],[0,2])
        self.assertGreaterEqual(order[1][1]-order[0][1],.32)
        self.assertFalse(transport.rpc_busy);self.assertEqual(transport.rpc_waiters,[])

    def test_expired_priority_waiter_is_removed(self):
        transport=live.Transport('https://unused.invalid','FAKE');transport.next_rpc=time.monotonic()+2
        with self.assertRaises(live.TradeError):
            with transport.slot(False,0,time.monotonic()+.01,{},'RPC'):self.fail('Expired request admitted')
        self.assertEqual(transport.rpc_waiters,[]);self.assertFalse(transport.rpc_busy)
    def test_two_hop_route_allows_own_intermediate_account_only(self):
        q=indirect_quote();live.quote_check(q,live.SOL,MINT,10000000)
        p=payload(q)
        setup=dict(programId=live.ATA,data=base64.b64encode(b'\x01').decode(),accounts=[
            account(live.OWNER,True,True),account(live.associated(live.USDC),False,True),
            account(live.OWNER),account(live.USDC),account(live.SYSTEM),account(live.TOKEN)])
        p['setupInstructions'].append(setup)
        self.assertEqual(len(live.checked_instructions(p,q,1)),5)
        setup['accounts'][2]['pubkey']=MINT
        with self.assertRaises(live.TradeError):live.checked_instructions(p,q,1)
        setup['accounts'][2]['pubkey']=live.OWNER
        setup['accounts'][3]['pubkey']=public_key(bytes([9])*32)
        with self.assertRaises(live.TradeError):live.checked_instructions(p,q,1)

    def test_cycles_disconnected_and_unfinished_route_branches_are_rejected(self):
        unknown=public_key(bytes([9])*32)
        extra=[{'inputMint':unknown,'outputMint':MINT}, {'inputMint':live.USDC,'outputMint':live.SOL},
               {'inputMint':live.USDC,'outputMint':unknown}]
        for info in extra:
            q=indirect_quote();q['routePlan'].append({'percent':100,'swapInfo':info})
            with self.subTest(info=info),self.assertRaises(live.TradeError):live.quote_check(q,live.SOL,MINT,10000000)
        q=indirect_quote();q['routePlan'].append({'swapInfo':{'inputMint':live.USDC,'outputMint':unknown}})
        q['routePlan'].append({'swapInfo':{'inputMint':unknown,'outputMint':live.USDC}})
        with self.assertRaises(live.TradeError):live.route_mints(q)

    def test_checked_lookup_compresses_the_message_and_keeps_exact_keys(self):
        from solders.hash import Hash
        from solders.message import MessageV0
        from solders.pubkey import Pubkey
        key=public_key(bytes([7])*32)
        addresses=[live.associated(MINT),live.associated(live.SOL),MINT]
        tables=live.checked_lookups([key],lookup_result(addresses))
        instructions=live.checked_instructions(payload(),quote(),1)
        plain=MessageV0.try_compile(Pubkey.from_string(live.OWNER),instructions,[],Hash.default())
        compressed=MessageV0.try_compile(Pubkey.from_string(live.OWNER),instructions,tables,Hash.default())
        self.assertEqual(len(compressed.address_table_lookups),1)
        self.assertLess(len(bytes(compressed)),len(bytes(plain)))
        self.assertEqual(list(tables[0].addresses),list(map(Pubkey.from_string,addresses)))

    def test_lookup_owner_deactivation_and_recent_extension_are_checked(self):
        key=public_key(bytes([7])*32)
        result=lookup_result([MINT]);result['value'][0]['owner']=live.TOKEN
        invalid=[result,lookup_result([MINT],deactivation=9),lookup_result([MINT])]
        invalid[-1]['context']['slot']=1
        for data in invalid:
            with self.subTest(data=data),self.assertRaises(live.TradeError):live.checked_lookups([key],data)
    def test_comparison_reports_statuses_without_exposing_saved_key(self):
        transport=live.Transport('https://unused.invalid','PRIVATE_SAVED_KEY')
        output=StringIO()
        with patch.object(transport,'quote_probe',side_effect=[{'status':200,'valid':True},{'status':401,'valid':False}]) as probe,patch('sys.stdout',output):
            transport.compare_quotes()
        self.assertEqual([call.args[0] for call in probe.call_args_list],['public','invalid'])
        self.assertIn('SANS CLE : HTTP 200',output.getvalue());self.assertIn('CLE FACTICE : HTTP 401',output.getvalue())
        self.assertNotIn('PRIVATE_SAVED_KEY',output.getvalue())

    def test_probe_only_gets_fixed_quote_without_saved_key(self):
        transport=live.Transport('https://unused.invalid','PRIVATE_SAVED_KEY')
        response=Mock(status_code=200);response.json.return_value=quote(live.SOL,live.USDC,1000000)
        with patch.object(transport.jupiter_http,'request',return_value=response) as client,patch.object(live.time,'sleep'):
            self.assertTrue(transport.quote_probe('public')['valid'])
            transport.quote_probe('invalid')
        for call in client.call_args_list:
            self.assertEqual(call.args[0],'GET');self.assertEqual(call.args[1],'https://api.jup.ag'+live.quote_path(live.SOL,live.USDC,1000000))
            self.assertNotIn('PRIVATE_SAVED_KEY',str(call));self.assertNotIn('json',call.kwargs)
        self.assertNotIn('x-api-key',client.call_args_list[0].kwargs['headers'])
        with self.assertRaises(live.TradeError):transport.quote_probe('sendTransaction')
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

    def test_quote_parameters_allow_intermediates_and_keep_slippage_and_v1(self):
        from urllib.parse import parse_qs,urlsplit
        values=parse_qs(urlsplit(live.quote_path(live.SOL,MINT,1000)).query)
        self.assertEqual(values['onlyDirectRoutes'],['false']);self.assertEqual(values['asLegacyTransaction'],['false'])
        self.assertEqual(values['restrictIntermediateTokens'],['true']);self.assertEqual(values['instructionVersion'],['V1'])
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
        self.e.blockhash_cache=None;self.e.blockhash_lock=asyncio.Lock()

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

    async def test_blockhash_cache_avoids_read_and_expiry_forces_refresh(self):
        from solders.hash import Hash
        self.e.rpc.return_value={'value':{'blockhash':str(Hash.default()),'lastValidBlockHeight':100}}
        first=await self.e.blockhash();second=await self.e.blockhash()
        self.assertEqual(first,second);self.e.rpc.assert_awaited_once()
        self.e.blockhash_cache['_observed_mono']=time.monotonic()-11
        await self.e.blockhash();self.assertEqual(self.e.rpc.await_count,2)

    async def test_concurrent_blockhash_requests_share_one_read(self):
        from solders.hash import Hash
        async def read(*args):
            await asyncio.sleep(.001)
            return {'value':{'blockhash':str(Hash.default()),'lastValidBlockHeight':100}}
        self.e.rpc.side_effect=read
        await asyncio.gather(self.e.blockhash(),self.e.blockhash())
        self.e.rpc.assert_awaited_once()

    async def test_send_requires_durable_signature_and_records_waits_after_http(self):
        data={'buy':{'raw':'fake','signature':'fake','height':100},'decision_wall':time.time()}
        with self.assertRaises(live.TradeError):await self.e.send(MINT,data,'buy')
        self.e.transport.send.assert_not_called()
        self.e.ledger.reserve(MINT,data)
        with patch.object(self.e.ledger,'save',wraps=self.e.ledger.save) as save:
            def submit(raw,deadline,capture):
                self.assertEqual(save.call_count,0)
                capture.update(request_at=time.time(),queue_wait_ms=4,quota_wait_ms=5,http_response_ms=7)
                return 'fake'
            self.e.transport.send.side_effect=submit
            await self.e.send(MINT,data,'buy')
            self.assertEqual(save.call_count,1)
        detail=json.loads(self.e.ledger.get(MINT)['data'])['latency_breakdown']
        self.assertEqual(detail['file_rpc_envoi_ms'],4);self.assertEqual(detail['quota_rpc_envoi_ms'],5)
        self.assertEqual(detail['reponse_http_apres_envoi_ms'],7)

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

    async def test_time_limit_sells_entire_position_without_sl_or_tp(self):
        confirmed=time.time()-61
        data={'quantity':42,'entry_usd':'1','sell_attempts':0,'confirmed_at':confirmed,'exit_deadline':confirmed+60}
        self.e.ledger.save(MINT,'open',data,reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=42);self.e.quote=AsyncMock(return_value={'outAmount':'10000000'})
        self.e.prepare=AsyncMock(return_value={'fake':'unsigned'});self.e.signed=Mock(return_value={'signature':'fake','raw':'fake','height':100})
        self.e.send=AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):await self.e.positions()
        row=self.e.ledger.get(MINT);saved=json.loads(row['data'])
        self.assertEqual(row['state'],'sell_pending');self.assertEqual(saved['exit_reason'],'TIMEOUT_60S')
        self.assertEqual(saved['exit_deadline'],confirmed+60)
        self.e.prepare.assert_awaited_once_with(MINT,'sell',42,{'outAmount':'10000000'})
        self.assertEqual(Decimal(row['reserved']),live.ENVELOPE)

    async def test_time_limit_never_sells_early_and_old_positions_use_original_confirmation(self):
        confirmed=time.time()-59
        data={'quantity':42,'entry_usd':'1','sell_attempts':0,'confirmed_at':confirmed}
        self.e.ledger.save(MINT,'open',data,reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=42);self.e.quote=AsyncMock(return_value={'outAmount':'10000000'})
        self.e.prepare=AsyncMock()
        task=asyncio.create_task(self.e.positions());await asyncio.sleep(.01);task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.e.prepare.assert_not_awaited();self.assertEqual(self.e.ledger.get(MINT)['state'],'open')
        self.assertEqual(live.exit_deadline(data),confirmed+60)
        self.assertIsNone(live.exit_deadline({'confirmed_at':float('nan')}))
        self.assertIsNone(live.exit_deadline({'confirmed_at':True}))

    async def test_timeout_decision_survives_quote_failure_and_restart(self):
        confirmed=time.time()-61
        self.e.ledger.save(MINT,'open',{'quantity':42,'entry_usd':'1','sell_attempts':0,'confirmed_at':confirmed},reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=42);self.e.quote=AsyncMock(side_effect=live.TradeError('NO_ROUTES_FOUND'))
        task=asyncio.create_task(self.e.positions());await asyncio.sleep(.01);task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.e.transport.send.assert_not_called()
        other=live.Ledger(Path(self.tmp.name)/'live.sqlite3')
        try:
            row=other.get(MINT);data=json.loads(row['data'])
            self.assertEqual(row['state'],'open');self.assertEqual(data['exit_reason'],'TIMEOUT_60S')
            self.assertEqual(live.exit_deadline(data),confirmed+60);self.assertEqual(data['sell_attempts'],0)
        finally:other.db.close()

    async def test_confirmation_sets_absolute_deadline_without_resetting_on_failed_sell(self):
        self.e.ledger.reserve(MINT,{'buy':{'signature':'fake','height':100},'sell_attempts':0})
        self.e.rpc.side_effect=[{'value':[{'confirmationStatus':'confirmed'}]}, {'meta':{'err':None,'postTokenBalances':[
            {'owner':live.OWNER,'mint':MINT,'uiTokenAmount':{'amount':'42'}}]}}]
        await self.e.reconcile(self.e.ledger.get(MINT));data=json.loads(self.e.ledger.get(MINT)['data'])
        self.assertEqual(data['exit_deadline'],data['confirmed_at']+60)
        data['sell_attempts']=1;data['sell']={'signature':'failed','height':100};data['exit_reason']='TIMEOUT_60S'
        self.e.ledger.save(MINT,'sell_pending',data)
        self.e.rpc.side_effect=[{'value':[{'confirmationStatus':'confirmed'}]}, {'meta':{'err':{'InstructionError':[0,'Custom']}}}]
        await self.e.reconcile(self.e.ledger.get(MINT));saved=json.loads(self.e.ledger.get(MINT)['data'])
        self.assertEqual(self.e.ledger.get(MINT)['state'],'open');self.assertEqual(saved['exit_deadline'],data['exit_deadline'])
        self.assertEqual(saved['exit_reason'],'TIMEOUT_60S')

    async def test_time_limit_crossed_during_quote_is_handled_in_same_iteration(self):
        clock=[100000.0]
        self.e.ledger.save(MINT,'open',{'quantity':42,'entry_usd':'1','sell_attempts':0,'confirmed_at':clock[0]-59.9},reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=42);self.e.price=Mock(return_value=Decimal(100))
        async def quote(*args):clock[0]+=.2;return {'outAmount':'10000000'}
        self.e.quote=AsyncMock(side_effect=quote);self.e.prepare=AsyncMock(return_value={'fake':'unsigned'})
        self.e.signed=Mock(return_value={'signature':'fake','raw':'fake','height':100});self.e.send=AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(live.time,'time',side_effect=lambda:clock[0]):
            with self.assertRaises(asyncio.CancelledError):await self.e.positions()
        self.assertEqual(json.loads(self.e.ledger.get(MINT)['data'])['exit_reason'],'TIMEOUT_60S')

    async def test_elapsed_timer_never_creates_second_sell_while_signature_is_pending(self):
        data={'quantity':42,'confirmed_at':time.time()-100,'sell':{'signature':'pending'},'sell_attempts':1}
        self.e.ledger.save(MINT,'sell_pending',data,reserve=live.ENVELOPE)
        self.e.reconcile=AsyncMock(side_effect=asyncio.CancelledError());self.e.prepare=AsyncMock()
        with self.assertRaises(asyncio.CancelledError):await self.e.positions()
        self.e.prepare.assert_not_awaited();self.e.transport.send.assert_not_called()
        self.assertEqual(self.e.ledger.get(MINT)['state'],'sell_pending')

    async def test_manual_balance_change_blocks_new_orders(self):
        data={'quantity':42,'entry_usd':'1','sell_attempts':0}
        self.e.ledger.save(MINT,'open',data,reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=41)
        task=asyncio.create_task(self.e.positions());await asyncio.sleep(.01)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.e.ledger.get(MINT)['state'],'manual')
        self.e.jup.assert_not_awaited();self.e.transport.send.assert_not_called()

    async def test_exit_failure_names_its_stage_without_claiming_sale(self):
        self.e.ledger.save(MINT,'open',{'quantity':42,'entry_usd':'1'},reserve=live.ENVELOPE)
        self.e.holdings=AsyncMock(return_value=42)
        self.e.quote=AsyncMock(side_effect=live.TradeError('Jupiter/cotation : NO_ROUTES_FOUND'))
        task=asyncio.create_task(self.e.positions());await asyncio.sleep(.01)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        notice=json.loads(self.e.ledger.db.execute("SELECT data FROM notices WHERE code='SORTIE_INDISPONIBLE'").fetchone()[0])
        self.assertEqual(notice['stage'],'cotation de vente Jupiter')
        self.assertIn('NO_ROUTES_FOUND',notice['message']);self.assertEqual(self.e.ledger.get(MINT)['state'],'open')
        self.e.transport.send.assert_not_called()

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
                if method=='getMultipleAccounts':return {'context':{'slot':10},'value':[{'owner':live.SYSTEM,'lamports':100000000} if address==live.OWNER else None for address in params[0]]}
                if method=='simulateTransaction':
                    self.assertEqual(params[1]['minContextSlot'],10)
                    return {'value':{'err':None,'accounts':[{'owner':live.SYSTEM,'lamports':90000000},token_snapshot(100000)]}}
                raise AssertionError('Unexpected method: '+method)
            self.e.rpc.side_effect=rpc
            prepared=await self.e.prepare(MINT,'buy')
            self.assertIn('simulation',prepared['preparation_profile']['phases_ms'])
            self.e.transport.send.assert_not_called()
            methods=[call.args[0] for call in self.e.rpc.call_args_list]
            self.assertNotIn('getBalance',methods);self.assertEqual(methods.count('getMultipleAccounts'),1)
            self.assertTrue(prepared['preparation_profile']['anticipated_accounts'])
            self.e.prices.usd=Decimal(200)
            with self.assertRaises(live.TradeError):self.e.signed(prepared)
            self.e.prices.usd=Decimal(100)
            signed=self.e.signed(prepared)
            prepared['blockhash_at']=time.monotonic()-16
            with self.assertRaises(live.TradeError):self.e.signed(prepared)
            tx=VersionedTransaction.from_bytes(base64.b64decode(signed['raw']))
            self.assertEqual(tx.verify_with_results(),[True])
            self.assertEqual(str(tx.message.account_keys[0]),str(key.pubkey()))
            self.assertEqual(self.e.ledger.used(),0)

    def configure_buy_preparation(self,received=100000,preexisting=0):
        from solders.hash import Hash
        self.e.holdings=AsyncMock(return_value=0);self.e.jup.side_effect=[quote(),payload()]
        async def rpc(method,params):
            if method=='getLatestBlockhash':return {'value':{'blockhash':str(Hash.default()),'lastValidBlockHeight':100}}
            if method=='getFeeForMessage':return {'value':5000}
            if method=='getMultipleAccounts':
                return {'context':{'slot':20},'value':[{'owner':live.SYSTEM,'lamports':100000000} if address==live.OWNER else
                    token_snapshot(preexisting) if preexisting and address==live.associated(MINT) else None for address in params[0]]}
            if method=='simulateTransaction':
                return {'value':{'err':None,'accounts':[{'owner':live.SYSTEM,'lamports':90000000},token_snapshot(received)]}}
            raise AssertionError('Unexpected RPC: '+method)
        self.e.rpc.side_effect=rpc

    async def test_simulation_rejects_over_five_percent_quote_deterioration_without_sign_or_budget(self):
        self.configure_buy_preparation(received=94999)
        with patch.object(self.e,'signed') as sign:
            with self.assertRaisesRegex(live.TradeError,'Prix degrade'):await self.e.prepare(MINT,'buy')
            sign.assert_not_called()
        self.assertEqual(self.e.ledger.used(),0);self.e.transport.send.assert_not_called()
        self.configure_buy_preparation(received=95000)
        prepared=await self.e.prepare(MINT,'buy')
        self.assertEqual(prepared['entry_guard']['simulated_tokens'],95000)
        self.assertEqual(prepared['entry_guard']['minimum_simulated_tokens'],95000)
        self.assertEqual(live.SLIPPAGE,2000)

    async def test_appearing_position_is_rejected_from_anticipated_accounts(self):
        self.configure_buy_preparation(preexisting=1)
        with self.assertRaisesRegex(live.TradeError,'Position apparue'):await self.e.prepare(MINT,'buy')
        self.assertNotIn('simulateTransaction',[call.args[0] for call in self.e.rpc.call_args_list])
        self.assertEqual(self.e.ledger.used(),0)

    async def test_stale_quote_or_simulation_is_rejected_before_private_signer_is_used(self):
        self.configure_buy_preparation();prepared=await self.e.prepare(MINT,'buy')
        for field,age in (('quote_at',2.01),('checked_at',.51)):
            with self.subTest(field=field):
                p=dict(prepared,entry_guard=dict(prepared['entry_guard']))
                p['entry_guard'][field]=time.monotonic()-age
                with self.assertRaisesRegex(live.TradeError,'Controle du prix'):self.e.signed(p)
        p=dict(prepared,entry_guard=None)
        with self.assertRaisesRegex(live.TradeError,'Controle du prix'):self.e.signed(p)
        self.e.transport.send.assert_not_called();self.assertEqual(self.e.ledger.used(),0)

    async def test_construction_and_account_reads_do_not_wait_for_holdings(self):
        self.configure_buy_preparation();built=asyncio.Event();snapshot=asyncio.Event()
        original=self.e.rpc.side_effect
        async def holdings(mint):
            await asyncio.wait_for(built.wait(),1);await asyncio.wait_for(snapshot.wait(),1);return 0
        async def jup(path,body=None):
            if body is None:return quote()
            built.set();await asyncio.wait_for(snapshot.wait(),1);return payload()
        async def rpc(method,params):
            if method=='getMultipleAccounts':snapshot.set()
            return await original(method,params)
        self.e.holdings.side_effect=holdings;self.e.jup.side_effect=jup;self.e.rpc.side_effect=rpc
        await self.e.prepare(MINT,'buy')
        methods=[call.args[0] for call in self.e.rpc.call_args_list]
        self.assertLess(methods.index('getMultipleAccounts'),methods.index('getFeeForMessage'))
        self.assertLess(methods.index('getFeeForMessage'),methods.index('simulateTransaction'))

    async def test_anticipated_balance_rent_and_network_fee_checks_remain_mandatory(self):
        for case in ('balance','rent','debit','fee','simulation'):
            with self.subTest(case=case):
                self.configure_buy_preparation();original=self.e.rpc.side_effect
                if case=='rent':
                    p=payload();p['setupInstructions'].append(dict(programId=live.ATA,data='AQ==',accounts=[
                        account(live.OWNER,True),account(live.associated(MINT)),account(live.OWNER),account(MINT),account(live.SYSTEM),account(live.TOKEN)]))
                    self.e.jup.side_effect=[quote(),p]
                async def rpc(method,params):
                    result=await original(method,params)
                    if method=='getMultipleAccounts' and case=='balance':result['value'][0]['lamports']=1
                    if method=='getFeeForMessage' and case=='fee':result['value']=1000000000
                    if method=='simulateTransaction' and case=='simulation':result['value']['err']={'InstructionError':[0,{'Custom':6001}]}
                    if method=='simulateTransaction' and case=='debit':result['value']['accounts'][0]['lamports']=1
                    if method=='simulateTransaction' and case=='rent':result['value']['accounts'][1]['lamports']=6000000
                    return result
                self.e.rpc.side_effect=rpc
                with self.assertRaises(live.TradeError):await self.e.prepare(MINT,'buy')
                self.assertEqual(self.e.ledger.used(),0);self.e.transport.send.assert_not_called()

    async def test_minimum_simulated_reception_rounds_up_and_incomplete_snapshots_fail_closed(self):
        self.configure_buy_preparation(received=95000)
        q=quote();q['outAmount']='100001';self.e.jup.side_effect=[q,payload(q)]
        with self.assertRaisesRegex(live.TradeError,'Prix degrade'):await self.e.prepare(MINT,'buy')
        self.configure_buy_preparation();original=self.e.rpc.side_effect
        async def rpc(method,params):
            result=await original(method,params)
            if method=='getMultipleAccounts':result['value'].pop()
            return result
        self.e.rpc.side_effect=rpc
        with self.assertRaisesRegex(live.TradeError,'Comptes anticipes incomplets'):await self.e.prepare(MINT,'buy')
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

    async def test_lookup_fetch_is_bounded_and_only_reads_accounts(self):
        key=public_key(bytes([7])*32)
        self.e.rpc.return_value=lookup_result([MINT])
        tables=await self.e.lookups({'addressLookupTableAddresses':[key]})
        self.assertEqual(len(tables),1)
        self.e.rpc.assert_awaited_once_with('getMultipleAccounts',[[key],{'encoding':'base64','commitment':'confirmed'}])
        with self.assertRaises(live.TradeError):await self.e.lookups({'addressLookupTableAddresses':[key]*5})


class SetupAndCheckTest(unittest.TestCase):
    def test_report_reads_old_records_and_new_latency_without_modifying_budget(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(live,'ROOT',Path(tmp)):
            path=Path(tmp)/'data/wallet_alerts_ws/live_trades.sqlite3';ledger=live.Ledger(path)
            ledger.reserve(MINT,{'buy':{'signature':'fake'},'latency_breakdown':{'preparation_restante_ms':100},
                'preparation_profile':{'phases_ms':{'simulation':10},'transport':{'RPC/test':[{'quota_wait_ms':5}]},'blockhash_cache_hit':True}})
            ledger.save('old-mint','closed',{'buy':{'signature':'old-fake'}},reserve='0')
            ledger.notice({'at':time.time(),'mint':MINT,'code':'ACHAT_IGNORE','message':'Preparation trop lente',
                           'execution_version':live.EXECUTION_VERSION,'latency_breakdown':{'preparation_restante_ms':1900}})
            ledger.db.close();before=path.stat().st_mtime_ns;output=StringIO()
            with patch.object(sys,'argv',['wallet_radar_live.py','--report']),patch('sys.stdout',output):live.main()
            self.assertEqual(path.stat().st_mtime_ns,before)
            self.assertIn('preparation_restante_ms',output.getvalue());self.assertIn('old-fake',output.getvalue())
            self.assertIn('blockhash precharge : True',output.getvalue());self.assertIn('2.40 / 45',output.getvalue())
            self.assertIn('mesures avec '+live.EXECUTION_VERSION+' : 1',output.getvalue())
    def test_update_jupiter_does_not_read_or_rewrite_wallet(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(live,'PRIVATE',Path(tmp)):
            wallet=Path(tmp)/'wallet.json';wallet.write_text('private-wallet-must-stay-untouched')
            before=(wallet.read_bytes(),wallet.stat().st_mtime_ns)
            with patch('builtins.input',return_value='NEW_FAKE_API_KEY'),patch.object(live.getpass,'getpass',side_effect=AssertionError('Wallet must not be requested')),patch.object(live,'load_signer') as signer,patch('builtins.print'):
                live.update_jupiter()
            signer.assert_not_called()
            self.assertEqual((wallet.read_bytes(),wallet.stat().st_mtime_ns),before)
            api=Path(tmp)/'jupiter_api_key.txt';self.assertEqual(api.read_text().strip(),'NEW_FAKE_API_KEY')
            self.assertEqual(api.stat().st_mode&0o777,0o600)

    def test_http400_check_compares_quotes_and_never_builds_order(self):
        transport=Mock();transport.rpc.return_value={'value':0}
        transport.jup.side_effect=live.TradeError('Jupiter/cotation HTTP 400',status=400)
        with patch.object(sys,'argv',['wallet_radar_live.py','--check']),patch.object(live,'load_signer'),patch.object(live,'secure_read',return_value='FAKE_TEST_KEY'),patch.object(live,'Transport',return_value=transport),patch('wallet_alerts_ws.settings',return_value=({}, {'BBIG BOSS':live.BBIG},'https://unused.invalid','wss://unused.invalid')),patch('builtins.print'):
            with self.assertRaises(SystemExit):live.main()
        transport.compare_quotes.assert_called_once();transport.jup.assert_called_once();transport.send.assert_not_called()

    def test_setup_reuses_existing_keys_without_prompt_or_overwrite(self):
        from solders.keypair import Keypair
        key=Keypair.from_seed(bytes([4])*32)
        with tempfile.TemporaryDirectory() as tmp,patch.object(live,'PRIVATE',Path(tmp)),patch.object(live,'OWNER',str(key.pubkey())):
            wallet=Path(tmp)/'wallet.json';wallet.write_text(json.dumps(list(bytes(key))));wallet.chmod(0o600)
            api=Path(tmp)/'jupiter_api_key.txt';api.write_text('FAKE_TEST_KEY');api.chmod(0o600)
            before=[(p.read_bytes(),p.stat().st_mtime_ns) for p in (wallet,api)]
            with patch('builtins.input',side_effect=AssertionError('No API prompt allowed')),patch.object(live.getpass,'getpass',side_effect=AssertionError('No prompt allowed')),patch('builtins.print'):
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


class ExitDiagnosticTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_exit_diagnostic_never_signs_sends_or_modifies_journal(self):
        from solders.hash import Hash
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'live.sqlite3';ledger=live.Ledger(path)
            ledger.save(MINT,'open',{'quantity':42,'entry_usd':'1','exit_reason':'SL'},reserve=live.ENVELOPE)
            ledger.db.close();before=path.read_bytes();stamp=path.stat().st_mtime_ns
            transport=Mock();q=quote(MINT,live.SOL,42);transport.jup.side_effect=[q,payload(q)]
            def rpc(method,params,capture):
                if method=='getTokenAccountsByOwner':return {'value':[{'account':{'data':{'parsed':{'info':{'tokenAmount':{'amount':'42'}}}}}}]}
                if method=='getRecentPrioritizationFees':return [{'prioritizationFee':1}]
                if method=='getLatestBlockhash':return {'value':{'blockhash':str(Hash.default()),'lastValidBlockHeight':100}}
                if method=='getFeeForMessage':return {'value':5000}
                if method=='getBalance':return {'value':500000000}
                if method=='simulateTransaction':return {'value':{'err':None,'accounts':[{'owner':live.SYSTEM,'lamports':500000100}]}}
                raise AssertionError('Forbidden RPC: '+method)
            transport.rpc.side_effect=rpc;output=StringIO()
            price=StringIO(json.dumps({'solana':{'usd':100,'last_updated_at':time.time()}}))
            with patch.object(live,'ExitReadOnlyTransport',return_value=transport),patch.object(live,'secure_read',return_value='FAKE_TEST_KEY'),patch.object(live,'load_signer',side_effect=AssertionError('No wallet key read')),patch.object(live.Executor,'signed',side_effect=AssertionError('No signing')),patch.object(live,'Ledger',side_effect=AssertionError('No journal mutation')),patch('urllib.request.urlopen',return_value=price),patch('sys.stdout',output):
                await live.diagnose_exit(path,'https://unused.invalid')
            transport.send.assert_not_called();self.assertIn('Preparation et simulation : OK',output.getvalue())
            self.assertNotIn('FAKE_TEST_KEY',output.getvalue());self.assertEqual(path.read_bytes(),before)
            self.assertEqual(path.stat().st_mtime_ns,stamp)
            for call in transport.rpc.call_args_list:self.assertIn(call.args[0],live.ExitReadOnlyTransport.READ_METHODS if isinstance(live.ExitReadOnlyTransport,type) else [])

    async def test_unavailable_quote_is_explained_without_later_price_or_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'live.sqlite3';ledger=live.Ledger(path)
            ledger.save(MINT,'open',{'quantity':42,'entry_usd':'1'},reserve=live.ENVELOPE);ledger.db.close()
            before=path.read_bytes();transport=Mock()
            transport.rpc.return_value={'value':[{'account':{'data':{'parsed':{'info':{'tokenAmount':{'amount':'42'}}}}}}]}
            transport.jup.side_effect=live.TradeError('Jupiter/cotation : NO_ROUTES_FOUND')
            output=StringIO()
            with patch.object(live,'ExitReadOnlyTransport',return_value=transport),patch.object(live,'secure_read',return_value='FAKE'),patch('sys.stdout',output),patch('urllib.request.urlopen') as price:
                await live.diagnose_exit(path,'https://unused.invalid')
            self.assertIn('BLOCAGE : cotation de vente Jupiter',output.getvalue());self.assertIn('NO_ROUTES_FOUND',output.getvalue())
            price.assert_not_called();transport.send.assert_not_called();self.assertEqual(path.read_bytes(),before)

    async def test_read_only_transport_rejects_mutations_and_reports_safe_simulation_code(self):
        transport=live.ExitReadOnlyTransport('https://unused.invalid','FAKE')
        with patch.object(transport,'request') as network:
            with self.assertRaises(live.TradeError):transport.rpc('sendTransaction',[])
            with self.assertRaises(live.TradeError):transport.send('fake')
            with self.assertRaises(live.TradeError):transport.jup('/swap/v1/swap',{})
            network.assert_not_called()
        self.assertEqual(transport.rpc_interval,1)
        self.assertIn('6001',str(live.simulation_error({'InstructionError':[2,{'Custom':6001}]})))
        self.assertNotIn('PRIVATE',str(live.simulation_error({'InstructionError':[2,'PRIVATE_SECRET']})))


class ExternalSaleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from solders.signature import Signature
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'live.sqlite3'
        self.buy=str(Signature.from_bytes(bytes([1])*64));self.sell=str(Signature.from_bytes(bytes([2])*64))
        self.data={'quantity':42,'entry_usd':'1','buy':{'signature':self.buy,'raw':'preserved'},
                   'sell':{'signature':'earlier_failed_attempt'},'sell_attempts':1}
        ledger=live.Ledger(self.path);ledger.save(MINT,'manual',self.data,reserve=live.ENVELOPE);ledger.db.close()
        self.buy_tx=self.tx('buy',self.buy,10);self.sell_tx=self.tx('sell',self.sell,20)
        self.history=[{'signature':self.sell,'slot':20,'err':None}]
        self.holdings=[0,0];self.transport=Mock();self.transport.rpc.side_effect=self.rpc

    def tx(self,side,signature,slot):
        before,after=(0,42) if side=='buy' else (42,0)
        token=live.associated(MINT);pool=public_key(bytes([7])*32)
        balance=lambda amount:dict(accountIndex=1,mint=MINT,owner=live.OWNER,uiTokenAmount={'amount':str(amount),'decimals':6})
        source,dest=(pool,token) if side=='buy' else (token,pool)
        return {'slot':slot,'blockTime':1000+slot,'transaction':{'signatures':[signature],'message':{
            'accountKeys':[{'pubkey':live.OWNER,'signer':True},{'pubkey':token,'signer':False}],
            'instructions':[{'programId':live.JUP}]}},'meta':{'err':None,'fee':5000,
            'preTokenBalances':[balance(before)],'postTokenBalances':[balance(after)],
            'preBalances':[1000000000,2000000],'postBalances':[900000000 if side=='buy' else 1100000000,2000000],
            'logMessages':['Program log: Instruction: Route'],'innerInstructions':[{'index':0,'instructions':[
                {'programId':live.TOKEN,'parsed':{'type':'transfer','info':{'source':source,'destination':dest,'amount':'42'}}}]}]}}

    def rpc(self,method,params,capture=None):
        if method=='getTokenAccountsByOwner':
            amount=self.holdings.pop(0)
            return {'value':[{'account':{'data':{'parsed':{'info':{'tokenAmount':{'amount':str(amount)}}}}}}]}
        if method=='getTransaction':
            self.assertEqual(params[1]['commitment'],'finalized')
            return self.buy_tx if params[0]==self.buy else self.sell_tx
        if method=='getSignaturesForAddress':
            self.assertEqual(params,[live.OWNER,{'limit':20,'commitment':'finalized','until':self.buy}])
            return self.history
        raise AssertionError('Forbidden RPC: '+method)

    def row(self):
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            return dict(db.execute('SELECT * FROM trades').fetchone())

    async def run_probe(self):
        with patch.object(live,'ExitReadOnlyTransport',return_value=self.transport),\
             patch.object(live,'load_signer',side_effect=AssertionError('No wallet key')),\
             patch.object(live,'secure_read',side_effect=AssertionError('No API key')),\
             patch.object(live.Executor,'signed',side_effect=AssertionError('No signature')),\
             patch.object(live,'Ledger',side_effect=AssertionError('No ledger initialization')),patch('sys.stdout',StringIO()):
            await live.reconcile_external_sale(self.path,'https://unused.invalid')
        self.transport.send.assert_not_called();self.transport.jup.assert_not_called()

    async def test_finalized_full_sale_closes_only_local_state_preserves_budget_and_packets(self):
        await self.run_probe()
        row=self.row();self.assertEqual(row['state'],'closed');self.assertEqual(Decimal(row['reserved']),live.ENVELOPE)
        data=json.loads(row['data'])
        for field in ('quantity','entry_usd','buy','sell','sell_attempts'):self.assertEqual(data[field],self.data[field])
        self.assertEqual(data['external_sale']['signature'],self.sell);self.assertEqual(data['exit_reason'],'MANUAL_EXTERNAL')
        # Closed mint remains protected from another buy; the reserve remains used.
        ledger=live.Ledger(self.path);self.addCleanup(ledger.db.close)
        self.assertEqual(ledger.used(),live.ENVELOPE);self.assertFalse(ledger.active())
        with self.assertRaises(live.TradeError):ledger.reserve(MINT,{})

    async def test_no_proof_never_changes_database(self):
        import copy
        original=copy.deepcopy(self.sell_tx)
        for case in ('transfer','failed','old','partial','wrong_owner','wrong_signature','no_sol','unavailable','wrong_initial_buy'):
            with self.subTest(case=case):
                self.sell_tx=copy.deepcopy(original);self.holdings=[0,0];self.buy_tx=self.tx('buy',self.buy,10)
                if case=='transfer':self.sell_tx['meta']['innerInstructions']=[]
                if case=='failed':self.sell_tx['meta']['err']={'InstructionError':[0,'invalid']}
                if case=='old':self.sell_tx['slot']=9
                if case=='partial':self.sell_tx['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='1'
                if case=='wrong_owner':self.sell_tx['transaction']['message']['accountKeys'][0]['signer']=False
                if case=='wrong_signature':self.sell_tx['transaction']['signatures']=[self.buy]
                if case=='no_sol':self.sell_tx['meta']['postBalances'][0]=999995000
                if case=='unavailable':self.sell_tx=None
                if case=='wrong_initial_buy':self.buy_tx['meta']['postTokenBalances'][0]['uiTokenAmount']['amount']='41'
                before=self.path.read_bytes();stamp=self.path.stat().st_mtime_ns
                with self.assertRaises(live.TradeError):await self.run_probe()
                self.assertEqual(self.path.read_bytes(),before);self.assertEqual(self.path.stat().st_mtime_ns,stamp)

    async def test_nonzero_or_reappearing_balance_blocks_reconciliation(self):
        for holdings in ([1],[0,1]):
            self.holdings=list(holdings);before=self.path.read_bytes()
            with self.assertRaises(live.TradeError):await self.run_probe()
            self.assertEqual(self.path.read_bytes(),before)

    async def test_concurrent_ledger_change_is_not_overwritten(self):
        original=self.rpc
        def rpc(method,params,capture=None):
            value=original(method,params,capture)
            if method=='getTokenAccountsByOwner' and not self.holdings:
                import sqlite3
                with sqlite3.connect(self.path) as db:db.execute("UPDATE trades SET data=?",(json.dumps(dict(self.data,external_note='changed')),))
            return value
        self.transport.rpc.side_effect=rpc
        with self.assertRaises(live.TradeError):await self.run_probe()
        row=self.row();self.assertEqual(row['state'],'manual');self.assertIn('external_note',json.loads(row['data']))

    async def test_search_is_bounded_and_open_or_pending_rows_are_never_closed(self):
        self.history=self.history*30;self.sell_tx=None
        with self.assertRaises(live.TradeError):await self.run_probe()
        self.assertEqual(sum(call.args[0]=='getTransaction' for call in self.transport.rpc.call_args_list),9)
        import sqlite3
        for state in ('open','buy_pending','sell_pending'):
            with sqlite3.connect(self.path) as db:db.execute('UPDATE trades SET state=?',(state,))
            self.transport.rpc.reset_mock()
            with self.assertRaises(live.TradeError):await self.run_probe()
            self.transport.rpc.assert_not_called();self.assertEqual(self.row()['state'],state)


if __name__=='__main__':unittest.main()
