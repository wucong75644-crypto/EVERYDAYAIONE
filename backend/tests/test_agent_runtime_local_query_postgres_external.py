from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_57_agent_runtime_local_query_facade.sql"
ROLLBACK = ROOT / "migrations/rollback/227_57_agent_runtime_local_query_facade_rollback.sql"


def _prepare(database: str) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY,org_id UUID,"
            "user_id UUID,relative_path TEXT NOT NULL,oss_object_key TEXT NOT NULL,"
            "purged BOOLEAN NOT NULL DEFAULT FALSE)"
        )
        connection.execute(
            "CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY,org_id UUID,"
            "user_id UUID,status TEXT NOT NULL DEFAULT 'active',"
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())"
        )
        connection.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_08_agent_runtime_facts_recovery_fence.sql",
    ):
        _apply(database, name)
    _apply(database, MIGRATION.name)


def test_local_query_facade_apply_acl_rollback_reapply(database: str) -> None:
    _prepare(database)
    signature = (
        "execute_agent_runtime_local_query_v1"
        "(uuid,text,uuid,bigint,text,text,jsonb,jsonb)"
    )
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_agent_runtime_worker", signature),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ("everydayai_worker", signature),
        ).fetchone()[0] is False
        assert connection.execute(
            "SELECT has_table_privilege(%s,'agent_runtime_sessions','SELECT')",
            ("everydayai_agent_runtime_worker",),
        ).fetchone()[0] is False
    _apply(database, ROLLBACK.relative_to(ROOT / "migrations").as_posix())
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (signature,),
        ).fetchone()[0] is None
    _apply(database, MIGRATION.name)
    _apply(database, ROLLBACK.relative_to(ROOT / "migrations").as_posix())
