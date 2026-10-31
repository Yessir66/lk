import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from discord_token_analysis import Store
import discord_token_telegram as t
import discord_solana_watch as w

MINT='3w2TvVmTrtq4kSkoFVrTFQgz6MX7ThzDVrx9r5c3pump'


def report(count=12):
 wallet=dict(available=True,buys=2,sells=1,first_buy={'at':1793291920,'quantity':'100','quotes':{'SOL':'2'}},
             quote_totals={'buy':{'SOL':'5'},'sell':{'SOL':'1'}},bought_quantity='200',sold_quantity='50',
             sold_percent_initial_buy='50',sold_percent_all_buys='25',history_limited=False)
 return dict(mint=MINT,name='HYPER INU',symbol='HI',signal_at=1793296320,created_at=1793291863,
             wallets={'DECU':wallet,'LE L':dict(available=False,reason='RPC indisponible')},
             callouts={'count':count,'errors':[],'coverage':{'LATEST':{'exhausted':True},'CLOSED_PNL':{'exhausted':True}}})


class TelegramTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=Store(Path(self.tmp.name)/'state');self.addCleanup(self.store.db.close)
  self.box=t.Outbox(self.store,'destination')
  self.sender=MagicMock();self.sender.send.return_value=123

 def add(self,count=12):
  self.store.enqueue(MINT,'scout','message',1793296320)
  self.store.finish(MINT,result=report(count))
  self.box.queue()

 def state(self):
  return self.store.db.execute('SELECT state,message_id FROM telegram_reports WHERE mint=?',(MINT,)).fetchone()

 def test_summary_contains_requested_wallets_amounts_percentage_and_signal_cutoff(self):
  text=t.summary(report())
  for value in ('DECU','LE L','2 SOL','5 SOL','50 %','25 %','12 callouts','minimum','18:52:00','RPC indisponible'):
   self.assertIn(value,text)
  self.assertNotIn('0 %',text.split('👛 LE L')[1])
  self.assertLessEqual(len(text.encode('utf-16-le'))//2,4096)

 def test_summary_flags_missing_or_partial_values_and_bounds_unicode_length(self):
  data=report(None);data['name']='😀'*100
  data['wallets']['DECU'].update(history_limited=True,sold_percent_initial_buy=None)
  data['callouts']['errors']=['HTTP 403']
  text=t.summary(data)
  self.assertIn('Nombre indisponible',text)
  self.assertIn('Historique incomplet',text)
  self.assertIn('Vendu / premier achat : indisponible',text)
  self.assertLessEqual(len(text.encode('utf-16-le'))//2,4096)

 def test_one_send_persisted_across_restart(self):
  self.add();self.box.deliver_one(self.sender,100)
  self.assertEqual(self.state(),('sent',123))
  restarted=t.Outbox(self.store,'destination',recover=True)
  restarted.queue();restarted.deliver_one(self.sender,200)
  self.sender.send.assert_called_once()

 def test_updated_result_edits_original_message_instead_of_sending_again(self):
  self.add();self.box.deliver_one(self.sender,100)
  self.add(15);self.box.deliver_one(self.sender,200)
  self.assertEqual(self.sender.send.call_count,2)
  self.assertEqual(self.sender.send.call_args.args[1],123)
  self.assertIn('15 callouts',self.sender.send.call_args.args[0])

 def test_ambiguous_send_does_not_retry_automatically(self):
  self.add();self.sender.send.side_effect=t.TelegramFailure('Network unavailable',ambiguous=True)
  self.box.deliver_one(self.sender,100)
  self.assertEqual(self.state()[0],'uncertain')
  restarted=t.Outbox(self.store,'destination',recover=True,retry_failed=True)
  restarted.queue();restarted.deliver_one(self.sender,1000)
  self.sender.send.assert_called_once()

 def test_429_retries_only_after_server_delay(self):
  self.add();self.sender.send.side_effect=[t.TelegramFailure('Quota',retry_after=30),123]
  self.box.deliver_one(self.sender,100)
  self.assertEqual(self.state()[0],'pending')
  self.assertFalse(self.box.deliver_one(self.sender,129))
  self.box.deliver_one(self.sender,130)
  self.assertEqual(self.state(),('sent',123))

 def test_crash_after_dispatch_intent_requires_manual_check_for_new_message(self):
  self.add();self.sender.send.side_effect=KeyboardInterrupt()
  with self.assertRaises(KeyboardInterrupt):self.box.deliver_one(self.sender,100)
  self.assertEqual(self.state()[0],'sending')
  restarted=t.Outbox(self.store,'destination',recover=True)
  self.assertEqual(self.state()[0],'uncertain')
  self.assertFalse(restarted.deliver_one(self.sender,200))

 def test_history_not_sent_unless_requested_and_destination_change_rejected(self):
  self.store.enqueue(MINT,'scout','m',1793296320);self.store.finish(MINT,result=report())
  self.store.db.execute('DELETE FROM telegram_meta');self.store.db.commit()
  box=t.Outbox(self.store,'destination')
  box.queue();self.assertEqual(self.state()[0],'skipped')
  box=t.Outbox(self.store,'destination',include_history=True)
  box.queue();box.deliver_one(self.sender,100)
  self.assertEqual(self.state()[0],'sent')
  with self.assertRaises(t.TelegramFailure):t.Outbox(self.store,'another-destination')

 def test_sender_uses_plain_text_configured_chat_and_redacts_transport_errors(self):
  sender=t.TelegramSender('private-token','42');sender.http=MagicMock()
  sender.http.post.return_value.status_code=200
  sender.http.post.return_value.json.return_value={'ok':True,'result':{'message_id':7}}
  self.assertEqual(sender.send('<b>Public token text</b>'),7)
  payload=sender.http.post.call_args.kwargs['json']
  self.assertEqual(payload['chat_id'],'42')
  self.assertNotIn('parse_mode',payload)
  sender.http.post.side_effect=RuntimeError('URL with private-token')
  with self.assertRaises(t.TelegramFailure) as caught:sender.send('text')
  self.assertTrue(caught.exception.ambiguous)
  self.assertNotIn('private-token',str(caught.exception))

 def test_existing_scout_config_is_reused_without_rewriting_it(self):
  path=Path(self.tmp.name)/'scout_config.json'
  data=json.dumps({'telegram_bot_token':'fake-token','telegram_chat_id':'42'})
  path.write_text(data)
  with patch.dict(w.os.environ,{},clear=True):
   sender=w.configured_telegram(path)
  self.assertEqual(sender.chat,'42')
  self.assertEqual(path.read_text(),data)

 def test_only_one_watcher_can_hold_the_same_state_lock(self):
  path=Path(self.tmp.name)/'lock-state'
  with w.StateLock(path):
   with self.assertRaises(w.RemoteError):
    with w.StateLock(path):pass
  with w.StateLock(path):pass

 def test_error_notification_is_edited_when_analysis_later_succeeds(self):
  self.store.enqueue(MINT,'scout','message',1793296320)
  self.store.finish(MINT,error='RPC indisponible')
  self.box.queue();self.box.deliver_one(self.sender,100)
  self.assertIn('ANALYSE DU SIGNAL INDISPONIBLE',self.sender.send.call_args.args[0])
  self.add();self.box.deliver_one(self.sender,200)
  self.assertEqual(self.sender.send.call_args.args[1],123)
  self.assertIn('12 callouts',self.sender.send.call_args.args[0])

 def test_ambiguous_edit_can_retry_the_same_message_without_new_send(self):
  self.add();self.box.deliver_one(self.sender,100)
  self.add(15)
  self.sender.send.side_effect=[t.TelegramFailure('Timeout',ambiguous=True),123]
  self.box.deliver_one(self.sender,200)
  self.assertEqual(self.state()[0],'pending')
  self.assertFalse(self.box.deliver_one(self.sender,229))
  self.box.deliver_one(self.sender,230)
  self.assertEqual(self.sender.send.call_args.args[1],123)
  self.assertEqual(self.state()[0],'sent')


if __name__=='__main__':unittest.main()
