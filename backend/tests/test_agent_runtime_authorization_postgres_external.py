"""Real PostgreSQL contract for AR-16 authorization ownership."""

from __future__ import annotations

import os

import psycopg
import pytest


pytestmark = pytest.mark.external
DATABASE_URL = os.getenv("AR16_TEST_DATABASE_URL", "")


@pytest.fixture(scope="module", autouse=True)
def dedicated_database() -> None:
    if os.getenv("RUN_AR16_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR16_DB_TEST=1 and AR16_TEST_DATABASE_URL required")
    if "ar16" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR16 database name required")


def _value(sql: str) -> object:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchone()[0]


def test_authorization_tables_force_rls_without_direct_worker_access() -> None:
    rows = _value("""
        SELECT jsonb_agg(jsonb_build_object(
            'table', relname, 'rls', relrowsecurity, 'force', relforcerowsecurity
        ) ORDER BY relname)
        FROM pg_class
        WHERE relname IN (
            'agent_interactions', 'agent_authorization_grants',
            'agent_authorization_grant_uses', 'agent_policy_receipts'
        )
    """)

    assert len(rows) == 4
    assert all(row["rls"] and row["force"] for row in rows)
    assert not _value("""
        SELECT has_table_privilege(
            'everydayai_worker', 'agent_authorization_grants', 'SELECT'
        )
    """)


def test_rpc_role_matrix_is_fail_closed() -> None:
    worker_open = _value("""
        SELECT has_function_privilege(
            'everydayai_worker',
            'open_agent_authorization_interaction(uuid,bigint,jsonb,text,integer)',
            'EXECUTE'
        )
    """)
    runtime_open = _value("""
        SELECT has_function_privilege(
            'everydayai_runtime',
            'open_agent_authorization_interaction(uuid,bigint,jsonb,text,integer)',
            'EXECUTE'
        )
    """)
    runtime_resolve = _value("""
        SELECT has_function_privilege(
            'everydayai_runtime',
            'resolve_agent_authorization_interaction('
            'uuid,bigint,text,text,jsonb,text,text,integer)',
            'EXECUTE'
        )
    """)

    assert worker_open
    assert not runtime_open
    assert runtime_resolve
