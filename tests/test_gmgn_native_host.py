"""Exercise real native-message framing; optional PowerShell runtime, no network/browser."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

PWSH=os.getenv('GMGN_PWSH') or shutil.which('pwsh')
SCRIPT=Path(__file__).resolve().parents[1]/'windows'/'GMGN_NATIVE_HOST.ps1'


@unittest.skipUnless(PWSH, 'PowerShell is optional for Python tests')
class NativeHostTest(unittest.TestCase):
    def run_host(self,lines,requests=None,session='a'*32):
        with tempfile.TemporaryDirectory(prefix='gmgn-native-test-') as folder:
            base=Path(folder)/'WalletAlertsGMGN';base.mkdir()
            (base/'bridge-session.txt').write_text(session)
            (base/('gmgn-events-'+session+'.jsonl')).write_text(lines)
            env=dict(os.environ,LOCALAPPDATA=folder,XDG_CACHE_HOME=folder,XDG_CONFIG_HOME=folder,XDG_DATA_HOME=folder)
            packets=[]
            for message in requests or [{'action':'poll'},{'action':'poll'}]:
                body=json.dumps(message).encode();packets.append(struct.pack('<I',len(body))+body)
            proc=subprocess.run([PWSH,'-NoProfile','-File',str(SCRIPT)],input=b''.join(packets),
                                capture_output=True,timeout=15,env=env,check=True)
            data=proc.stdout;messages=[]
            while data:
                self.assertGreaterEqual(len(data),4)
                size=struct.unpack('<I',data[:4])[0];self.assertGreater(size,0)
                self.assertGreaterEqual(len(data),size+4)
                messages.append(json.loads(data[4:4+size]));data=data[4+size:]
            return messages

    def test_reads_multiple_events_once_and_ignores_an_incomplete_last_line(self):
        messages=self.run_host('{"mint":"first"}\n{"mint":"second"}\n{"mint":"partial"')
        self.assertEqual(messages,[{'ok':True,'events':[{'mint':'first'},{'mint':'second'}]},
                                   {'ok':True,'events':[]}])

    def test_malformed_journal_lines_do_not_corrupt_binary_protocol(self):
        messages=self.run_host('bad-json\n{"mint":"valid"}\n')
        self.assertEqual(messages[0],{'ok':True,'events':[{'mint':'valid'}]})

    def test_unknown_requests_are_rejected_without_consuming_events(self):
        messages=self.run_host('{"mint":"valid"}\n',[{'action':'execute'},{'action':'poll'}])
        self.assertEqual(messages[0],{'ok':False,'events':[]})
        self.assertEqual(messages[1]['events'],[{'mint':'valid'}])

    def test_invalid_session_name_cannot_select_other_paths(self):
        # A legal filename but not a session identifier.
        messages=self.run_host('{"mint":"valid"}\n',session='not-a-session')
        self.assertEqual(messages[0],{'ok':False,'events':[]})
