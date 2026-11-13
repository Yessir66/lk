"""New purchase sources must never reach social publishing or source alerts."""
from decimal import Decimal
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import callout_repost as r
import callout_hub as h
import callout_trojan as t
import callout_workflow as w
import callout_dashboard as d


class BuyOnlyTest(unittest.TestCase):
    def setUp(self):
        # Retain coverage of publication restrictions with synthetic purchase-only
        # fixtures, even though the production roster now contains publishers only.
        fixtures={'ApRnQN2HkbCn7W2WWiT2FEKvuKJp9LugRyAE1a9Hdz1':'FixtureBuy1',
                  'CwUHN4zTn5wiEYoZjsP4FrDvAT9heDWewCTQjhgwhJqS':'FixtureBuy2'}
        for mapping in (r.BUY_ONLY_CALLERS,r.CALLERS):
            patcher=patch.dict(mapping,fixtures);patcher.start();self.addCleanup(patcher.stop)
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.journal=r.Journal(self.root/'state',999,'owner');self.addCleanup(self.journal.db.close)
        self.args=SimpleNamespace(live=True,max_age=60,usd=Decimal(2),take_profit=None,
                                  executor='jupiter',tp_interval=5,text=None,update_delay=10)
        self.execution=MagicMock()
        self.execution.prepare.return_value=dict(raw='bytes',signature='sig',last_valid_height=100)
        self.execution.status.return_value={'err':None,'confirmationStatus':'confirmed'}
        self.clock=patch.object(r.time,'monotonic',return_value=1);self.clock.start();self.addCleanup(self.clock.stop)

    def add(self,author):
        call=dict(calloutId='11111111-1111-4111-8111-111111111111',author=author,
                  coinMint=r.SOL,createdAt=1000,thesis='Original source')
        self.assertTrue(self.journal.add(call,1001,60,0,None,buy_delay=12))
        return call

    def test_each_new_author_buys_once_after_general_delay_without_publication(self):
        for author in r.BUY_ONLY_CALLERS:
            with self.subTest(author=author):
                self.journal.db.execute('DELETE FROM jobs');self.journal.db.commit();self.execution.reset_mock()
                call=self.add(author);engine=r.Engine(self.journal,self.execution,self.args)
                engine.step(1011.9);self.execution.prepare.assert_not_called()
                engine.step(1012);engine.step(1013);engine.step(1014)
                self.execution.prepare.assert_called_once_with(r.SOL)
                self.execution.broadcast.assert_called_once()
                job=self.journal.jobs()[0]
                self.assertTrue(job['buy_confirmed']);self.assertEqual(job['state'],'confirmed')
                self.execution.pump_call.assert_not_called();self.execution.check_auth.assert_not_called()
                self.assertFalse(self.journal.add(call,1014,60,0,None))

    def test_trojan_dispatch_and_confirmation_do_not_publish(self):
        self.add(next(iter(r.BUY_ONLY_CALLERS)))
        job=self.journal.jobs()[0];job['executor']='trojan';self.journal.save(job)
        self.args.executor='trojan';self.execution.owner='owner'
        self.execution.rpc.side_effect=lambda method,params:100 if method=='getSlot' else []
        self.execution.token_amount.return_value=100
        self.execution.find_purchase.return_value=('sig',100,1013)
        engine=t.TrojanEngine(self.journal,self.execution,self.args)
        engine.step(1012);engine.step(1014);engine.step(1016)
        self.execution.relay.send_ca.assert_called_once()
        self.assertTrue(self.journal.jobs()[0]['buy_confirmed'])
        self.execution.pump_call.assert_not_called();self.execution.prepare.assert_not_called()

    def test_new_sources_route_to_purchases_but_never_source_telegram_alerts(self):
        for author in r.BUY_ONLY_CALLERS:
            reader=h.SharedReader(MagicMock(),'owner',0,0,queue.Queue(),queue.Queue())
            reader.refresh(0)
            # Even a followed profile must retain the purchase-only rule.
            reader.follows[author]='followed'
            call=dict(calloutId=author,coinMint=r.SOL,author=author,createdAt=1000)
            reader.route([call]);reader.route([call])
            self.assertEqual(reader.repost_queue.qsize(),1)
            self.assertTrue(reader.alerts_queue.empty())
            self.assertIn(author,reader.targets)

    def test_old_job_missing_flag_cannot_publish_or_accept_updates(self):
        self.add(next(iter(r.BUY_ONLY_CALLERS)))
        job=self.journal.jobs()[0];job.pop('publication_disabled');job.update(state='confirmed',buy_confirmed=True)
        self.journal.save(job)
        engine=r.Engine(self.journal,self.execution,self.args);engine.advance(job,1015)
        self.execution.pump_call.assert_not_called()
        self.assertFalse(self.journal.add_update(dict(mint=r.SOL,source_call_id=job['call_id'],
            update=dict(id='u',createdAt=1014,content='Update')),1015,0))
        watcher=w.UpdateWatcher(queue.Queue(),threading.Event())
        watcher.set_jobs([dict(job,state='posted')]);self.assertEqual(watcher.targets,[])
        self.journal.apply_policy()
        self.assertTrue(self.journal.jobs()[0]['publication_disabled'])

    def test_no_content_or_images_prepared_for_buy_only_jobs(self):
        self.add(next(iter(r.BUY_ONLY_CALLERS)))
        workflow=w.Workflow.__new__(w.Workflow)
        workflow.journal=self.journal;workflow.args=self.args
        workflow.update_events=queue.Queue();workflow.watcher=MagicMock();workflow.content=MagicMock()
        workflow.tick(1002)
        workflow.content.submit.assert_not_called();workflow.content.take.assert_not_called()

    def test_restored_publishing_state_is_quarantined_without_social_request(self):
        self.add(next(iter(r.BUY_ONLY_CALLERS)))
        job=self.journal.jobs()[0];job.update(state='publishing',buy_confirmed=True)
        engine=r.Engine(self.journal,self.execution,self.args);engine.advance(job,1015)
        self.assertEqual(self.journal.jobs()[0]['state'],'review')
        self.execution.pump_call.assert_not_called();self.execution.already_posted.assert_not_called()

    def test_existing_saved_selection_is_preserved_and_new_choices_are_exposed(self):
        import json
        folder=self.root/'data'/'dashboard';folder.mkdir(parents=True)
        original=dict(d.DEFAULTS,callers=list(r.REPOST_CALLERS.values()))
        (folder/'settings.json').write_text(json.dumps(original))
        dashboard=d.Dashboard(self.root);snapshot=dashboard.snapshot()
        self.assertEqual(snapshot['config']['callers'],list(r.REPOST_CALLERS.values()))
        self.assertEqual(snapshot['buy_only_callers'],list(r.BUY_ONLY_CALLERS.values()))
        selected=d.validate_config(dict(original,callers=list(r.BUY_ONLY_CALLERS.values())))
        self.assertEqual(len(selected['callers']),2)

    def test_cli_can_select_only_the_two_purchase_sources(self):
        with patch.dict(r.CALLERS,dict(r.CALLERS)):
            r.parse_args(['--callers',','.join(r.BUY_ONLY_CALLERS.values())])
            self.assertEqual(r.CALLERS,r.BUY_ONLY_CALLERS)

