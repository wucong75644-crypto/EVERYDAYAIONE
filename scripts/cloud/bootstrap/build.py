#!/usr/bin/env python3
"""Build/audit a NEW native development baseline; never edits old migrations.

This offline maintenance tool uses only repository SQL and a private local cluster.
It is not the production migration executor and never writes its ledger.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import psycopg
from psycopg import sql
from pglast import parse_sql
from pglast.stream import RawStream

ROOT = Path(__file__).resolve().parents[3]
NATIVE = Path('/workspace/.everydayaione-dev')


def statements(text):
    # pglast's Python offsets preserve the exact source/function bodies. Reprinting
    # an AST is not safe: pglast 8.6 misplaces NULLS NOT DISTINCT after WHERE.
    data=text
    result=[]
    for node in parse_sql(text):
        if type(node.stmt).__name__ in ('TransactionStmt','VariableSetStmt'):
            result.append(RawStream()(node.stmt))
        else:
            end=node.stmt_location+node.stmt_len if node.stmt_len else len(data)
            result.append(data[node.stmt_location:end].strip().rstrip(';'))
    return result


def sources():
    p = ROOT / 'docs/database/supabase_init.sql'
    yield str(p.relative_to(ROOT)) + ':native-structure', p.read_text().split('-- 11. RLS')[0]
    for p in sorted((ROOT / 'docs/database/migrations').glob('*.sql')):
        text = p.read_text()
        if p.name.startswith(('006_', '007_')):
            left, right = text.split('-- 启用行级安全', 1)
            text = left + right.split('-- 添加注释', 1)[1]
        yield str(p.relative_to(ROOT)), text
    # Missing from the standalone legacy chain but defined by the native baseline.
    text = (ROOT / 'deploy/init-database.sql').read_text()
    table = 'CREATE TABLE IF NOT EXISTS erp_sync_dead_letter'
    yield 'deploy/init-database.sql:erp_sync_dead_letter', table + text.split(table)[1].split(';')[0] + ';'
    yield 'scripts/cloud/bootstrap/native-prerequisites.sql', (Path(__file__).parent / 'native-prerequisites.sql').read_text()
    for p in sorted((ROOT / 'backend/migrations').glob('*.sql')):
        if p.name == '094_varchar_to_text.sql':
            yield '094 native missing predecessor: target TEXT shop_user_id', 'ALTER TABLE erp_document_items ADD COLUMN shop_user_id TEXT; ALTER TABLE erp_document_items_archive ADD COLUMN shop_user_id TEXT;'
            yield 'scripts/cloud/bootstrap/094-predecessors.sql', (Path(__file__).parent/'094-predecessors.sql').read_text()
        yield str(p.relative_to(ROOT)), p.read_text()


def build(name):
    state = NATIVE / 'instances' / hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    m = json.loads((state / 'local.json').read_text())
    conn = dict(host='127.0.0.1', port=m['pgport'], user='dev_admin', password=m['password'])
    with psycopg.connect(**conn, dbname='postgres', autocommit=True) as c:
        if Path(c.execute('SHOW data_directory').fetchone()[0]).resolve()!=(state/'pgdata').resolve():
            raise RuntimeError('Refusing an unrecognized local cluster')
        if c.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone():
            raise RuntimeError('Never overwrite an existing build database')
        for role in ['everydayai', 'everydayai_owner', 'everydayai_runtime', 'everydayai_wecom_runtime', 'everydayai_worker', 'everydayai_sync', 'everydayai_migrator', 'everydayai_config_import_reader', 'authenticated', 'service_role']:
            if not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                c.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(role)))
        c.execute(sql.SQL('CREATE DATABASE {} OWNER everydayai').format(sql.Identifier(name)))
    report = {'database': name, 'sources': [], 'steps': []}
    statement = ''
    out = ROOT / '.cloud-dev' / 'builds' / name
    out.mkdir(parents=True, exist_ok=False)
    with psycopg.connect(**conn, dbname=name, autocommit=True) as c:
        for ext in ['uuid-ossp', 'vector', 'pg_trgm', 'ltree']:
            c.execute(sql.SQL('CREATE EXTENSION {}').format(sql.Identifier(ext)))
        c.execute('GRANT USAGE,CREATE ON SCHEMA public TO everydayai,everydayai_owner')
        c.execute('SET ROLE everydayai')
        try:
            for identity, text in sources():
                report['sources'].append({'source': identity, 'sha256': hashlib.sha256(text.encode()).hexdigest()})
                parsed = statements(text)
                # 039 requests this same constraint drop, but after DROP INDEX.
                if '039_multi_tenant' in identity:
                    parsed.insert(0, 'ALTER TABLE erp_sync_state DROP CONSTRAINT IF EXISTS erp_sync_state_sync_type_key')
                if '156_governance' in identity:
                    c.execute('RESET ROLE')
                    c.execute('GRANT SELECT,INSERT,UPDATE,DELETE,REFERENCES ON ALL TABLES IN SCHEMA public TO everydayai_owner')
                    c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO everydayai_owner')
                if '266_mcp_org_connector' in identity:
                    # This migration and verify_migration_executor declare these
                    # registries legacy-owned; reproduce that ownership contract.
                    c.execute('RESET ROLE')
                    for table in ('configuration_definitions','configuration_bundle_definitions'):
                        c.execute(sql.SQL('ALTER TABLE {} OWNER TO everydayai').format(sql.Identifier(table)))
                        c.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE,REFERENCES ON {} TO everydayai_owner').format(sql.Identifier(table)))
                if '271_mcp_application_privileges' in identity:
                    c.execute('RESET ROLE')
                    for table in ('configuration_entries','secret_records','governance_audit_log','configuration_policies'):
                        c.execute(sql.SQL('ALTER TABLE {} OWNER TO everydayai').format(sql.Identifier(table)))
                if '272_mcp_configuration_helper' in identity:
                    c.execute('RESET ROLE')
                    for row in c.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname=ANY(%s)", (['_validate_configuration_material','_write_configuration_entry','_configuration_scope_id','_project_configuration_entry','_resolve_effective_configuration_item','_resolve_configuration_bundle'],)).fetchall():
                        c.execute(sql.SQL('ALTER FUNCTION {} OWNER TO everydayai').format(sql.SQL(row[0])))
                if '281_ecommerce_recovery' in identity:
                    # 279 creates these tables under the restricted schema owner;
                    # 281 has no in-file role switch. Preserve that same owner.
                    c.execute('SET ROLE everydayai_owner')
                # Most scripts need one transaction for SET LOCAL ROLE. Explicit
                # BEGIN/COMMIT are represented by this context, not nested SQL.
                concurrent = any('CREATE INDEX CONCURRENTLY' in s for s in parsed)
                context = c.transaction() if not concurrent else __import__('contextlib').nullcontext()
                with context:
                    for statement in parsed:
                        if statement in ('BEGIN', 'COMMIT', 'START TRANSACTION'):
                            continue
                        # Qualify metadata comments; preserve every function body.
                        match = re.search(r"\bCOMMENT\s+ON\s+FUNCTION\s+([\w.]+)\s+IS\s+(.*)\Z", statement, re.S)
                        if match:
                            fn, comment = match.groups()
                            row = c.execute("SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname=%s ORDER BY p.oid DESC LIMIT 1", (fn.split('.')[-1],)).fetchone()
                            if not row: raise RuntimeError('Comment references undefined function: ' + fn)
                            statement = 'COMMENT ON FUNCTION ' + row[0] + ' IS ' + comment
                        index = len(report['steps'])
                        (out / f'{index:04}.sql').write_text(statement + ';\n')
                        c.execute(statement)
                        report['steps'].append({'source': identity, 'statement': index, 'sha256': hashlib.sha256(statement.encode()).hexdigest()})
                # RESET ROLE in newer scripts returns to migration administrator.
                if 'RESET ROLE' not in parsed:
                    c.execute('SET ROLE everydayai')
            report['status'] = 'built'
        except Exception as error:
            report['status'] = 'failed'
            report['error'] = {'source': identity, 'statement': statement[:160], 'type': type(error).__name__, 'message': str(error).split('\n')[0]}
        (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('steps','sources')}))
    return 0 if report['status']=='built' else 1


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('database')
    raise SystemExit(build(parser.parse_args().database))
