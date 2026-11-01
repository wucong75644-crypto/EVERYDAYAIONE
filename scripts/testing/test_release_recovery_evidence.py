"""Pre-mutation recovery requires a stopped publisher and exact private stage log."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('recovery_evidence',ROOT/'deploy/recovery-evidence.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)
        self.git('init','-q');self.git('config','user.email','test@invalid');self.git('config','user.name','Test')
        (self.repo/'backend').mkdir();(self.repo/'backend/main.py').write_text('baseline')
        self.git('add','.');self.git('commit','-qm','baseline')
        self.previous=self.git('rev-parse','HEAD').strip()
        self.git('update-ref','refs/remotes/origin/main',self.previous)
        (self.repo/'backend/main.py').write_text('candidate')
        (self.repo/'backend/new.py').write_text('new')
        self.git('add','.');self.git('commit','-qm','candidate')
        self.failed=self.git('rev-parse','HEAD').strip()
        logs=self.repo/'.git/release-logs';logs.mkdir()
        self.log=logs/('20261029T114652Z-'+self.failed+'-99999999.log')
        self.log.write_text('DEPLOY_STAGE stage=preflight\nDEPLOY_STAGE stage=tests_and_build\n')
        self.log.chmod(0o600)
        original=subprocess.check_output
        def query(args,**kwargs):
            return '' if args[0]=='ps' else original(args,**kwargs)
        self.addCleanup(patch.stopall)
        patch.object(r.subprocess,'check_output',side_effect=query).start()
    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.repo),*args],text=True)
    def evidence(self):
        return r.evidence(self.repo,'20261029114642-99999999-11-22',self.failed,self.previous)
    def test_stable_baseline_is_pinned_and_new_files_must_be_absent(self):
        proof=self.evidence()
        self.assertEqual(proof['recovery_mode'],'pre_mutation')
        self.assertEqual(proof['files']['backend/main.py'],hashlib.sha256(b'baseline').hexdigest())
        self.assertIsNone(proof['files']['backend/new.py'])
    def test_remote_stage_retains_original_candidate_recovery(self):
        self.log.write_text(self.log.read_text()+'DEPLOY_STAGE stage=remote_mutation\n')
        self.assertNotIn('recovery_mode',self.evidence())
    def test_live_original_publisher_or_public_log_is_rejected(self):
        with patch.object(r.os,'kill',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'publisher may still'):self.evidence()
        self.log.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,'permissions invalid'):self.evidence()
    def test_missing_production_identity_cannot_use_pre_mutation_path(self):
        with self.assertRaisesRegex(RuntimeError,'unchanged production commit'):
            r.evidence(self.repo,'20261029114642-99999999-11-22',self.failed,'')

if __name__=='__main__':unittest.main()
