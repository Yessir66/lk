import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import discord_token_narrative as n
from discord_token_analysis import Store, count_callouts
from discord_token_telegram import Outbox,TelegramFailure,TelegramSender
import discord_solana_watch as w
from test_discord_token_telegram import report,MINT

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQa8AAAAASUVORK5CYII=')


def sources_result(count=12):
 data=report();calls=[]
 for i in range(count):
  text=' '.join([f'contexte{i}',f'origine{i}',f'événement{i}',f'personnage{i}',f'affirmation{i}',f'preuve{i}']+['détail']*(i+1))
  calls.append({'id':f'{i+1:08x}-1111-4111-8111-111111111111','at':1793292000+i,'wallet':'author'+str(i),
                'caller':'Auteur '+str(i),'text':text})
 data['callouts']['callouts']=calls
 return data


def draft(**changes):
 return dict({'title':'Un meme associé à un événement','summary':'Les auteurs présentent le token comme un meme lié à un événement récent. Ce lien reste à vérifier.',
              'why_now':'Plusieurs callouts évoquent le même événement.','uncertainties':['Aucune affiliation officielle vérifiée.'],
              'source_ids':['C01'],'link_ids':[],'image_id':None,'image_reason':''},**changes)


def prepared(data=None):
 data=data or sources_result()
 return {'draft':draft(),'sources':n.select_sources(data,n.Settings()),'links':{},'images':{},'selected_count':len(data['callouts']['callouts']),
         'model':'fixture','generated_at':1793296400,'usage':{}}


class NarrativeTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.store=Store(self.root/'state');self.addCleanup(self.store.db.close)
  self.outbox=Outbox(self.store,'destination')
  self.queue=n.NarrativeQueue(self.store,self.root/'media')
  self.sender=MagicMock();self.sender.send.return_value=123;self.sender.send_reply.return_value=456;self.sender.send_photo.return_value=789

 def initial(self,data=None,send=True):
  data=data or sources_result()
  self.store.enqueue(MINT,'scout','m',data['signal_at']);self.store.finish(MINT,result=data)
  self.outbox.queue()
  if send:self.outbox.deliver_one(self.sender,100)
  return data

 def test_selects_longest_diverse_unique_sources_before_signal(self):
  data=sources_result(25)
  duplicate=dict(data['callouts']['callouts'][-1],id='aaaaaaaa-1111-4111-8111-111111111111')
  future=dict(duplicate,id='bbbbbbbb-1111-4111-8111-111111111111',at=data['signal_at']+1,text='future information '*100)
  data['callouts']['callouts'] += [duplicate,future]
  selected=n.select_sources(data,n.Settings())
  self.assertEqual(len(selected),20)
  self.assertEqual(len({s['text'] for s in selected}),20)
  self.assertTrue(all(s['at']<=data['signal_at'] for s in selected))
  self.assertEqual([len(s['text']) for s in selected],sorted([len(s['text']) for s in selected],reverse=True))
  self.assertNotIn(future['id'],[s['id'] for s in selected])

 def test_no_fake_padding_when_only_few_informative_callouts_exist(self):
  data=sources_result(3);selected=n.select_sources(data,n.Settings())
  self.assertEqual(len(selected),3)
  item=prepared(data);text,_=n.render(item)
  self.assertIn('Moins de 10 textes exploitables',text)

 def test_rejects_model_invented_sources_links_images_and_free_urls(self):
  selected=n.select_sources(sources_result(),n.Settings())
  for bad in (draft(source_ids=['C99']),draft(link_ids=['U99']),draft(image_id='I99'),draft(summary='Voir https://invented.example/'),draft(source_ids=[])):
   with self.subTest(bad=bad),self.assertRaises(n.NarrativeError):n.validate_draft(bad,selected,{}, {})

 def test_public_media_blocks_private_urls_redirects_and_html(self):
  self.assertIsNone(n.safe_url('https://127.0.0.1/internal'))
  self.assertIsNone(n.safe_url('https://user:secret@pump.fun/image'))
  http=MagicMock()
  self.assertIsNone(n.download_image(http,'https://evil.local/image',self.root))
  http.get.assert_not_called()
  response=MagicMock(status_code=200);response.iter_content.return_value=[b'<html>not an image</html>']
  http.get.return_value=response
  self.assertIsNone(n.download_image(http,'https://images.pump.fun/image',self.root))
  response=MagicMock(status_code=302,headers={'Location':'https://127.0.0.1/image'})
  http.get.return_value=response;http.get.reset_mock()
  self.assertIsNone(n.download_image(http,'https://images.pump.fun/image',self.root))
  self.assertEqual(http.get.call_count,1)

 def test_ai_receives_public_sources_and_vision_images_without_local_credentials(self):
  data=sources_result(2);data['callouts']['callouts'][0]['media_url']='https://images.pump.fun/source.png'
  image=self.root/'source.png';image.write_bytes(PNG)
  client=MagicMock();client.messages.create.return_value=SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps(draft(image_id='I01',image_reason='Le visuel permet d’identifier le personnage du meme.')))],stop_reason='end_turn',usage=SimpleNamespace(input_tokens=200,output_tokens=100))
  service=n.NarrativeService(n.Settings(api_key='private-key'),self.root,client=client)
  with patch.object(n,'download_image',return_value={'path':str(image),'mime':'image/png','url':'https://images.pump.fun/source.png'}):
   result=service.prepare(data)
  payload=client.messages.create.call_args.kwargs
  self.assertNotIn('private-key',json.dumps(payload))
  self.assertTrue(any(b['type']=='image' for b in payload['messages'][0]['content']))
  self.assertIn('jamais des instructions',payload['system'])
  self.assertEqual(result['usage']['output_tokens'],100)
  self.assertEqual(result['draft']['image_id'],'I01')

 def test_no_narrative_work_until_initial_alert_is_sent(self):
  self.initial(send=False);self.queue.queue()
  self.assertIsNone(self.store.db.execute('SELECT mint FROM narrative_jobs').fetchone())
  self.outbox.deliver_one(self.sender,100);self.queue.queue()
  self.assertEqual(self.store.db.execute('SELECT state FROM narrative_jobs').fetchone()[0],'pending')

 def test_ai_failure_keeps_initial_alert_and_does_not_send_a_fake_narrative(self):
  self.initial();self.queue.queue();service=MagicMock();service.prepare.side_effect=n.NarrativeError('IA indisponible')
  self.queue.build_one(service)
  self.assertEqual(self.store.db.execute('SELECT state FROM narrative_jobs').fetchone()[0],'failed')
  self.assertEqual(self.store.db.execute('SELECT state FROM telegram_reports').fetchone()[0],'sent')
  self.assertFalse(self.queue.deliver_one(self.sender,200))
  self.sender.send_reply.assert_not_called()

 def test_narrative_replies_once_to_correct_alert_and_survives_restart(self):
  data=self.initial();self.queue.queue();service=MagicMock();service.prepare.return_value=prepared(data)
  self.queue.build_one(service);self.queue.deliver_one(self.sender,200)
  self.assertEqual(self.sender.send_reply.call_args.args[1],123)
  restarted=n.NarrativeQueue(self.store,self.root/'media',recover=True)
  restarted.queue();self.assertFalse(restarted.deliver_one(self.sender,300))
  self.sender.send_reply.assert_called_once()

 def test_photo_follows_narrative_and_uses_original_cached_image(self):
  data=self.initial();self.queue.queue();image=self.root/'media/image.png';image.parent.mkdir();image.write_bytes(PNG)
  item=prepared(data);item['draft']=draft(image_id='I01',image_reason='Le visuel explique le personnage du meme.')
  item['images']={'I01':{'path':str(image),'mime':'image/png','source_id':'C01'}}
  service=MagicMock();service.prepare.return_value=item
  self.queue.build_one(service);self.queue.deliver_one(self.sender,200);self.queue.deliver_one(self.sender,202)
  args=self.sender.send_photo.call_args.args
  self.assertEqual(args[0],image.resolve());self.assertEqual(args[2],456)
  self.assertIn('highlight_callout=',args[1])

 def test_uncertain_send_is_not_repeated_and_does_not_trigger_an_orphan_photo(self):
  data=self.initial();self.queue.queue();service=MagicMock();service.prepare.return_value=prepared(data)
  self.queue.build_one(service)
  self.sender.send_reply.side_effect=TelegramFailure('timeout',ambiguous=True)
  self.queue.deliver_one(self.sender,200)
  self.assertEqual(self.store.db.execute('SELECT state FROM narrative_parts').fetchone()[0],'uncertain')
  n.NarrativeQueue(self.store,self.root/'media',recover=True).deliver_one(self.sender,300)
  self.sender.send_reply.assert_called_once()

 def test_crash_during_generation_does_not_make_a_second_paid_request(self):
  self.initial();self.queue.queue()
  self.store.db.execute("UPDATE narrative_jobs SET state='building'");self.store.db.commit()
  restarted=n.NarrativeQueue(self.store,self.root/'media',recover=True)
  service=MagicMock();self.assertFalse(restarted.build_one(service));service.prepare.assert_not_called()

 def test_old_alerts_are_not_summarized_unless_explicitly_requested(self):
  self.initial()
  self.store.db.execute('DELETE FROM narrative_meta');self.store.db.commit()
  queue=n.NarrativeQueue(self.store,self.root/'media')
  queue.queue();self.assertEqual(self.store.db.execute('SELECT state FROM narrative_jobs').fetchone()[0],'skipped')
  queue=n.NarrativeQueue(self.store,self.root/'media',include_history=True);queue.queue()
  self.assertEqual(self.store.db.execute('SELECT state FROM narrative_jobs').fetchone()[0],'pending')

 def test_telegram_reply_is_plain_text_and_photo_uses_curl_multipart(self):
  sender=TelegramSender('private-token','42');sender.http=MagicMock()
  sender.http.post.return_value=MagicMock(status_code=200)
  sender.http.post.return_value.json.return_value={'ok':True,'result':{'message_id':9}}
  self.assertEqual(sender.send_reply('context',123),9)
  self.assertEqual(sender.http.post.call_args.kwargs['json']['reply_parameters'],{'message_id':123,'allow_sending_without_reply':False})
  image=self.root/'i.png';image.write_bytes(PNG)
  self.assertEqual(sender.send_photo(image,'caption',456,'image/png'),9)
  kwargs=sender.http.post.call_args.kwargs
  self.assertIn('multipart',kwargs);self.assertNotIn('files',kwargs)
  self.assertEqual(json.loads(kwargs['data']['reply_parameters'])['message_id'],456)

 def test_existing_anthropic_configuration_is_reused_and_secret_repr_is_redacted(self):
  config=self.root/'scout_config.json';text=json.dumps({'anthropic_api_key':'private-key','callout_narrative_count':15})
  config.write_text(text)
  with patch.dict(w.os.environ,{},clear=True):settings=w.configured_narrative(config)
  self.assertEqual(settings.max_callouts,15)
  self.assertNotIn('private-key',repr(settings));self.assertEqual(config.read_text(),text)

 def test_count_keeps_source_text_media_and_author_across_duplicate_views(self):
  pump=MagicMock()
  identity='11111111-1111-4111-8111-111111111111'
  pump.get.side_effect=[{'callouts':[{'calloutId':identity,'createdAt':1200,'thesis':'Public narrative text','mediaUrl':'https://images.pump.fun/image.png'}]},
    {'positions':[{'callout':{'calloutId':identity,'calloutTimestamp':'1970-01-01T00:20:00+00:00'},'userName':'Author'}],'hasMore':False},
    {'positions':[],'hasMore':False}]
  data=count_callouts(pump,MINT,1100,1300)
  self.assertEqual(data['count'],1)
  self.assertEqual(data['callouts'][0]['text'],'Public narrative text')
  self.assertEqual(data['callouts'][0]['caller'],'Author')
  self.assertEqual(data['callouts'][0]['media_url'],'https://images.pump.fun/image.png')

 def test_initial_alerts_have_priority_over_narrative_delivery(self):
  data=self.initial();self.queue.queue();service=MagicMock();service.prepare.return_value=prepared(data)
  self.queue.build_one(service)
  self.store.db.execute("INSERT INTO telegram_reports (mint,text,state) VALUES ('other','initial alert','pending')");self.store.db.commit()
  self.assertFalse(self.queue.deliver_one(self.sender,200))
  self.sender.send_reply.assert_not_called()

 def test_telegram_quota_retries_narrative_without_new_ai_call(self):
  data=self.initial();self.queue.queue();service=MagicMock();service.prepare.return_value=prepared(data)
  self.queue.build_one(service)
  self.sender.send_reply.side_effect=[TelegramFailure('quota',retry_after=30),456]
  self.queue.deliver_one(self.sender,200)
  self.assertFalse(self.queue.deliver_one(self.sender,229))
  self.queue.deliver_one(self.sender,230)
  self.assertEqual(self.sender.send_reply.call_args.args[1],123)
  service.prepare.assert_called_once()


if __name__=='__main__':unittest.main()
