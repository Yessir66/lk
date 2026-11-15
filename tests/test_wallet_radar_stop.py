"""Scoped process control: own test processes only, never the deployed watcher."""
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from io import StringIO

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wallet_alerts_ws as w


class StopMessageTest(unittest.TestCase):
    def test_known_lock_error_has_actionable_message_without_leaking_errors(self):
        message=w.startup_failure(w.RemoteError('Cette analyse Discord est déjà lancée pour ce journal ; fermer son ancien terminal'))
        self.assertIn('Choisir 5',message)
        self.assertNotIn('Discord',message)
        self.assertNotIn('secret',w.startup_failure(w.RemoteError('https://rpc/?api-key=secret')))


@unittest.skipUnless(os.name=='posix' and Path('/proc').is_dir(),'Linux process control')
class ScopedStopTest(unittest.TestCase):
    def test_absolute_relative_and_other_script_arguments_are_distinguished(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);script=root/'scripts/wallet_alerts_ws.py'
            script.parent.mkdir();script.write_text('')
            proc=root/'proc';proc.mkdir()
            cases={9000001:['python','-u',str(script)],9000002:['python','scripts/wallet_alerts_ws.py'],
                   9000003:['python','-c',str(script)],9000004:['bash','-c',str(script)],
                   9000005:['python',str(root/'other/wallet_alerts_ws.py')]}
            for pid,args in cases.items():
                entry=proc/str(pid);entry.mkdir();(entry/'cwd').symlink_to(root,target_is_directory=True)
                (entry/'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in args)+b'\0')
            self.assertEqual(w.watcher_pids(script,proc),[9000001,9000002])

    def test_graceful_stop_leaves_an_unrelated_python_process_running(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'wallet_alerts_ws.py';other=root/'other.py'
            body='import time\ntry: time.sleep(60)\nexcept KeyboardInterrupt: pass\n'
            target.write_text(body);other.write_text(body)
            children=[subprocess.Popen([sys.executable,'-u',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                      for path in (target,other)]
            try:
                deadline=time.monotonic()+3
                while children[0].pid not in w.watcher_pids(target) and time.monotonic()<deadline:time.sleep(.01)
                self.assertIn(children[0].pid,w.watcher_pids(target))
                output=StringIO()
                with contextlib.redirect_stdout(output):w.stop_watchers(target,timeout=3)
                children[0].wait(timeout=3)
                self.assertIsNone(children[1].poll())
                self.assertIn('Donnees conservees',output.getvalue())
            finally:
                for child in children:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=3)

    def test_no_matching_process_is_a_successful_noop(self):
        with tempfile.TemporaryDirectory() as folder,contextlib.redirect_stdout(StringIO()) as output:
            w.stop_watchers(Path(folder)/'never-launched.py',timeout=.1)
        self.assertIn('Aucune surveillance',output.getvalue())
