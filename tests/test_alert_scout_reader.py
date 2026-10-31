import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from alert_scout_lecture_discord import configure_reader


class ReaderOnlyTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.module=SimpleNamespace(cfg={},seen={},JOURNAL=str(Path(self.temp.name)/'journal'),
     scout=MagicMock(),journal=MagicMock(),resume_journal=MagicMock(),main=MagicMock(),
     load_config=lambda path:self.load(path),on_message=lambda msg,source:msg)
  self.config=Path(self.temp.name)/'config.json'
  self.config.write_text(json.dumps({'telegram_bot_token':'private-token','anthropic_api_key':'private-key'}))
  self.reader=configure_reader(self.module)

 def load(self,path):
  self.module.cfg.update(json.loads(Path(path).read_text()))
  return 'rpc-selector'

 def test_disables_old_notifications_without_touching_config_on_disk(self):
  before=self.config.read_text()
  self.assertEqual(self.reader.load_config(self.config),'rpc-selector')
  self.assertEqual(self.reader.cfg['telegram_bot_token'],'')
  self.assertEqual(self.reader.cfg['anthropic_api_key'],'')
  self.assertEqual(self.reader.tg('sendMessage',{'text':'legacy card'}),{})
  self.assertEqual(self.config.read_text(),before)

 def test_captures_only_discord_signals_without_original_analysis(self):
  self.reader.scout('mint',1200,'discord','text')
  self.reader.scout('manual',1200,'telegram','text')
  self.reader.scout('replay',1200,'discord','text',replay=True)
  self.reader.journal.assert_called_once()
  event=self.reader.journal.call_args.args[0]
  self.assertEqual(event['type'],'alert')
  self.assertEqual(event['t_alert'],1200)

 def test_original_snowflake_time_overrides_extension_fallback_clock(self):
  at_ms=1793296320123
  identity=str((at_ms-1420070400000)<<22)
  data={'id':'chat-messages-111111111111111111-'+identity,'channel':'111111111111111111','ts':at_ms+30000}
  received=self.reader.on_message(data,'discord')
  self.assertEqual(received['ts'],at_ms)
  self.assertNotEqual(received['ts'],data['ts'])
  self.assertIsInstance(self.reader.on_message(dict(data,channel='222222222222222222'),'discord'),str)
  self.assertEqual(self.reader.on_message(data,'telegram'),'source ignorée')


if __name__=='__main__':unittest.main()
