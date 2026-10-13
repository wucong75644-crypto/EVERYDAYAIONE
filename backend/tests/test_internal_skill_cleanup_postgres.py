"""Cleanup uses real forced RLS without touching workflow grants or documents."""
from dataclasses import replace
import json
import os
from pathlib import Path

import psycopg
import pytest

from services.skills.contracts import PackageCreate, revision_path
from tests.test_skill_catalog_postgres import (  # noqa: F401
    MIGRATIONS, SKILL_TABLES, database, postgres_socket, published,
)
from tests.test_skill_storage import PACKAGE, publication


@pytest.mark.parametrize('deleted_fixture', [False, True])
@pytest.mark.parametrize('table_owner', ['everydayai', 'everydayai_owner'])
def test_cleanup_is_narrow_idempotent_and_removes_temporary_policies(published, table_owner, deleted_fixture):
    conn, repository, a, b, _, _, storage = published
    conn.execute('RESET ROLE')
    for name in ('259_skill_authoring.sql', '260_skill_reenable.sql', '261_skill_safe_removal.sql'):
        conn.execute((MIGRATIONS / name).read_text())
    # Optional read-only production schema snapshot contains no business rows.
    schema_path = os.getenv('SKILL_CLEANUP_PRODUCTION_SCHEMA')
    if schema_path:
        schema = json.loads(Path(schema_path).read_text())
        conn.execute('CREATE TABLE IF NOT EXISTS users(id uuid PRIMARY KEY, status text, role text)')
        for definition in schema['functions']:
            conn.execute(definition)
        for table, name, roles, command, using, check in schema['policies']:
            conn.execute(psycopg.sql.SQL('DROP POLICY IF EXISTS {} ON public.{}').format(
                psycopg.sql.Identifier(name), psycopg.sql.Identifier(table)))
            statement = psycopg.sql.SQL('CREATE POLICY {} ON public.{} FOR {} TO {}').format(
                psycopg.sql.Identifier(name), psycopg.sql.Identifier(table), psycopg.sql.SQL(command),
                psycopg.sql.SQL(',').join(psycopg.sql.Identifier(role) for role in roles))
            if using:
                statement += psycopg.sql.SQL(' USING ({})').format(psycopg.sql.SQL(using))
            if check:
                statement += psycopg.sql.SQL(' WITH CHECK ({})').format(psycopg.sql.SQL(check))
            conn.execute(statement)
    conn.execute('SET LOCAL ROLE everydayai')
    base = storage.validate(PACKAGE, publication())
    keys = ['asset-test-0921', 'recommendation-smoke-20260926', 'test-daily-report',
            'a001', 'easel-text-polisher', 'reference-image-prompts']
    expected = {}
    for org in (a, b):
        for key in keys:
            package = repository(org).create_package(PackageCreate(
                skill_key=key, scope_kind='org', org_id=org, source='test'))
            revision = repository(org).publish_revision(package.id, replace(base,
                skill_key=key, nas_path=revision_path(package, base.revision)))
            repository(org).set_assignment(package.id, revision.id, enabled=True)
            expected[(org, package.id)] = key not in keys[:3]
    conn.execute('RESET ROLE')
    if deleted_fixture:
        deleted_id = next(pid for (org, pid) in expected if org == a)
        expected[(a, deleted_id)] = True
        # Seed a historical deleted record; migration runs with all guards on.
        conn.execute('ALTER TABLE skill_drafts DISABLE TRIGGER USER')
        conn.execute("INSERT INTO skill_drafts(package_id,status,revision,content,deleted_at,deleted_by) VALUES (%s,'deprecated','v1','{}',now(),%s)", (deleted_id, a))
        conn.execute('ALTER TABLE skill_drafts ENABLE TRIGGER USER')
    original_drafts = conn.execute('SELECT * FROM skill_drafts ORDER BY package_id').fetchall()
    original_revisions = conn.execute('SELECT * FROM skill_revisions ORDER BY id').fetchall()
    conn.execute('CREATE ROLE everydayai_owner NOSUPERUSER NOBYPASSRLS')
    for table in (*SKILL_TABLES, 'skill_drafts', 'skill_change_audits'):
        conn.execute(psycopg.sql.SQL('ALTER TABLE {} OWNER TO {}').format(
            psycopg.sql.Identifier(table), psycopg.sql.Identifier(table_owner)))
    conn.execute(psycopg.sql.SQL('ALTER FUNCTION skill_catalog_guard() OWNER TO {}').format(psycopg.sql.Identifier(table_owner)))
    for function in ('skill_authoring_guard', 'skill_record_change', 'skill_catalog_org_id'):
        conn.execute(psycopg.sql.SQL('ALTER FUNCTION {}() OWNER TO {}').format(
            psycopg.sql.Identifier(function), psycopg.sql.Identifier(table_owner)))
    migration = (MIGRATIONS / '300_disable_retired_test_skills.sql').read_text()
    for _ in range(2):
        # Match the controlled executor: session user can change roles, initial
        # effective role is everydayai_owner even when tables have legacy owners.
        conn.execute('SET LOCAL ROLE everydayai_owner')
        conn.execute(migration)
        conn.execute('RESET ROLE')
        actual = {(org, pid): enabled for org, pid, enabled in conn.execute(
            'SELECT org_id,package_id,enabled FROM skill_assignments').fetchall()}
        assert actual == expected
        assert conn.execute('SELECT * FROM skill_revisions ORDER BY id').fetchall() == original_revisions
        assert conn.execute("SELECT count(*) FROM pg_policies WHERE policyname LIKE 'skill_cleanup_300_%'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE action='migration_300_disable_test_skills' AND actor_user_id IS NULL").fetchone()[0] == (5 if deleted_fixture else 6)
        assert conn.execute('SELECT * FROM skill_drafts ORDER BY package_id').fetchall() == original_drafts
