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
        self.assertIsNone(p.Prices().dollars(events[0],1000))

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


def key_bytes(key):
    value=0
    for char in key:value=value*58+p.ALPHABET.index(char)
    return value.to_bytes(32,'big')


class ObserverProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_subscription_and_unsubscribe_use_only_read_methods(self):
        with tempfile.TemporaryDirectory() as folder:
            observer=p.PaperObserver('wss://unused.invalid',Path(folder)/'paper.jsonl')
            observer.prices.run=AsyncMock()
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
            rows=[json.loads(line) for line in observer.path.read_text().splitlines()]
            self.assertEqual(rows[0]['limit'],30)
