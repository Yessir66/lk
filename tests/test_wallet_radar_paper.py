import base64
import asyncio
import contextlib
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wallet_radar_paper as p

MINT='A'*32
OTHER='B'*32
CREATOR='D'*32
SIGNATURE='C'*88


def event(at=0,**values):
    return dict(dict(mint=MINT,user=OTHER,creator=CREATOR,token_amount=10,is_buy=True,
                     signature='E'*88,slot=10,index=0,mono=100+at,wall=1000+at,
                     quote_mint=p.SOL,quote_amount=10**9,sol_amount=10**9),**values)


def encoded_event(**values):
    values=dict(dict(mint=bytes([2])*32,user=bytes([3])*32,creator=bytes([4])*32,
                     token_amount=100,is_buy=True,sol_amount=10**9,quote_mint=bytes([0])*32),**values)
    data=p.DISCRIMINATOR
    for name,kind in p.FIELDS:
        value=values.get(name)
        if kind=='pubkey':data+=value if value is not None else bytes(32)
        elif kind=='bool':data+=bytes([int(value or 0)])
        elif kind=='string':
            raw=(value or 'buy').encode();data+=len(raw).to_bytes(4,'little')+raw
        elif kind=='shareholders':data+=bytes(4)
        else:data+=int(value or 0).to_bytes(8,'little',signed=kind=='i64')
    return data


class PaperDecisionsTest(unittest.TestCase):
    def setUp(self):
        self.prices=p.Prices();self.prices.update({'solana':{'usd':100,'last_updated_at':1000}},1000)
        self.output=[];self.d=p.Decisions(self.prices,self.output.append)
        self.d.observe(event(user=p.BBIG,signature=SIGNATURE))
        self.c=self.d.candidates[MINT]

    def codes(self):return [row['code'] for row in self.output]

    def buy(self,at=0.5,dollars=400,**values):
        self.d.observe(event(at,quote_amount=int(Decimal(str(dollars))*10**7),**values))

    def test_no_purchase_before_one_second_even_when_volume_is_already_sufficient(self):
        self.c.baseline=100
        self.buy(.2,450);self.d.tick(100.999)
        self.assertNotIn('ACHAT_SIMULE',self.codes())
        self.d.tick(101)
        self.assertEqual(self.codes()[-2:],['ATTENTE_VOLUME','ACHAT_SIMULE'])

    def test_initial_transaction_is_excluded_and_subsequent_bbig_buy_counts(self):
        self.c.baseline=100
        self.buy(.2,1000,signature=SIGNATURE)
        self.assertEqual(self.c.volume,0)
        self.buy(1.2,400,user=p.BBIG)
        self.assertEqual(self.c.volume,400);self.assertEqual(self.codes()[-1],'ACHAT_SIMULE')

    def test_complete_position_sold_within_first_second_cancels(self):
        self.c.baseline=100
        self.d.observe(event(.8,user=p.BBIG,is_buy=False,token_amount=100))
        self.buy(1.2,700,signature='F'*88)
        self.assertEqual(self.c.terminal,'SORTIE_TOTALE_1S')
        self.assertNotIn('ACHAT_SIMULE',self.codes())

    def test_partial_sale_does_not_cancel(self):
        self.c.baseline=100
        self.d.observe(event(.5,user=p.BBIG,is_buy=False,token_amount=40))
        self.buy(1.3,400,signature='F'*88)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_whole_position_includes_holdings_from_before_initial_buy(self):
        self.c.baseline=1000
        self.d.observe(event(.5,user=p.BBIG,is_buy=False,token_amount=10))
        self.buy(1.3,400,signature='F'*88)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_full_sale_after_one_second_does_not_cancel(self):
        self.c.baseline=100
        self.d.tick(101)
        self.d.observe(event(1.5,user=p.BBIG,is_buy=False,token_amount=100))
        self.buy(2,400,signature='F'*88)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_initialization_closure_and_transfer_do_not_invalidate_buy_volume(self):
        self.c.baseline=100
        self.buy(.5,232)
        self.d.tick(101)
        for instruction in ('InitializeAccount3','CloseAccount','TransferChecked'):
            self.d.unparsed(('token',MINT),{'err':None,'logs':['Program log: Instruction: '+instruction]},102)
        self.d.tick(103.01)
        self.assertEqual(self.c.terminal,'VOLUME_INSUFFISANT')
        self.assertFalse(self.c.unknown_volume)

    def test_unparsed_sale_after_one_second_does_not_block_subsequent_threshold(self):
        self.c.baseline=100;self.d.tick(101)
        value={'err':None,'logs':['Program log: Instruction: Sell']}
        self.d.unparsed(('wallet',p.BBIG),value,101.8)
        self.d.unparsed(('token',MINT),value,101.8)
        self.buy(2,400)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_sell_with_unknown_appended_fields_after_first_second_does_not_invalidate_buy_volume(self):
        self.c.baseline=100;self.d.tick(101)
        self.d.observe(event(1.8,user=p.BBIG,is_buy=False,token_amount=100,unparsed_tail_bytes=8))
        self.buy(2,400,signature='F'*88)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_unparsed_early_sale_still_invalidates_position(self):
        self.c.baseline=100
        self.d.unparsed(('wallet',p.BBIG),{'err':None,'logs':['Program log: Instruction: Sell']},100.8)
        self.buy(1.5,500);self.d.tick(103.01)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')
        self.assertIn('MOUVEMENT_POSITION_NON_DECODE',self.output[-1]['missing_reasons'])

    def test_unparsed_swap_is_still_incomplete_and_reason_is_preserved(self):
        self.c.baseline=100
        self.d.unparsed(('token',MINT),{'err':None,'logs':['Program log: Instruction: SwapV2']},101.5)
        self.d.tick(103.01)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')
        self.assertIn('ACHAT_OU_SWAP_NON_DECODE',self.output[-1]['missing_reasons'])

    def test_truncated_logs_remain_incomplete_even_without_instruction(self):
        self.c.baseline=100
        self.d.unparsed(('token',MINT),{'err':None,'logs':['Log truncated']},101.5)
        self.d.tick(103.01)
        self.assertIn('LOGS_TRONQUES',self.output[-1]['missing_reasons'])

    def test_deadline_remains_three_seconds_from_initial_event(self):
        self.c.baseline=100
        self.buy(2.9,399);self.d.tick(103.001)
        self.buy(3.1,900,signature='F'*88)
        self.assertEqual(self.c.terminal,'VOLUME_INSUFFISANT')
        self.assertEqual(self.c.volume,399)

    def test_exact_threshold_and_deadline_can_qualify(self):
        self.c.baseline=100;self.buy(3,400)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_duplicate_signature_and_event_index_count_once(self):
        self.c.baseline=100
        self.buy(.4,200);self.buy(.5,200)
        self.assertEqual(self.c.volume,200)
        self.buy(1.4,200,signature='F'*88)
        self.assertEqual(self.c.volume,400);self.assertEqual(self.c.buys,2)

    def test_bwam_creator_excludes_entire_token(self):
        mint='F'*32
        self.d.observe(event(mint=mint,user=p.BBIG,creator=p.BWAM,signature='G'*88))
        self.assertEqual(self.d.candidates[mint].terminal,'DEV_EXCLU')

    def test_missing_initial_position_prevents_green_light(self):
        self.buy(.5,500);self.d.tick(101);self.d.tick(103.01)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')

    def test_unrecognized_early_position_change_prevents_green_light(self):
        self.c.baseline=100;self.c.unknown_position=True
        self.buy(1.5,500);self.d.tick(103.01)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')

    def test_stale_price_and_unknown_quote_do_not_become_zero_volume(self):
        self.c.baseline=100;self.prices.published=0
        self.buy(1.5,500);self.d.tick(103.01)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')
        self.assertIsNone(self.prices.dollars(event(quote_mint=CREATOR),1000))

    def test_pump_quote_uses_six_decimals_and_its_own_usd_price(self):
        self.c.baseline=100
        self.prices.update_pump({'pump-fun':{'usd':'0.004','last_updated_at':1000}},1000)
        self.d.observe(event(1.2,quote_mint=p.PUMP_TOKEN,quote_amount=100000*10**6))
        self.assertEqual(self.c.volume,400)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_ttwo_volume_uses_verified_six_decimals(self):
        self.c.baseline=100
        self.prices.update_ttwo({p.TTWO_PRICE_ID:{'usd':'200','last_updated_at':1000}},1000)
        self.d.observe(event(1.2,quote_mint=p.TTWO,quote_amount=2*10**6))
        self.assertEqual(self.c.volume,400)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_doge_volume_uses_eight_decimals_and_address_specific_candle(self):
        self.c.baseline=100
        candle={'meta':{'base':{'address':p.DOGE}},'data':{'attributes':{'ohlcv_list':[[960,.05,.05,.05,.05,10]]}}}
        self.prices.update_doge(candle,1000)
        self.d.observe(event(.2,quote_mint=p.DOGE,quote_amount=8000*10**8))
        self.assertIsNone(self.c.terminal)
        self.d.tick(101)
        self.assertEqual(self.c.volume,400)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')

    def test_ttwo_and_doge_pending_prices_keep_deadline(self):
        for quote in (p.TTWO,p.DOGE):
            with self.subTest(quote=quote):
                output=[];prices=p.Prices();engine=p.Decisions(prices,output.append)
                engine.observe(event(user=p.BBIG,signature=SIGNATURE))
                candidate=engine.candidates[MINT];candidate.baseline=100
                engine.observe(event(.5,quote_mint=quote,quote_amount=10**12))
                reason='COURS_TTWO_USD_ABSENT_OU_PERIME' if quote==p.TTWO else 'COURS_DOGE_USD_ABSENT_OU_PERIME'
                self.assertIn(reason,candidate.reasons)
                self.assertNotIn('DEVISE_COTATION_NON_SUPPORTEE',candidate.reasons)
                engine.tick(103.01)
                self.assertEqual(candidate.terminal,'DONNEES_INSUFFISANTES')
                self.assertEqual(candidate.volume,0)

    def test_pending_price_is_revalued_before_deadline_without_resetting_t0(self):
        self.c.baseline=100
        self.d.observe(event(.2,quote_mint=p.PUMP_TOKEN,quote_amount=100000*10**6))
        self.assertEqual(self.c.volume,0);self.assertEqual(len(self.c.pending_prices),1)
        self.prices.update_pump({'pump-fun':{'usd':'0.004','last_updated_at':1001}},1001)
        self.d.tick(101.5)
        self.assertEqual(self.c.t0,100);self.assertEqual(self.c.volume,400)
        self.assertEqual(self.c.terminal,'ACHAT_SIMULE')
        self.assertNotIn('COURS_PUMP_USD_ABSENT_OU_PERIME',self.output[-1]['missing_reasons'])
        self.d.tick(102);self.assertEqual(self.c.volume,400)

    def test_price_arriving_after_deadline_cannot_qualify_old_buys(self):
        self.c.baseline=100
        self.d.observe(event(.2,quote_mint=p.PUMP_TOKEN,quote_amount=100000*10**6))
        self.prices.update_pump({'pump-fun':{'usd':'0.004','last_updated_at':1003}},1003)
        self.d.tick(103.1)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')
        self.assertEqual(self.c.volume,0)
        self.assertNotIn('ACHAT_SIMULE',self.codes())

    def test_pending_pump_buys_are_deduplicated_before_price_arrives(self):
        self.c.baseline=100
        value=event(.2,quote_mint=p.PUMP_TOKEN,quote_amount=100000*10**6)
        self.d.observe(value);self.d.observe(value)
        self.prices.update_pump({'pump-fun':{'usd':'0.004','last_updated_at':1001}},1001)
        self.d.tick(101.5)
        self.assertEqual(self.c.buys,1);self.assertEqual(self.c.volume,400)

    def test_new_price_does_not_clear_unrelated_unknown_position(self):
        self.c.baseline=100;self.c.unknown_position=True
        self.d.observe(event(.2,quote_mint=p.PUMP_TOKEN,quote_amount=100000*10**6))
        self.prices.update_pump({'pump-fun':{'usd':'0.004','last_updated_at':1001}},1001)
        self.d.tick(101.5);self.d.tick(103.1)
        self.assertEqual(self.c.volume,400);self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')

    def test_disconnect_abandons_current_decisions(self):
        self.c.baseline=100;self.d.interrupted(100.3)
        self.buy(1.5,700)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')

    def test_late_confirmation_never_restarts_three_second_clock(self):
        self.buy(1.4,500)
        trade={'wallet':p.BBIG,'kind':'buy','source':'websocket','mint':MINT,'signature':SIGNATURE,'swap_proof_version':1}
        tx={'meta':{'postTokenBalances':[{'mint':MINT,'owner':p.BBIG,'accountIndex':1,'uiTokenAmount':{'amount':'100'}}]}}
        self.d.confirmed(trade,tx,103.1)
        self.assertEqual(self.c.t0,100);self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')
        self.assertNotIn('ACHAT_SIMULE',self.codes())

    def test_exactly_thirty_distinct_tokens_are_sampled(self):
        output=[];d=p.Decisions(self.prices,output.append)
        for i in range(31):
            mint=p.public_key(bytes([i+1])*32)
            d.observe(event(mint=mint,user=p.BBIG,creator=p.BWAM,signature=SIGNATURE))
        self.assertEqual(len(d.sampled),30)
        self.assertEqual(len([row for row in output if row['code']=='DEV_EXCLU']),30)

    def test_unknown_abi_extension_on_initial_trade_never_creates_green_light(self):
        mint='F'*32
        self.d.observe(event(mint=mint,user=p.BBIG,unparsed_tail_bytes=8))
        self.assertEqual(self.d.candidates[mint].terminal,'DONNEES_INSUFFISANTES')

    def test_earliest_main_notification_keeps_original_deadlines(self):
        with tempfile.TemporaryDirectory() as folder,patch('builtins.print'):
            observer=p.PaperObserver('wss://unused.invalid',Path(folder)/'paper.jsonl')
            observer.prices=self.prices;observer.decisions.prices=self.prices
            observer.decisions.observe(event(user=p.BBIG,signature=SIGNATURE))
            observer.decisions.observe(event(.98,user=p.BBIG,is_buy=False,token_amount=100))
            observer.decisions.observe(event(.5,quote_amount=5000000000,signature='F'*88))
            trade={'wallet':p.BBIG,'kind':'buy','source':'websocket','mint':MINT,'signature':SIGNATURE,'swap_proof_version':1}
            tx={'transaction':{'message':{'accountKeys':[p.BBIG,OTHER]}},
                'meta':{'postTokenBalances':[{'mint':MINT,'owner':p.BBIG,'accountIndex':1,'uiTokenAmount':{'amount':'100'}}]}}
            snapshot={'context':{'slot':10},'value':[{'pubkey':OTHER,'account':{'data':{'parsed':{'info':{
                'owner':p.BBIG,'mint':MINT,'tokenAmount':{'amount':'100'}}}}}}]}
            observer.confirmed(trade,tx,101,processed_mono=99.9,processed_wall=999.9,account_snapshot=snapshot)
            c=observer.decisions.candidates[MINT]
            self.assertEqual(c.t0,99.9)
            self.assertEqual(c.terminal,'ACHAT_SIMULE')  # Sale at 100.98 is beyond the first second from 99.9.

    def test_unseen_nonzero_token_account_prevents_full_position_claim(self):
        trade={'wallet':p.BBIG,'kind':'buy','source':'websocket','mint':MINT,'signature':SIGNATURE,'swap_proof_version':1}
        tx={'transaction':{'message':{'accountKeys':[p.BBIG,OTHER]}},
            'meta':{'postTokenBalances':[{'mint':MINT,'owner':p.BBIG,'accountIndex':1,'uiTokenAmount':{'amount':'100'}}]}}
        snapshot={'context':{'slot':10},'value':[{'pubkey':CREATOR,'account':{'data':{'parsed':{'info':{
            'owner':p.BBIG,'mint':MINT,'tokenAmount':{'amount':'500'}}}}}}]}
        self.d.confirmed(trade,tx,100.5,snapshot)
        self.buy(1.5,500);self.d.tick(103.1)
        self.assertEqual(self.c.terminal,'DONNEES_INSUFFISANTES')


class NativeDecoderTest(unittest.TestCase):
    def test_actual_archived_transaction_decodes_creator_and_non_sol_quote(self):
        tx=json.loads((Path(__file__).parent/'fixtures/decu_v1_buy.json').read_text())
        events=p.native_trades({'signature':SIGNATURE,'err':None,'logs':tx['meta']['logMessages']},1,1000,100)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['token_amount'],22741914376767)
        self.assertEqual(events[0]['creator'],'DitH8kfqVFDrDZzQmpQ1sRmyad48546XmbushMM8vmdG')
        self.assertEqual(events[0]['quote_amount'],600584)
        self.assertEqual(events[0]['quote_transfer_fee'],0)
        self.assertEqual(events[0]['unparsed_tail_bytes'],0)
        self.assertIsNone(p.Prices().dollars(events[0],1000))

    def test_official_transfer_fee_field_and_previous_layout_are_both_supported(self):
        raw=encoded_event(quote_transfer_fee=12345)
        current=p.decode_event(raw)
        self.assertEqual(current['quote_transfer_fee'],12345)
        self.assertEqual(current['unparsed_tail_bytes'],0)
        legacy=p.decode_event(raw[:-8])
        self.assertNotIn('quote_transfer_fee',legacy)
        self.assertEqual(legacy['unparsed_tail_bytes'],0)
        self.assertEqual(legacy['token_amount'],current['token_amount'])

    def test_current_official_event_can_progress_to_theoretical_decision(self):
        prices=p.Prices();prices.update({'solana':{'usd':100,'last_updated_at':1000}},1000)
        output=[];engine=p.Decisions(prices,output.append)
        def logs(raw):return [f'Program {p.PUMP} invoke [1]',
            'Program data: '+base64.b64encode(raw).decode(),f'Program {p.PUMP} success']
        raw=encoded_event(user=key_bytes(p.BBIG),quote_transfer_fee=0)
        initial=p.native_trades({'signature':SIGNATURE,'err':None,'logs':logs(raw)},10,1000,100)[0]
        engine.observe(initial)
        candidate=engine.candidates[initial['mint']]
        self.assertIsNone(candidate.terminal)
        candidate.baseline=100
        raw=encoded_event(sol_amount=4*10**9,quote_transfer_fee=0)
        following=p.native_trades({'signature':'F'*88,'err':None,'logs':logs(raw)},10,1000.5,100.5)[0]
        engine.observe(following);engine.tick(101)
        self.assertEqual(candidate.terminal,'ACHAT_SIMULE')
        self.assertEqual(candidate.volume,400)

    def test_program_stack_prevents_fake_event_logs_from_other_programs(self):
        raw=base64.b64encode(encoded_event()).decode()
        good=[f'Program {p.PUMP} invoke [1]','Program data: '+raw,f'Program {p.PUMP} success']
        value={'signature':SIGNATURE,'err':None,'logs':good}
        self.assertEqual(len(p.native_trades(value,1,1000,100)),1)
        value['logs']=[f'Program {OTHER} invoke [1]','Program data: '+raw,f'Program {OTHER} success']
        self.assertEqual(p.native_trades(value,1,1000,100),[])
        value['logs']=good;value['err']={'InstructionError':[1,'failed']}
        self.assertEqual(p.native_trades(value,1,1000,100),[])

    def test_truncated_and_future_unknown_layouts_are_rejected(self):
        raw=encoded_event()
        with self.assertRaises(ValueError):p.decode_event(raw[:-1])
        self.assertEqual(p.decode_event(raw+b'extra')['unparsed_tail_bytes'],5)
        self.assertIsNone(p.decode_event(b'not-a-trade-event'))

    def test_usdc_has_nominal_usd_value_without_sol_price(self):
        self.assertEqual(p.Prices().dollars(event(quote_mint=p.USDC,quote_amount=400000000),1000),400)

    def test_price_rejects_nonfinite_and_stale_snapshots(self):
        for price,published in [('NaN',1000),('100',1),('0',1000),('100',1001)]:
            with self.subTest(price=price,published=published),self.assertRaises(ValueError):
                p.Prices().update({'solana':{'usd':price,'last_updated_at':published}},1000)

    def test_pump_price_rejects_invalid_and_stale_prices(self):
        for price,published in [('NaN',1000),('-1',1000),('0.004',900),('0.004',1001)]:
            with self.subTest(price=price,published=published),self.assertRaises(ValueError):
                p.Prices().update_pump({'pump-fun':{'usd':price,'last_updated_at':published}},1000)

    def test_ttwo_price_rejects_invalid_stale_and_future_prices(self):
        for price,published in [('NaN',1000),('Infinity',1000),('-1',1000),('200',900),('200',1001),('200',True)]:
            with self.subTest(price=price,published=published),self.assertRaises((ValueError,ArithmeticError)):
                p.Prices().update_ttwo({p.TTWO_PRICE_ID:{'usd':price,'last_updated_at':published}},1000)

    def test_doge_rejects_wrong_asset_missing_stale_and_invalid_candles(self):
        import copy
        valid={'meta':{'base':{'address':p.DOGE}},'data':{'attributes':{'ohlcv_list':[[960,.05,.05,.05,.05,10]]}}}
        invalid=[]
        for value in ('NaN',0,-1):
            data=copy.deepcopy(valid);data['data']['attributes']['ohlcv_list'][0][4]=value;invalid.append(data)
        for value in (900,1001):
            data=copy.deepcopy(valid);data['data']['attributes']['ohlcv_list'][0][0]=value;invalid.append(data)
        data=copy.deepcopy(valid);data['meta']['base']['address']=p.SOL;invalid.append(data)
        invalid.extend([{}, {'meta':valid['meta'],'data':{'attributes':{'ohlcv_list':[]}}}])
        for data in invalid:
            with self.subTest(data=data),self.assertRaises((ValueError,ArithmeticError)):
                p.Prices().update_doge(data,1000)
        prices=p.Prices();prices.update_doge(valid,1000)
        self.assertIsNone(prices.dollars(event(quote_mint=p.DOGE,quote_amount=10**8),1051))

    def test_report_uses_last_session_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'paper.jsonl'
            rows=[{'type':'paper_session','session_id':'old','limit':30},
                  {'type':'paper_decision','session_id':'old','mint':MINT,'code':'ACHAT_SIMULE'},
                  {'type':'paper_session','session_id':'new','limit':30},
                  {'type':'paper_decision','session_id':'new','mint':OTHER,'code':'DEV_EXCLU'}]
            path.write_text('\n'.join(map(json.dumps,rows))+'\npartial')
            output=StringIO()
            with contextlib.redirect_stdout(output):p.report(path)
            self.assertIn('1/30',output.getvalue());self.assertIn('DEV_EXCLU : 1',output.getvalue())
            self.assertNotIn('ACHAT_SIMULE :',output.getvalue())
            self.assertIn('volume USD observe non mesure',output.getvalue())


def key_bytes(key):
    value=0
    for char in key:value=value*58+p.ALPHABET.index(char)
    return value.to_bytes(32,'big')


class ObserverProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_subscription_and_unsubscribe_use_only_read_methods(self):
        with tempfile.TemporaryDirectory() as folder:
            observer=p.PaperObserver('wss://unused.invalid',Path(folder)/'paper.jsonl')
            observer.prices.run=AsyncMock()
            observer.prices.run_doge=AsyncMock()
            sent=[];queue=asyncio.Queue();mint=p.public_key(bytes([2])*32)
            def frame(signature,raw,sub):
                return json.dumps({'method':'logsNotification','params':{'subscription':sub,'result':{
                    'context':{'slot':10},'value':{'signature':signature,'err':None,'logs':[
                    f'Program {p.PUMP} invoke [1]','Program data: '+base64.b64encode(raw).decode(),f'Program {p.PUMP} success']}}}})
            class Socket:
                async def send(self,raw):
                    request=json.loads(raw);sent.append(request)
                    if request['method']=='logsUnsubscribe':raise asyncio.CancelledError()
                    identity=request['id'];is_wallet=request['params'][0]['mentions']==[p.BBIG]
                    queue.put_nowait(json.dumps({'id':identity,'result':1 if is_wallet else 2}))
                    if is_wallet:
                        queue.put_nowait(frame(SIGNATURE,encoded_event(user=key_bytes(p.BBIG)),1))
                    else:
                        observer.decisions.candidates[mint].baseline=100
                        raw=encoded_event(quote_mint=key_bytes(p.USDC),quote_amount=400000000)
                        queue.put_nowait(frame('F'*88,raw,2))
                async def recv(self):return await queue.get()
            class Connection:
                async def __aenter__(self):return Socket()
                async def __aexit__(self,*args):return False
            with patch('websockets.asyncio.client.connect',return_value=Connection()),patch('builtins.print'):
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(observer.run(),3)
            self.assertEqual([row['method'] for row in sent],['logsSubscribe','logsSubscribe','logsUnsubscribe'])
            self.assertEqual(observer.results[mint],'ACHAT_SIMULE')
            observer.prices.run_doge.assert_awaited_once()
            rows=[json.loads(line) for line in observer.path.read_text().splitlines()]
            self.assertEqual(rows[0]['limit'],30)
