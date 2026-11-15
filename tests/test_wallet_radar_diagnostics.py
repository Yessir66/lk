import contextlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wallet_radar_diagnostics as d
import wallet_alerts_ws as w
from discord_token_analysis import RemoteError
from wallet_radar_paper import SOL

MINT='BLw4fXAc9P7NYhnRH9GyccXKUmvpUSiqc67BGbanxcDp'
SIGNATURE='C'*88


class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.log=self.root/'paper.jsonl';self.state=self.root/'state.sqlite3'
        rows=[{'type':'paper_session','session_id':'test','limit':30},
              {'type':'paper_decision','session_id':'test','mint':MINT,'code':'DONNEES_INSUFFISANTES',
               'message':'Verification des comptes de position indisponible.','initial_signature':SIGNATURE}]
        self.log.write_text('\n'.join(map(json.dumps,rows))+'\n')

    def run_diag(self,rpc):
        output=StringIO()
        with contextlib.redirect_stdout(output):d.diagnose(self.log,self.state,rpc)
        return output.getvalue()

    def test_provider_codes_are_preserved_without_private_message_or_rpc_body(self):
        rpc=MagicMock()
        rpc.call.side_effect=[RemoteError('https://rpc/?api-key=secret',status=429,rpc_code=-32000),None]
        text=self.run_diag(rpc)
        self.assertIn('HTTP 429 RPC -32000',text);self.assertNotIn('secret',text)
        self.assertEqual([call.args[0] for call in rpc.call.call_args_list],['getTokenAccountsByOwner','getTransaction'])

    def test_binary_accounts_and_pumpswap_are_identified_without_mutating_journal(self):
        rpc=MagicMock()
        rpc.call.side_effect=[{'value':[{'account':{'data':['private-unprinted-body','base64']}}]},
            {'slot':5,'meta':{'err':None,'logMessages':[f'Program {d.SWAP} invoke [1]',f'Program {d.SWAP} success']}}]
        before=self.log.read_bytes();text=self.run_diag(rpc)
        self.assertIn('binaire base64',text);self.assertIn('PumpSwap',text)
        self.assertNotIn('private-unprinted-body',text);self.assertEqual(self.log.read_bytes(),before)

    def test_missing_signature_is_retrieved_read_only_from_state(self):
        db=sqlite3.connect(self.state)
        db.execute('CREATE TABLE events (wallet TEXT,state TEXT,trades TEXT,first_seen REAL,signature TEXT)')
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(d.BBIG,'done',json.dumps([{'mint':MINT,'kind':'buy'}]),1000,SIGNATURE))
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(d.BBIG,'done',json.dumps([{'mint':MINT,'kind':'sell'}]),1001,'D'*88))
        db.commit();db.close()
        self.assertEqual(d.find_signature({'at':1001},MINT,self.state),SIGNATURE)

    def test_no_failed_samples_makes_no_rpc_calls(self):
        self.log.write_text('')
        rpc=MagicMock();text=self.run_diag(rpc)
        rpc.call.assert_not_called();self.assertIn('Aucun cas incomplet',text)

    def test_check_remains_offline_even_if_diagnostics_flag_is_also_supplied(self):
        config=self.root/'config.json';state=self.root/'unused.sqlite3'
        config.write_text(json.dumps({'helius_api_key':'never-print-this','wallets':{'BBIG BOSS':MINT}}))
        output=StringIO()
        with patch.object(sys,'argv',['wallet_alerts_ws.py','--config',str(config),'--state',str(state),'--check','--paper-diagnose']),\
             patch.object(w,'ReadRPC') as rpc,contextlib.redirect_stdout(output):
            w.main()
        rpc.assert_not_called();self.assertFalse(state.exists());self.assertNotIn('never-print-this',output.getvalue())
