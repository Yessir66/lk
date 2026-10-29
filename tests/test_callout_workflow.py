import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import callout_repost as r
import callout_content as content
import callout_workflow as workflow
import callout_hub as hub

AUTHOR = next(iter(r.CALLERS))
PARENT = '11111111-1111-4111-8111-111111111111'
OWN = '22222222-2222-4222-8222-222222222222'


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = r.Journal(Path(self.tmp.name)/'state', 999, 'owner')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.journal.db.close)
        self.args = SimpleNamespace(live=True, max_age=60, usd=Decimal('2'), take_profit=Decimal('50'), tp_interval=5)
        self.execution = MagicMock()
        self.execution.prepare.return_value = dict(raw='buy-bytes', signature='buy-sig', last_valid_height=100, cost_usd='2')
        self.execution.pump_call.return_value = (201, json.dumps({'callout': {'calloutId': OWN}}))
        self.execution.status.return_value = {'err': None, 'confirmationStatus': 'confirmed'}
        self.execution.holds.return_value = True
        self.execution.acquired.return_value = 1000
        self.execution.token_amount.return_value = 1000
        self.execution.sol_price.return_value = Decimal('100')
        self.execution.exit_quote.return_value = {'outAmount': '20000000', 'otherAmountThreshold': '19000000'}
        self.engine = r.Engine(self.journal, self.execution, self.args)
        clock = patch.object(r.time, 'monotonic', return_value=10)
        clock.start();self.addCleanup(clock.stop)

    def add(self):
        self.journal.add(dict(calloutId=PARENT, author=AUTHOR, coinMint=r.SOL, createdAt=1000,
                             thesis='Wojak ecosystem narrative', mediaUrl='https://images.pump.fun/test.png'),
                         1001, 60, 0, None, buy_delay=12, stop_loss=50)

    def job(self):
        return self.journal.jobs()[0]

    def confirmed(self):
        self.add()
        self.engine.step(1012)
        self.engine.step(1013)

    def test_buy_waits_12_seconds_and_posts_in_confirmation_step(self):
        self.add()
        self.engine.step(1001);self.engine.step(1011.99)
        self.execution.prepare.assert_not_called()
        self.engine.step(1012)
        self.execution.prepare.assert_called_once()
        self.execution.broadcast.assert_called_once_with('buy-bytes')
        self.execution.pump_call.assert_not_called()
        self.engine.step(1013)
        self.assertEqual(self.job()['state'], 'posted')
        self.assertEqual(self.job()['own_callout_id'], OWN)
        self.execution.pump_call.assert_called_once()

    def test_yourmistyy_buys_after_5_seconds_and_posts_after_confirmation(self):
        author = next(wallet for wallet, name in r.CALLERS.items() if name == 'YourMistyy')
        self.journal.add(dict(calloutId=PARENT, author=author, coinMint=r.SOL, createdAt=1000,
                              thesis='Project context'), 1001, 60, 0, None, buy_delay=12, stop_loss=50)
        self.assertEqual(self.job()['buy_at'], 1005)
        self.engine.step(1004.99)
        self.execution.prepare.assert_not_called()
        self.engine.step(1005)
        self.execution.prepare.assert_called_once()
        self.execution.pump_call.assert_not_called()
        self.engine.step(1006)
        self.assertEqual(self.job()['state'], 'posted')
        self.execution.pump_call.assert_called_once()

    def test_other_callers_keep_configured_buy_delay(self):
        for author, name in r.CALLERS.items():
            if name == 'YourMistyy':
                continue
            with self.subTest(caller=name):
                self.journal.add(dict(calloutId=PARENT, author=author, coinMint=r.SOL,
                                      createdAt=1000), 1001, 60, 0, None, buy_delay=12)
                self.assertEqual(self.job()['buy_at'], 1012)
                self.journal.db.execute('DELETE FROM jobs')
                self.journal.db.commit()

    def test_stop_loss_uses_expected_quote_and_sells_only_bought_tokens(self):
        self.confirmed()
        job = self.job();job['position_tokens'] = 1000;self.journal.save(job)
        self.execution.token_amount.return_value = 9000
        self.execution.sol_price.return_value = Decimal('100')
        self.execution.exit_quote.return_value = {'outAmount': '10000001', 'otherAmountThreshold': '9900000'}
        self.engine.manage_exit(self.job(), 1020)
        self.execution.sign_quote.assert_not_called()
        self.execution.exit_quote.return_value = {'outAmount': '10000000', 'otherAmountThreshold': '9900000'}
        self.execution.sign_quote.return_value = {'raw': 'sell-bytes', 'signature': 'sell-sig', 'last_valid_height': 200}
        self.engine.manage_exit(self.job(), 1025)
        self.assertEqual(self.job()['exit_reason'], 'sl')
        self.assertEqual(self.job()['exit_tokens'], 1000)
        self.assertEqual(self.job()['exit_state'], 'signed')
        self.execution.exit_quote.assert_called_with(r.SOL, 1000)
        self.engine.manage_exit(self.job(), 1026)
        self.assertEqual(self.job()['exit_state'], 'sold')

    def update(self):
        event = dict(mint=r.SOL, source_call_id=PARENT, update=dict(id='source-update', createdAt=1014, content='New project details'))
        self.assertTrue(self.journal.add_update(event, 1015, 10))
        self.assertFalse(self.journal.add_update(event, 1016, 10))

    def test_updates_wait_10_seconds_target_own_post_and_deduplicate_after_restart(self):
        self.confirmed();self.update();self.execution.pump_call.reset_mock()
        self.engine.manage_updates(1023)
        self.execution.pump_call.assert_not_called()
        self.engine.manage_updates(1024)
        self.execution.pump_call.assert_called_once()
        self.assertEqual(self.execution.pump_call.call_args.kwargs['reply_to'], OWN)
        self.assertEqual(self.journal.updates()[0]['state'], 'posted')
        other = r.Journal(Path(self.tmp.name)/'state', 1025, 'owner')
        try:
            r.Engine(other, self.execution, self.args).manage_updates(1030)
        finally:
            other.db.close()
        self.execution.pump_call.assert_called_once()

    def test_update_timeout_reconciles_without_second_post(self):
        self.confirmed();self.update();self.execution.pump_call.reset_mock()
        self.execution.pump_call.side_effect = r.RemoteError('Publication inconnue')
        self.engine.manage_updates(1024)
        self.assertEqual(self.journal.updates()[0]['state'], 'publishing')
        self.execution.reply_exists.return_value = True
        self.engine.manage_updates(1035)
        self.execution.pump_call.assert_called_once()
        self.assertEqual(self.journal.updates()[0]['state'], 'posted')

    def test_closed_position_overrides_prepared_holding_claim(self):
        self.confirmed();self.update()
        update=self.journal.updates()[0];update.update(text='I am still holding', prepared_closed=False);self.journal.save_update(update)
        job=self.job();job['exit_state']='sold';self.journal.save(job)
        self.engine.manage_updates(1024)
        sent=self.execution.pump_call.call_args.args[0]['content']
        self.assertIn('My position is closed',sent)
        self.assertNotIn('I am still holding',sent)

    def test_removed_callers_cannot_buy_or_republish_but_holdings_survive(self):
        old=dict(mint='old', caller='chairman', created=900, state='new', source_author='old-wallet')
        self.journal.save(old)
        held=dict(old,mint='held',state='posted',buy_confirmed=True,cost_usd='2')
        self.journal.save(held);self.journal.apply_policy()
        jobs={j['mint']:j for j in self.journal.jobs()}
        self.assertEqual(jobs['old']['failure_reason'],'caller_removed')
        self.assertEqual(jobs['held']['state'],'posted')
        self.assertTrue(jobs['held']['publication_disabled'])
        self.assertNotIn('stop_loss',jobs['held'])

    def test_default_hub_targets_and_alerts_are_only_three_new_profiles(self):
        pump=MagicMock()
        reader=hub.SharedReader(pump,'owner',0,0,queue.Queue(),queue.Queue())
        reader.refresh(0)
        pump.following.assert_not_called()
        self.assertEqual(reader.follows,r.CALLERS)
        self.assertEqual(set(r.CALLERS.values()),{'lynk0x_','YourMistyy','narc'})

    def test_watcher_uses_author_only_and_rejects_other_wallet(self):
        self.confirmed()
        events,stop=queue.Queue(),threading.Event()
        watcher=workflow.UpdateWatcher(events,stop);watcher.set_jobs([self.job()])
        pump=MagicMock()
        def get(path):
            self.assertIn('filter=AUTHOR_ONLY',path)
            stop.set()
            return {'replies':[dict(id='valid',createdAt=1014,content='Source update',walletAddress=AUTHOR),
                               dict(id='wrong',createdAt=1014,content='Other person',walletAddress='other')]}
        pump.get.side_effect=get
        with patch.object(workflow,'Pump',return_value=pump):watcher.run()
        self.assertEqual(events.qsize(),1)
        self.assertEqual(events.get()['update']['id'],'valid')

    def test_image_download_is_bounded_and_non_images_rejected(self):
        self.assertFalse(content.public_image_url('http://127.0.0.1/private'))
        self.assertFalse(content.public_image_url('https://images.pump.fun.evil.example/a'))
        self.assertTrue(content.public_image_url('https://images.pump.fun/a'))
        http=MagicMock();response=http.get.return_value
        response.status_code=200;response.iter_content.return_value=[b'<html>not an image</html>']
        self.assertEqual(content.download_image(http,'https://images.pump.fun/a',self.tmp.name),{})
        response.iter_content.return_value=[b'\x89PNG\r\n\x1a\n' + b'data']
        image=content.download_image(http,'https://images.pump.fun/a',self.tmp.name)
        self.assertEqual(image['image_mime'],'image/png')
        self.assertTrue(Path(image['image_path']).is_file())

    def test_ai_requires_explicit_mode_and_failure_keeps_factual_fallback(self):
        http=MagicMock();source={'thesis':'Maybe a Launch ecosystem token'}
        with patch.dict(content.os.environ,{'OPENAI_API_KEY':'DO_NOT_PRINT'}):
            text,method=content.rewrite(http,source,'initial',False,'local')
            http.post.assert_not_called()
            http.post.side_effect=RuntimeError('DO_NOT_PRINT')
            text,method=content.rewrite(http,source,'initial',False,'ai')
        self.assertEqual(method,'local_ai_unavailable')
        self.assertIn('Maybe',text)
        self.assertNotIn('DO_NOT_PRINT',text)

    def test_image_multipart_attaches_bytes_and_destination_without_credentials_in_body(self):
        execution=object.__new__(r.Execution)
        execution.args=SimpleNamespace(token=str(Path(self.tmp.name)/'token'))
        Path(execution.args.token).write_text('PRIVATE_SESSION')
        folder=Path(self.tmp.name)/'data/content';folder.mkdir(parents=True)
        photo=folder/'test.image';photo.write_bytes(b'\x89PNG\r\n\x1a\ndata')
        execution.http=MagicMock();execution.http.request.return_value=SimpleNamespace(status_code=201,text='{}')
        with patch.object(r,'ROOT',Path(self.tmp.name)):
            execution.pump_call({'content':'Update'},image={'image_path':str(photo)},reply_to=OWN)
        call=execution.http.request.call_args
        self.assertTrue(call.args[1].endswith('/'+OWN+'/replies'))
        self.assertIn(b'name="image"',call.kwargs['data'])
        self.assertNotIn(b'PRIVATE_SESSION',call.kwargs['data'])

    def test_rejected_image_retries_text_without_another_purchase(self):
        self.add()
        job=self.job();job.update(image_path='prepared-local-image');self.journal.save(job)
        self.engine.step(1012)
        self.execution.pump_call.return_value=(400,'MEDIA_REJECTED')
        self.engine.step(1013)
        self.assertEqual(self.job()['state'],'confirmed')
        self.assertNotIn('image_path',self.job())
        self.execution.pump_call.return_value=(201,json.dumps({'callout':{'calloutId':OWN}}))
        self.engine.step(1014)
        self.assertEqual(self.job()['state'],'posted')
        self.execution.prepare.assert_called_once()

    def test_slow_content_cannot_hold_up_the_scheduled_buy_or_confirmation(self):
        self.add()
        blocked,release,stop=threading.Event(),threading.Event(),threading.Event()
        def slow(*args):
            blocked.set();release.wait(3)
            return {'text':'Prepared later','rewrite_method':'local'}
        flow=None
        with patch.object(workflow.UpdateWatcher,'run',return_value=None), patch.object(content.ContentService,'prepare',side_effect=slow):
            try:
                flow=workflow.Workflow(self.journal,SimpleNamespace(rewrite_mode='local',text=None),queue.Queue(),stop)
                flow.tick(1001)
                self.assertTrue(blocked.wait(1))
                self.engine.step(1012);self.engine.step(1013)
                self.assertEqual(self.job()['state'],'posted')
                self.execution.prepare.assert_called_once()
                self.assertFalse(release.is_set())
            finally:
                release.set();stop.set()
                if flow:flow.close()


if __name__=='__main__':unittest.main()
