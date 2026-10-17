"""Opt-in integration against this checkout's private native cluster only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]
HERE=ROOT/'scripts/cloud/bootstrap'
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('cloud_bootstrap_apply',HERE/'apply.py')
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)


@unittest.skipUnless(os.getenv('CLOUD_NATIVE_INTEGRATION')=='1','requires private cloud native services')
class NativeBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state=Path('/workspace/.everydayaione-dev/instances')/hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
        m=json.loads((state/'local.json').read_text())
        cls.connection=dict(host='127.0.0.1',port=m['pgport'],user='dev_admin',password=m['password'])
        with psycopg.connect(**cls.connection,dbname='postgres') as c:
            assert Path(c.execute('SHOW data_directory').fetchone()[0]).resolve()==(state/'pgdata').resolve()

    def database(self):
        name='cloud_dev_test_'+uuid4().hex
        with psycopg.connect(**self.connection,dbname='postgres',autocommit=True) as c:
            c.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
        return psycopg.connect(**self.connection,dbname=name,autocommit=True)

    def test_sources_and_artifacts_immutable(self):
        manifest=bootstrap.resources()
        for name,digest in manifest['original_source_checksums'].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest,name)

    def test_refuses_system_database_even_on_private_cluster(self):
        with psycopg.connect(**self.connection,dbname='postgres',autocommit=True) as c:
            with self.assertRaisesRegex(RuntimeError,'only accepts'):bootstrap.apply(c)

    def test_refuses_artifact_checksum_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for p in HERE.iterdir():
                if p.is_file():shutil.copyfile(p,folder/p.name)
            (folder/'seed.sql').write_text((folder/'seed.sql').read_text()+'\n-- unexpected drift\n')
            with patch.object(bootstrap,'HERE',folder):
                with self.assertRaisesRegex(RuntimeError,'checksum drift'):bootstrap.resources()

    def test_two_empty_databases_repeat_without_data_loss(self):
        with self.database() as a,self.database() as b:
            self.assertEqual(bootstrap.apply(a),'initialized-empty')
            self.assertEqual(bootstrap.apply(b),'initialized-empty')
            a.execute("INSERT INTO users(nickname,created_by) VALUES('Synthetic preserved','system')")
            self.assertEqual(bootstrap.apply(a),'verified-existing')
            self.assertEqual(a.execute('SELECT count(*) FROM users').fetchone()[0],1)
            self.assertEqual(b.execute('SELECT count(*) FROM users').fetchone()[0],0)
            self.assertTrue(bootstrap.verify(b))
            self.assertIsNone(a.execute("SELECT to_regclass('public.schema_migration_ledger')").fetchone()[0])

    def test_refuses_unknown_nonempty_database(self):
        with self.database() as c:
            c.execute('CREATE TABLE preserve_user_data(value TEXT)')
            c.execute("INSERT INTO preserve_user_data VALUES('synthetic-preserve')")
            with self.assertRaisesRegex(RuntimeError,'Refusing nonempty'):bootstrap.apply(c)
            self.assertEqual(c.execute('SELECT value FROM preserve_user_data').fetchone()[0],'synthetic-preserve')

    def test_failed_bootstrap_rolls_back_and_retries_safely(self):
        with self.database() as c,tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for p in HERE.iterdir():
                if p.is_file():shutil.copyfile(p,folder/p.name)
            seed=folder/'seed.sql';seed.write_text(seed.read_text()+'\nSELECT 1/0;\n')
            manifest=json.loads((folder/'manifest.json').read_text())
            manifest['artifacts']['seed.sql']=hashlib.sha256(seed.read_bytes()).hexdigest()
            (folder/'manifest.json').write_text(json.dumps(manifest))
            with patch.object(bootstrap,'HERE',folder):
                with self.assertRaises(psycopg.errors.DivisionByZero):bootstrap.apply(c)
            self.assertIsNone(c.execute("SELECT to_regclass('public.users')").fetchone()[0])
            self.assertIsNone(c.execute("SELECT to_regclass('cloud_development.bootstrap_state')").fetchone()[0])
            self.assertEqual(bootstrap.apply(c),'initialized-empty')

    def test_detects_column_and_privilege_drift(self):
        with self.database() as c:
            bootstrap.apply(c)
            c.execute('ALTER TABLE users ADD COLUMN unexpected_drift TEXT')
            with self.assertRaisesRegex(RuntimeError,'schema drift'):bootstrap.apply(c)
            c.execute('ALTER TABLE users DROP COLUMN unexpected_drift')
            bootstrap.verify(c)
            c.execute('GRANT UPDATE ON users TO authenticated')
            with self.assertRaisesRegex(RuntimeError,'table_privileges'):bootstrap.verify(c)

    def test_real_app_role_and_transaction_scope(self):
        values=dotenv_values(ROOT/'backend/.env')
        with psycopg.connect(values['DATABASE_URL']) as c:
            bootstrap.verify(c)
            self.assertEqual(c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=current_user').fetchone(),(False,False,False,False,False))
            self.assertEqual(c.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector").fetchone()[0],1.0)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():c.execute('SET ROLE dev_admin')
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():c.execute("UPDATE cloud_development.bootstrap_state SET identity='fake'")
            actor,org=str(uuid4()),str(uuid4())
            with c.transaction(force_rollback=True):
                c.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id',%s,true)",(actor,org))
                self.assertEqual(c.execute('SELECT tenant_actor_user_id()::text,tenant_org_id()::text').fetchone(),(actor,org))
            self.assertEqual(c.execute('SELECT tenant_actor_user_id(),tenant_org_id()').fetchone(),(None,None))


if __name__=='__main__':unittest.main(verbosity=2)
