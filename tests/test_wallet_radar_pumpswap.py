"""PumpSwap ABI and live observation tests. Public fixtures; no key or order API."""
import asyncio
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import AsyncMock,patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wallet_radar_pumpswap as swap
import wallet_radar_paper as paper
import wallet_alerts_ws as watcher
from discord_token_analysis import parse_trade,RemoteError

FIX=Path(__file__).parent/'fixtures'
BUY=json.loads((FIX/'pumpswap_bbig_buy_v2.json').read_text())
SELL=json.loads((FIX/'pumpswap_bbig_sell_v2.json').read_text())
POOL_RESULT=json.loads((FIX/'pumpswap_pool.json').read_text())
POOL='8a1mP1CTXjsMvrpkja6izVTjoVgTHJU5QAaSXNZ7jzd7'
MINT='3Yq3ucTYekHXz9KS6qWsfygBh18sTjDaGMvMv5pkpump'


def native(tx,mono=None):
    return paper.native_trades({'signature':tx['transaction']['signatures'][0],'err':tx['meta']['err'],'logs':tx['meta']['logMessages']},tx['slot'],time.time(),time.monotonic() if mono is None else mono)


def snapshot():
    keys=BUY['transaction']['message']['accountKeys'];values=[]
    for b in BUY['meta']['postTokenBalances']:
        if b.get('owner')==paper.BBIG and b['mint']==MINT:
            address=keys[b['accountIndex']];address=address.get('pubkey') if isinstance(address,dict) else address
            values.append({'pubkey':address,'account':{'data':{'parsed':{'info':{'owner':paper.BBIG,'mint':MINT,'tokenAmount':b['uiTokenAmount']}}}}})
    return {'context':{'slot':BUY['slot']},'value':values}


class SwapAbiTest(unittest.TestCase):
    def test_schemas_discriminators_match_official_sdk_2_2_0(self):
        idl=json.loads((FIX/'pumpswap_sdk_2_2_0.json').read_text())
        self.assertEqual(idl['address'],swap.PROGRAM)
        for name,fields in [('BuyEvent',swap.BUY_FIELDS),('SellEvent',swap.SELL_FIELDS),('Pool',swap.POOL_FIELDS)]:
            actual=next(t for t in idl['types'] if t['name']==name)['type']['fields']
            self.assertEqual(fields,[(f['name'],f['type']) for f in actual])
        self.assertEqual(swap.BUY_DISC,bytes(next(t for t in idl['events'] if t['name']=='BuyEvent')['discriminator']))
        self.assertEqual(swap.SELL_DISC,bytes(next(t for t in idl['events'] if t['name']=='SellEvent')['discriminator']))

    def test_actual_bbig_buy_and_sell_match_owned_token_deltas(self):
        metadata=swap.decode_pool(POOL_RESULT,POOL)
        for tx,buy in [(BUY,True),(SELL,False)]:
            events=native(tx);self.assertEqual(len(events),1)
            event=swap.normalize(events[0],metadata)
            trade=parse_trade(tx,paper.BBIG,MINT,tx['transaction']['signatures'][0])
            self.assertEqual(event['mint'],MINT);self.assertEqual(event['user'],paper.BBIG)
            self.assertEqual(event['is_buy'],buy);self.assertEqual(event['token_amount'],trade['raw'])
            self.assertEqual(event['unparsed_tail_bytes'],0);self.assertEqual(event['quote_mint'],paper.SOL)
            self.assertGreater(event['quote_amount'],0)
        self.assertEqual(metadata['creator'],'E1Kx9xDr29LWvNkoUdoF9Z5fEZEdkTo55wBmS3MZDd6G')
        self.assertEqual(metadata['coin_creator'],'H6zpaY14WgkEmB3MJTvC7Wb5PQJkBVLd4KwLD2ktVgop')

    def test_failed_or_forged_program_logs_never_become_swaps(self):
        tx=copy.deepcopy(BUY);tx['meta']['err']={'InstructionError':[0,'Custom']};self.assertEqual(native(tx),[])
        tx=copy.deepcopy(BUY);tx['meta']['logMessages']=[line.replace(swap.PROGRAM,paper.BBIG) for line in tx['meta']['logMessages']]
        self.assertEqual(native(tx),[])
        raw=next(line for line in BUY['meta']['logMessages'] if line.startswith('Program data:'))
        value={'signature':'C'*88,'err':None,'logs':[f'Program {swap.PROGRAM} invoke [1]',f'Program {paper.BBIG} invoke [2]',raw,f'Program {paper.BBIG} success',f'Program {swap.PROGRAM} success']}
        self.assertEqual(paper.native_trades(value,1,time.time(),time.monotonic()),[])

    def test_unknown_event_suffix_is_not_silently_accepted_and_short_event_fails(self):
        raw=base64.b64decode(next(line[14:] for line in BUY['meta']['logMessages'] if line.startswith('Program data:')))
        self.assertEqual(swap.decode_trade(raw+b'12345678')['unparsed_tail_bytes'],8)
        with self.assertRaises(ValueError):swap.decode_trade(raw[:100])
        # Previous published IDL omitted only this final u64.
        self.assertEqual(swap.decode_trade(raw[:-8])['unparsed_tail_bytes'],0)

    def test_pool_owner_layout_padding_and_creator_must_match(self):
        for case in ('owner','discriminator','extension','encoding','truncated'):
            with self.subTest(case=case):
                result=copy.deepcopy(POOL_RESULT)
                if case=='owner':result['value']['owner']=paper.PUMP
                elif case=='encoding':result['value']['data'][1]='base58'
                else:
                    raw=bytearray(base64.b64decode(result['value']['data'][0]))
                    if case=='discriminator':raw[0]^=1
                    if case=='extension':raw[-1]=1
                    if case=='truncated':raw=raw[:100]
                    result['value']['data'][0]=base64.b64encode(raw).decode()
                with self.assertRaises(ValueError):swap.decode_pool(result,POOL)
        pool=swap.decode_pool(POOL_RESULT,POOL);pool['coin_creator']=paper.BWAM
        with self.assertRaises(ValueError):swap.normalize(native(BUY)[0],pool)

    def test_pool_rpc_is_read_only_and_errors_do_not_expose_provider_data(self):
        rpc=watcher.MeasuredRPC('https://unused.invalid/PRIVATE',None,rps=10)
        response=Mock(status_code=200);response.json.return_value={'result':POOL_RESULT}
        rpc.http.post=Mock(return_value=response)
        self.assertEqual(rpc.call('getAccountInfo',[POOL,{'encoding':'base64','commitment':'confirmed'}]),POOL_RESULT)
        self.assertEqual(rpc.http.post.call_args.kwargs['json']['method'],'getAccountInfo')
        with self.assertRaises(ValueError):rpc.call('sendTransaction',['fake'])
        self.assertEqual(rpc.http.post.call_count,1)
        response.json.return_value={'error':{'code':429,'message':'PRIVATE KEY BODY'}}
        with self.assertRaises(RemoteError) as error:rpc.call('getAccountInfo',[POOL,{'encoding':'base64','commitment':'confirmed'}])
        self.assertEqual(error.exception.rpc_code,429);self.assertNotIn('PRIVATE',str(error.exception))

    def test_diagnostic_reads_pool_and_names_pumpswap_without_mutating_session(self):
        import wallet_radar_diagnostics as diagnostic
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'paper.jsonl'
            path.write_text(json.dumps({'type':'paper_session','session_id':'test','limit':30})+'\n'+json.dumps({
                'type':'paper_decision','session_id':'test','code':'NON_SUPPORTE','mint':MINT,'initial_signature':BUY['transaction']['signatures'][0]})+'\n')
            before=path.read_bytes();rpc=Mock();rpc.call.side_effect=[snapshot(),BUY,POOL_RESULT];output=StringIO()
            with redirect_stdout(output):diagnostic.diagnose(path,Path(tmp)/'no-state.sqlite3',rpc)
            self.assertIn('EVENEMENTS PUMPSWAP : 1',output.getvalue());self.assertIn('COTATION : SOL',output.getvalue())
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual([call.args[0] for call in rpc.call.call_args_list],['getTokenAccountsByOwner','getTransaction','getAccountInfo'])


class SwapObserverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.observer=paper.PaperObserver('wss://unused.invalid',Path(self.temp.name)/'paper.jsonl')
        self.observer.connected=True;self.observer.ready_wall=time.time()-10
        self.output=[];self.observer.emit=self.output.append;self.observer.decisions.emit=self.output.append
        self.observer.prices.usd=100;self.observer.prices.published=time.time()-1
        self.observer.read_rpc=AsyncMock(return_value=POOL_RESULT)

    async def asyncTearDown(self):
        tasks=list(self.observer.swap_tasks.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True);self.temp.cleanup()

    async def resolve(self):await asyncio.gather(*list(self.observer.swap_tasks.values()))

    def trade(self):
        trade=parse_trade(BUY,paper.BBIG,MINT,BUY['transaction']['signatures'][0])
        return dict(trade,source='websocket',processed_seen_at=time.time()-.2)

    def start_with_snapshot(self):
        self.observer.confirmed(self.trade(),BUY,time.monotonic(),processed_mono=time.monotonic()-.2,processed_wall=time.time()-.2,account_snapshot=snapshot())

    async def test_confirmation_before_pool_is_replayed_without_unsupported_or_timer_reset(self):
        original=time.monotonic()-.2;wall=time.time()-.2
        self.observer.confirmed(self.trade(),BUY,time.monotonic(),processed_mono=original,processed_wall=wall,account_snapshot=snapshot())
        self.assertEqual(self.output,[])
        await self.resolve();c=self.observer.decisions.candidates[MINT]
        self.assertEqual(c.t0,original);self.assertEqual(c.baseline,sum(int(a['account']['data']['parsed']['info']['tokenAmount']['amount']) for a in snapshot()['value']))
        self.assertNotIn('NON_SUPPORTE',[e['code'] for e in self.output]);self.assertEqual(c.volume,0)
        self.observer.read_rpc.assert_awaited_once_with('getAccountInfo',[POOL,{'encoding':'base64','commitment':'confirmed'}],background=False)

    async def test_pool_lookups_are_coalesced_and_cached_for_other_buyers(self):
        event=native(BUY)[0];value={'logs':BUY['meta']['logMessages']}
        self.observer.observe_native([event,event],('wallet',paper.BBIG),value)
        await self.resolve();self.assertEqual(self.observer.read_rpc.await_count,1)
        c=self.observer.decisions.candidates[MINT];c.baseline=100
        other=dict(event,user=paper.public_key(bytes([3])*32),signature='E'*88,index=0,quote_amount_in=5000000000,mono=c.t0+.5)
        self.observer.observe_native([other,other],('token',MINT),value)
        self.assertEqual(c.volume,500);self.assertEqual(self.observer.read_rpc.await_count,1)
        self.observer.decisions.tick(c.t0+1.1);self.assertEqual(c.terminal,'ACHAT_SIMULE')

    async def test_first_second_sale_waiting_for_pool_blocks_buy_until_verified(self):
        first=swap.normalize(native(BUY,time.monotonic()-1.1)[0],swap.decode_pool(POOL_RESULT,POOL))
        self.observer.decisions.observe(first)
        c=self.observer.decisions.candidates[MINT]
        c.baseline=swap.normalize(native(SELL)[0],swap.decode_pool(POOL_RESULT,POOL))['token_amount'];c.volume=500
        sale=native(SELL,c.t0+.5)[0]
        self.observer.observe_native([sale],('token',MINT),{'logs':SELL['meta']['logMessages']})
        self.observer.decisions.tick(time.monotonic());self.assertIsNone(c.terminal)
        self.assertIn(POOL,c.pending_pools)
        await self.resolve();self.assertEqual(c.terminal,'SORTIE_TOTALE_1S');self.assertFalse(c.pending_pools)

    async def test_late_buffered_volume_never_reopens_three_second_window(self):
        metadata=swap.decode_pool(POOL_RESULT,POOL);metadata['_cached_at']=time.monotonic();self.observer.swap_pools[POOL]=metadata
        first=swap.normalize(native(BUY,time.monotonic()-3.1)[0],metadata);self.observer.decisions.observe(first)
        c=self.observer.decisions.candidates[MINT];c.baseline=100
        other=dict(native(BUY,c.t0+.5)[0],signature='E'*88,user=paper.public_key(bytes([3])*32),quote_amount_in=5000000000)
        self.observer.observe_native([other],('token',MINT),{'logs':BUY['meta']['logMessages']})
        self.assertEqual(c.terminal,'VOLUME_INSUFFISANT');self.assertEqual(c.volume,0)

    async def test_failed_pool_metadata_rejects_initial_signal_without_secret(self):
        invalid=copy.deepcopy(POOL_RESULT);invalid['value']['owner']=paper.PUMP
        self.observer.read_rpc.return_value=invalid;self.start_with_snapshot();await self.resolve()
        self.assertEqual(self.output[-1]['code'],'DONNEES_INSUFFISANTES')
        self.assertIn('POOL_PUMPSWAP_NON_VERIFIE',self.output[-1]['missing_reasons'])
        self.assertNotIn(MINT,self.observer.decisions.candidates)

    async def test_bwam_creator_and_unknown_suffix_still_exclude_candidates(self):
        metadata=swap.decode_pool(POOL_RESULT,POOL);metadata['_cached_at']=time.monotonic();self.observer.swap_pools[POOL]=metadata
        metadata['coin_creator']=paper.BWAM
        event=dict(native(BUY)[0],coin_creator=paper.BWAM)
        self.observer.observe_native([event],('wallet',paper.BBIG),{'logs':BUY['meta']['logMessages']})
        self.assertEqual(self.observer.decisions.candidates[MINT].terminal,'DEV_EXCLU')

    async def test_pool_capacity_is_bounded_without_blocking_websocket(self):
        entered=asyncio.Event()
        async def read(*args,**kwargs):entered.set();await asyncio.Future()
        self.observer.read_rpc.side_effect=read
        for i in range(9):
            event=dict(native(BUY)[0],pool=paper.public_key(bytes([i+1])*32),signature=paper.public_key(bytes([i+1])*64))
            self.observer.observe_native([event],('wallet',paper.BBIG),{'logs':BUY['meta']['logMessages']})
        await asyncio.wait_for(entered.wait(),1)
        self.assertEqual(len(self.observer.swap_tasks),8);self.assertEqual(len(self.observer.swap_pending),8)
        self.assertIn('LECTURE_POOL_PUMPSWAP_INDISPONIBLE',self.observer.swap_errors.values())

    async def test_watcher_verifies_initial_position_before_pool_candidate_exists(self):
        journal=watcher.Journal(Path(self.temp.name)/'state.sqlite3')
        try:
            service=watcher.Service(journal,{'BBIG BOSS':paper.BBIG},Mock(interval=.001),'wss://unused.invalid',paper=self.observer,live_only=True)
            self.assertEqual(self.observer.read_rpc,service.call)
            async def call(method,params,**kwargs):
                if method=='getTransaction':return BUY
                if method=='getTokenAccountsByOwner':return snapshot()
                if method=='getAccountInfo':return POOL_RESULT
                raise AssertionError(method)
            service.call=AsyncMock(side_effect=call);self.observer.read_rpc=service.call
            signature=BUY['transaction']['signatures'][0];now=time.time();mono=time.monotonic()
            journal.observe(paper.BBIG,signature,now,BUY['slot'],'websocket',False,stage='processed',monotonic=mono)
            journal.observe(paper.BBIG,signature,now,BUY['slot'],'websocket',True,stage='confirmed',monotonic=mono)
            with redirect_stdout(StringIO()):
                self.assertTrue(await service.decode_one(now+.01));await asyncio.gather(*list(service.paper_checks));await self.resolve()
            self.assertGreater(self.observer.decisions.candidates[MINT].baseline,0)
            self.assertEqual([c.args[0] for c in service.call.call_args_list],['getTransaction','getTokenAccountsByOwner','getAccountInfo'])
            self.assertNotIn('NON_SUPPORTE',[e['code'] for e in self.output])
        finally:journal.db.close()

    async def test_failed_or_unresolved_pool_during_first_second_cannot_allow_entry(self):
        first=swap.normalize(native(BUY,time.monotonic()-1.1)[0],swap.decode_pool(POOL_RESULT,POOL));self.observer.decisions.observe(first)
        c=self.observer.decisions.candidates[MINT];c.baseline=100;c.volume=500
        bad=copy.deepcopy(POOL_RESULT);bad['value']['owner']=paper.PUMP;self.observer.read_rpc.return_value=bad
        sale=native(SELL,c.t0+.5)[0];self.observer.observe_native([sale],('token',MINT),{'logs':SELL['meta']['logMessages']})
        await self.resolve();self.observer.decisions.tick(time.monotonic())
        self.assertTrue(c.unknown_position);self.assertIsNone(c.terminal)
        self.observer.decisions.tick(c.t0+3.01);self.assertEqual(c.terminal,'DONNEES_INSUFFISANTES')

    async def test_new_pool_has_only_one_bounded_retry_and_no_retry_on_rpc_error(self):
        self.observer.read_rpc.side_effect=[{'value':None},POOL_RESULT]
        self.start_with_snapshot();await self.resolve()
        self.assertEqual(self.observer.read_rpc.await_count,2);self.assertGreater(self.observer.decisions.candidates[MINT].baseline,0)

    async def test_rpc_error_is_not_retried_or_printed_with_private_body(self):
        self.observer.read_rpc.side_effect=RemoteError('PRIVATE_URL_AND_BODY',status=429)
        self.start_with_snapshot();await self.resolve()
        self.assertEqual(self.observer.read_rpc.await_count,1);self.assertNotIn('PRIVATE',json.dumps(self.output))
        self.assertEqual(self.output[-1]['code'],'DONNEES_INSUFFISANTES')

    async def test_delayed_evaluation_uses_actual_elapsed_time_in_decision(self):
        metadata=swap.decode_pool(POOL_RESULT,POOL);metadata['_cached_at']=time.monotonic();self.observer.swap_pools[POOL]=metadata
        first=swap.normalize(native(BUY,time.monotonic()-1.3)[0],metadata);self.observer.decisions.observe(first)
        c=self.observer.decisions.candidates[MINT];c.baseline=100
        event=dict(native(BUY,c.t0+.5)[0],signature='E'*88,user=paper.public_key(bytes([3])*32),quote_amount_in=5000000000)
        self.observer.observe_native([event],('token',MINT),{'logs':BUY['meta']['logMessages']})
        self.assertEqual(c.terminal,'ACHAT_SIMULE')
        self.assertGreaterEqual(self.output[-1]['elapsed_s'],1.3)

    async def test_truncated_wallet_logs_cannot_allow_positive_decision(self):
        metadata=swap.decode_pool(POOL_RESULT,POOL);metadata['_cached_at']=time.monotonic();self.observer.swap_pools[POOL]=metadata
        first=swap.normalize(native(BUY,time.monotonic()-1.2)[0],metadata);self.observer.decisions.observe(first)
        c=self.observer.decisions.candidates[MINT];c.baseline=100;c.volume=500
        event=dict(native(BUY,c.t0+.5)[0],signature='E'*88,token_amount=10)
        self.observer.observe_native([event],('wallet',paper.BBIG),{'logs':BUY['meta']['logMessages']+['Log truncated']})
        self.assertTrue(c.unknown_position);self.assertIsNone(c.terminal)

    async def test_buy_volume_cannot_win_over_buffered_first_second_full_sale(self):
        original=time.monotonic()-1.2;wall=time.time()-1.2
        self.observer.confirmed(self.trade(),BUY,time.monotonic(),processed_mono=original,processed_wall=wall,account_snapshot=snapshot())
        initial=native(BUY,original)[0]
        volume=dict(initial,user=paper.public_key(bytes([3])*32),signature='E'*88,quote_amount_in=5000000000,mono=original+.4)
        sale=native(SELL,original+.6)[0]
        self.observer.observe_native([volume],('token',MINT),{'logs':BUY['meta']['logMessages']})
        self.observer.observe_native([sale],('wallet',paper.BBIG),{'logs':SELL['meta']['logMessages']})
        await self.resolve();c=self.observer.decisions.candidates[MINT]
        self.assertEqual(c.terminal,'SORTIE_TOTALE_1S')
        self.assertNotIn('ACHAT_SIMULE',[event['code'] for event in self.output])


if __name__=='__main__':unittest.main()
