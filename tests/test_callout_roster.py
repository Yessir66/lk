"""Eight replacement callers: entries, original callouts, and author updates."""
from pathlib import Path
import json
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import callout_repost as r
import callout_trojan as t
import callout_workflow as w
import callout_dashboard as d
import callout_hub as h

WALLETS=[
 'Dzv3gkwmkMkPoJwSaRCPyQZFhc8hNUL3FwaFgp3tgKUr',
 'FniZYxox48b6FPfJC9XZVAdVT3fQ6dUiXzQ5ZBjUCyz6',
 'DJ78WYDTkU5Fo5oJDMyqD7KmXJKQUQMiqsuxvokUdxmh',
 '4JBkkjZhReTMCtriRoL7epbTMCoxNoX1Sab5vUvqDfMy',
 '8Fxc1gYmuR1fvQsnCXHnFCmimNpUZ4r5gopuVoM1pjvg',
 '3saciJi6vUnWx1GgWPEwtgSQCscXstacqZkPZCpuxffW',
 'GAVq4j28TVdfG138wMtgLcPQGiP2rEkzUmWmhZwUrRa8',
 '2oHsGWdPkHoCfesoUfJHQfjnaethvUuYvEANQByghpXd']
OLD={
 'CkPFGv2Wv1vwdWjtXioEgb8jhZQfs3eVZez3QCetu7xD':'lynk0x_',
 '8NFh9y6qmMigMvRtT2KhrnCDo4BE2jMYcondAzmXZ26E':'YourMistyy',
 'CxgPWvH2GoEDENELne2XKAR2z2Fr4shG2uaeyqZceGve':'narc',
 'ApRnQN2HkbCn7W2WWiT2FEKvuKJp9LugRyAE1a9Hdz1':'ApRnQN2H',
 'CwUHN4zTn5wiEYoZjsP4FrDvAT9heDWewCTQjhgwhJqS':'CwUHN4zT'}
PARENT='11111111-1111-4111-8111-111111111111'
OWN='22222222-2222-4222-8222-222222222222'


class RosterTest(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
  self.journal=r.Journal(self.root/'state',999,'owner');self.addCleanup(self.journal.db.close)
  self.args=SimpleNamespace(live=True,max_age=100,usd=2,take_profit=None,executor='jupiter',tp_interval=5)
  clock=patch.object(r.time,'monotonic',return_value=1);clock.start();self.addCleanup(clock.stop)

 def test_exact_requested_wallets_have_publication_permissions_and_no_special_delays(self):
  from solders.pubkey import Pubkey
  self.assertEqual(list(r.CALLERS),WALLETS)
  self.assertEqual(r.CALLERS,r.REPOST_CALLERS)
  self.assertEqual(r.BUY_ONLY_CALLERS,{})
  self.assertEqual(r.BUY_DELAY_OVERRIDES,{})
  for wallet in WALLETS:self.assertEqual(str(Pubkey.from_string(wallet)),wallet)
  d.validate_installation()

 def test_every_new_caller_completes_buy_original_and_delayed_update_for_both_executors(self):
  for executor in ('jupiter','trojan'):
   for wallet in WALLETS:
    with self.subTest(wallet=wallet,executor=executor):
     self.journal.db.execute('DELETE FROM jobs');self.journal.db.execute('DELETE FROM updates');self.journal.db.commit()
     call=dict(calloutId=PARENT,author=wallet,coinMint=r.SOL,createdAt=1000,thesis='Public project context')
     self.assertTrue(self.journal.add(call,1001,100,0,None,buy_delay=12))
     job=self.journal.jobs()[0];self.assertEqual(job['buy_at'],1012);self.assertTrue(r.can_publish(job))
     execution=MagicMock();execution.prepare.return_value=dict(raw='bytes',signature='sig',last_valid_height=100)
     execution.status.return_value={'err':None,'confirmationStatus':'confirmed'}
     execution.pump_call.return_value=(201,json.dumps({'calloutId':OWN}))
     execution.owner='owner';execution.rpc.side_effect=lambda method,params:100 if method=='getSlot' else []
     execution.token_amount.return_value=0
     execution.find_purchase.return_value=('sig',100,1013)
     self.args.executor=executor
     if executor=='trojan':
      job['executor']='trojan';self.journal.save(job);engine=t.TrojanEngine(self.journal,execution,self.args)
     else:engine=r.Engine(self.journal,execution,self.args)
     engine.step(1011.9);execution.prepare.assert_not_called();execution.relay.send_ca.assert_not_called()
     engine.step(1012);execution.pump_call.assert_not_called()
     engine.step(1014);self.assertEqual(self.journal.jobs()[0]['state'],'posted')
     self.assertTrue(self.journal.jobs()[0]['buy_confirmed']);execution.pump_call.assert_called_once()
     watcher=w.UpdateWatcher(queue.Queue(),threading.Event());watcher.set_jobs(self.journal.jobs())
     self.assertEqual(len(watcher.targets),1)
     event=dict(mint=r.SOL,source_call_id=PARENT,update=dict(id='update1',createdAt=1015,content='Project update'))
     self.assertTrue(self.journal.add_update(event,1016,10))
     engine.manage_updates(1024.9);self.assertEqual(execution.pump_call.call_count,1)
     engine.manage_updates(1025);self.assertEqual(execution.pump_call.call_count,2)
     self.assertEqual(execution.pump_call.call_args.kwargs['reply_to'],OWN)
     self.assertEqual(self.journal.updates()[0]['state'],'posted')
     self.assertFalse(self.journal.add(call,1026,100,0,None))
     if executor=='trojan':
      execution.relay.send_ca.assert_called_once();execution.prepare.assert_not_called()
     else:execution.broadcast.assert_called_once()

 def test_old_callers_cannot_create_jobs_or_publish_and_open_positions_are_kept(self):
  execution=MagicMock();engine=r.Engine(self.journal,execution,self.args)
  for index,(wallet,name) in enumerate(OLD.items()):
   self.assertFalse(self.journal.add(dict(calloutId=PARENT,author=wallet,coinMint=r.SOL,createdAt=1000),1001,100,0,None))
   for state in ('new','confirmed','posted'):
    self.journal.save(dict(mint=f'{index}-{state}',call_id=PARENT,source_author=wallet,caller=name,
      created=1000,due=1000,state=state,buy_confirmed=state!='new',exit_state='external'))
  self.journal.apply_policy()
  for job in self.journal.jobs():
   self.assertTrue(job['publication_disabled'])
   if job['mint'].endswith('-new'):self.assertEqual(job['failure_reason'],'caller_removed')
   else:self.assertIn(job['state'],('confirmed','posted'));self.assertTrue(job['buy_confirmed'])
   engine.advance(job,1015)
   self.assertFalse(self.journal.add_update(dict(mint=job['mint'],source_call_id=PARENT),1015,0))
  execution.pump_call.assert_not_called();execution.prepare.assert_not_called()

 def test_saved_settings_keep_amounts_delays_mode_profile_and_executor_when_roster_changes(self):
  directory=self.root/'data'/'dashboard';directory.mkdir(parents=True)
  saved=dict(d.DEFAULTS,callers=list(OLD.values()),usd=17,profile='ExistingProfile',mode='live',
             buy_delay=22,update_delay=19,max_age=100,executor='trojan',take_profit=85,stop_loss=32)
  path=directory/'settings.json';original=json.dumps(saved);path.write_text(original)
  dashboard=d.Dashboard(self.root)
  self.assertEqual(dashboard.config,dict(saved,callers=[v[:8] for v in WALLETS]))
  self.assertIsNone(dashboard.process);self.assertEqual(path.read_text(),original)
  self.assertEqual(dashboard.snapshot()['buy_only_callers'],[])

 def test_shared_reader_routes_new_callers_and_rejects_all_previous_sources(self):
  reader=h.SharedReader(MagicMock(),'owner',999,999,queue.Queue(),queue.Queue());reader.refresh(0)
  rows=[dict(calloutId=wallet,author=wallet,coinMint=r.SOL,createdAt=1000) for wallet in WALLETS+list(OLD)]
  reader.route(rows);reader.route(rows)
  self.assertEqual(reader.repost_queue.qsize(),8);self.assertEqual(reader.alerts_queue.qsize(),8)
  self.assertEqual(set(reader.targets),set(WALLETS))

