"""Cleanup uses real forced RLS without touching workflow grants or documents."""
from dataclasses import replace

import psycopg

from services.skills.contracts import PackageCreate, revision_path
from tests.test_skill_catalog_postgres import (  # noqa: F401
    MIGRATIONS, SKILL_TABLES, database, postgres_socket, published,
)
from tests.test_skill_storage import PACKAGE, publication


def test_cleanup_is_narrow_idempotent_and_removes_temporary_policies(published):
    conn, repository, a, b, _, _, storage = published
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
    original_revisions = conn.execute('SELECT * FROM skill_revisions ORDER BY id').fetchall()
    conn.execute('CREATE ROLE everydayai_owner NOSUPERUSER NOBYPASSRLS')
    for table in SKILL_TABLES:
        conn.execute(psycopg.sql.SQL('ALTER TABLE {} OWNER TO everydayai_owner').format(psycopg.sql.Identifier(table)))
    conn.execute('ALTER FUNCTION skill_catalog_guard() OWNER TO everydayai_owner')
    migration = (MIGRATIONS / '300_disable_retired_test_skills.sql').read_text()
    for _ in range(2):
        conn.execute(migration)
        conn.execute('RESET ROLE')
        actual = {(org, pid): enabled for org, pid, enabled in conn.execute(
            'SELECT org_id,package_id,enabled FROM skill_assignments').fetchall()}
        assert actual == expected
        assert conn.execute('SELECT * FROM skill_revisions ORDER BY id').fetchall() == original_revisions
        assert conn.execute("SELECT count(*) FROM pg_policies WHERE policyname LIKE 'skill_cleanup_300_%'").fetchone()[0] == 0
