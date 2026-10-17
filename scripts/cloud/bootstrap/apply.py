#!/usr/bin/env python3
"""Atomic, empty-database-only native DEV bootstrap. Never a production migrator."""
import hashlib
import json
from pathlib import Path
import re

import psycopg
from psycopg import sql

from catalog import catalog, fingerprint

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
ROLES=('everydayai','everydayai_owner','everydayai_runtime','everydayai_wecom_runtime',
       'everydayai_worker','everydayai_sync','everydayai_migrator',
       'everydayai_config_import_reader','authenticated','service_role')


def resources():
    manifest=json.loads((HERE/'manifest.json').read_text())
    for name,digest in manifest['artifacts'].items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Bootstrap artifact checksum drift: '+name)
    return manifest


def verify(connection):
    manifest=resources()
    with connection.transaction():
        marker=connection.execute('SELECT identity,fingerprint FROM cloud_development.bootstrap_state').fetchone()
        if marker!=(manifest['bootstrap_identity'],manifest['catalog_fingerprint']):
            raise RuntimeError('Unknown or incomplete development bootstrap identity')
        actual=catalog(connection)
        if fingerprint(actual)!=manifest['catalog_fingerprint']:
            expected=json.loads((HERE/'catalog.json').read_text())
            diagnostic=HERE.parents[2]/'.cloud-dev/catalog-actual.json'
            diagnostic.parent.mkdir(exist_ok=True)
            diagnostic.write_text(json.dumps(actual,indent=2))
            categories=[name for name in expected if expected[name]!=actual[name]]
            raise RuntimeError('Development schema drift: '+','.join(categories))
    return True


def apply(connection):
    """Caller has already verified this is its own private loopback cluster.

    Everything, including the separate DEV receipt, commits together. On failure
    the database stays empty and may retry. Nonempty unmarked DBs are refused.
    No legacy schema_migration_ledger is created or populated.
    """
    manifest=resources()
    with connection.transaction():
        expected=Path('/workspace/.everydayaione-dev/instances')/hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:16]/'pgdata'
        identity=connection.execute('SELECT current_database(),current_user,host(inet_server_addr())').fetchone()
        if not identity[0].startswith('cloud_dev_') or identity[1]!='dev_admin' or identity[2]!='127.0.0.1':
            raise RuntimeError('Bootstrap only accepts this task private native development database')
        if Path(connection.execute('SHOW data_directory').fetchone()[0]).resolve()!=expected.resolve():
            raise RuntimeError('Private cluster filesystem identity mismatch')
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('cloud-native-development-v1'))")
        marked=connection.execute("SELECT to_regclass('cloud_development.bootstrap_state')").fetchone()[0]
        if marked:
            verify(connection)
            return 'verified-existing'
        objects=connection.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'""").fetchone()[0]
        namespaces=connection.execute("SELECT count(*) FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname NOT IN ('public','information_schema')").fetchone()[0]
        if objects or namespaces:
            raise RuntimeError('Refusing nonempty database without this development bootstrap receipt')
        setting_names=['statement_timeout','lock_timeout','idle_in_transaction_session_timeout',
            'transaction_timeout','client_encoding','standard_conforming_strings','search_path',
            'check_function_bodies','xmloption','client_min_messages','row_security',
            'default_tablespace','default_table_access_method']
        original_settings={name:connection.execute('SELECT current_setting(%s)',(name,)).fetchone()[0] for name in setting_names}
        connection.execute((HERE/'schema.sql').read_text())
        connection.execute((HERE/'seed.sql').read_text())
        connection.execute('SET check_function_bodies=on; SET row_security=on; RESET ROLE')
        for name,value in original_settings.items():
            connection.execute('SELECT set_config(%s,%s,false)',(name,value))
        actual=catalog(connection)
        if fingerprint(actual)!=manifest['catalog_fingerprint']:
            expected=json.loads((HERE/'catalog.json').read_text())
            (HERE.parents[2]/'.cloud-dev/catalog-actual.json').write_text(json.dumps(actual,indent=2))
            raise RuntimeError('Restored catalog differs: '+','.join(k for k in actual if actual[k]!=expected[k]))
        connection.execute('CREATE SCHEMA cloud_development')
        connection.execute('REVOKE ALL ON SCHEMA cloud_development FROM PUBLIC')
        connection.execute('CREATE TABLE cloud_development.bootstrap_state(identity TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        connection.execute('INSERT INTO cloud_development.bootstrap_state(identity,fingerprint) VALUES(%s,%s)',(manifest['bootstrap_identity'],manifest['catalog_fingerprint']))
        connection.execute('GRANT USAGE ON SCHEMA cloud_development TO everydayai')
        connection.execute('GRANT SELECT ON cloud_development.bootstrap_state TO everydayai')
    return 'initialized-empty'


def prepare(root: Path, name='cloud_dev_app_v1'):
    if not re.fullmatch(r'cloud_dev_[a-z0-9_]+',name):
        raise RuntimeError('Only private cloud_dev_* databases can be prepared')
    state=Path('/workspace/.everydayaione-dev/instances')/hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    m=json.loads((state/'local.json').read_text())
    with psycopg.connect(host='127.0.0.1',port=m['pgport'],user='dev_admin',password=m['password'],dbname='postgres',autocommit=True) as c:
        data=Path(c.execute('SHOW data_directory').fetchone()[0]).resolve()
        if data!=(state/'pgdata').resolve():raise RuntimeError('Private cluster data directory identity mismatch')
        for role in ROLES:
            found=c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
            if found and any(found):raise RuntimeError('Unexpected elevated role in private development cluster')
            if not found:c.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(role)))
        if not c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    with psycopg.connect(host='127.0.0.1',port=m['pgport'],user='dev_admin',password=m['password'],dbname=name,autocommit=True) as c:
        result=apply(c)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--database',default='cloud_dev_app_v1')
    args=parser.parse_args()
    print(prepare(HERE.parents[2],args.database))
