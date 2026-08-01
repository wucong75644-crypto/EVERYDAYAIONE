"""AR-17.3 migration contract on the disposable local PostgreSQL fixture."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _rollback(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / name).read_text())


def test_ar173_226_apply_rollback_reapply_and_worker_acl(database: str) -> None:
    migrations = [f"226_{index:02d}_" for index in range(1, 7)]
    names = [next((ROOT / "migrations").glob(f"{prefix}*.sql")).name for prefix in migrations]
    rollbacks = [next((ROOT / "migrations/rollback").glob(f"{prefix}*_rollback.sql")).name for prefix in reversed(migrations)]
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY, org_id UUID, user_id UUID, relative_path TEXT NOT NULL, oss_object_key TEXT NOT NULL, purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY, org_id UUID, user_id UUID, status TEXT NOT NULL DEFAULT 'active', updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for name in names:
        _apply(database, name)
    with psycopg.connect(database) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
        assert {"agent_action_callback_inbox", "agent_action_cost_settlements", "agent_action_artifact_links"} <= tables
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='agent_action_cost_settlements'::regclass").fetchone() == (True, True)
        assert conn.execute("SELECT has_table_privilege('everydayai_agent_runtime_worker','agent_action_cost_settlements','SELECT')").fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','reserve_agent_action_cost(UUID,UUID,BIGINT,TEXT)','EXECUTE')").fetchone()[0] is True
    for name in rollbacks:
        _rollback(database, name)
    for name in names:
        _apply(database, name)
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT to_regclass('agent_action_cost_settlements')").fetchone()[0] == "agent_action_cost_settlements"
