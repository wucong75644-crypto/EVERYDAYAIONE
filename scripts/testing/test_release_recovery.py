"""Recovery cannot erase a live/foreign lock or manufacture successful provenance."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('recovery',ROOT/'deploy/recover-release.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Path(self.temp.name)/'app';self.app.mkdir()
        self.lock=Path(str(self.app)+'.release-lock');self.lock.mkdir()
        (self.lock/'owner').write_text('old-owner\n')
        (self.app/'backend').mkdir();(self.app/'backend/main.py').write_text('verified')
        self.manifest={'backend/main.py':hashlib.sha256(b'verified').hexdigest()}
        self.addCleanup(patch.stopall)
        patch.object(r.os,'geteuid',return_value=0).start()
        self.writers=patch.object(r,'active_writers',return_value=[]).start()
        patch.object(r,'database_snapshot',return_value=[['m.sql','abc']]).start()
        patch.object(r.subprocess,'run',return_value=SimpleNamespace(returncode=0)).start()
        patch.object(r.fcntl,'flock').start()
    def run_recovery(self):
        r.recover('old-owner','a'*40,'codex/task/canvas',self.manifest,self.app)
    def test_success_archives_lock_and_creates_only_recovery_receipt(self):
        self.run_recovery()
        self.assertFalse(self.lock.exists())
        self.assertFalse((self.app/'.release-provenance').exists())
        receipt=json.loads((self.app/'.release-recovery').read_text())
        self.assertEqual(receipt['status'],'recovered_not_deployed')
        self.assertEqual((Path(receipt['evidence'])/'release-lock/owner').read_text(),'old-owner\n')
        self.assertTrue((Path(receipt['evidence'])/'files.tar.gz').exists())
    def test_live_writer_keeps_lock(self):
        self.writers.return_value=[123]
        with self.assertRaisesRegex(RuntimeError,'writer'):self.run_recovery()
        self.assertTrue(self.lock.exists());self.assertFalse((self.app/'.release-recovery').exists())
    def test_writer_appearing_during_snapshot_keeps_lock(self):
        self.writers.side_effect=[[],[123]]
        with self.assertRaisesRegex(RuntimeError,'Writer appeared'):self.run_recovery()
        self.assertTrue(self.lock.exists())
        self.assertFalse((self.app/'.release-recovery').exists())
    def test_foreign_owner_keeps_lock(self):
        (self.lock/'owner').write_text('new-owner')
        with self.assertRaisesRegex(RuntimeError,'owner changed'):self.run_recovery()
        self.assertTrue(self.lock.exists())
    def test_foreign_attempt_rejected(self):
        (self.lock/'attempt').write_text('commit='+'b'*40+'\nsource=codex/task/other\n')
        with self.assertRaisesRegex(RuntimeError,'recorded attempt'):self.run_recovery()
        self.assertTrue(self.lock.exists())
    def test_source_drift_rejected(self):
        (self.app/'backend/main.py').write_text('unknown')
        with self.assertRaisesRegex(RuntimeError,'differs'):self.run_recovery()
        self.assertTrue(self.lock.exists())
    def test_database_failure_keeps_lock(self):
        with patch.object(r,'database_snapshot',side_effect=RuntimeError('Database identity mismatch')):
            with self.assertRaisesRegex(RuntimeError,'identity'):self.run_recovery()
        self.assertTrue(self.lock.exists())
    def test_unready_service_keeps_lock(self):
        with patch.object(r.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError,'not active'):self.run_recovery()
        self.assertTrue(self.lock.exists())
    def test_other_task_provenance_rejected(self):
        (self.app/'.release-provenance').write_text('commit='+'b'*40+'\nsource=codex/task/other\n')
        with self.assertRaisesRegex(RuntimeError,'Another task'):self.run_recovery()
        self.assertTrue(self.lock.exists())
    def test_symlink_and_path_escape_rejected(self):
        for path in ('backend/../secret','/etc/passwd'):
            with self.assertRaisesRegex(RuntimeError,'Unsafe'):r.validate_manifest(self.app,{path:'a'*64})
        (self.app/'backend/main.py').unlink();(self.app/'backend/main.py').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(RuntimeError,'unsafe|missing'):self.run_recovery()

if __name__=='__main__':unittest.main()
