from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_59_agent_runtime_scheduled_adoption_preflight.sql"
ROLLBACK = "227_59_agent_runtime_scheduled_adoption_preflight_rollback.sql"


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _rollback(url: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / ROLLBACK).read_text())


def _setup_tables(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("""
            CREATE TABLE scheduled_tasks(
                id UUID PRIMARY KEY, org_id UUID, user_id UUID, name TEXT,
                prompt TEXT, timezone TEXT, push_target JSONB,
                template_file JSONB, max_credits INTEGER, retry_count INTEGER,
                timeout_sec INTEGER, schedule_type TEXT, cron_expr TEXT,
                run_at TIMESTAMPTZ, weekdays SMALLINT[], day_of_month SMALLINT,
                next_run_at TIMESTAMPTZ, last_summary TEXT, status TEXT,
                runtime_action_id UUID, runtime_attempt_id UUID,
                runtime_request_hash TEXT, runtime_idempotency_key TEXT,
                runtime_state_version BIGINT DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE agent_runtime_scheduled_execution_profiles(
                scheduled_task_id UUID PRIMARY KEY
            )
        """)
        rows = [
            ("00000000-0000-0000-0000-000000000001", "active", None, None, None, None, "web"),
            ("00000000-0000-0000-0000-000000000002", "running", None, None, None, None, "web"),
            ("00000000-0000-0000-0000-000000000003", "paused", None, None, None, None, "web"),
            ("00000000-0000-0000-0000-000000000004", "error", None, None, None, None, "web"),
            ("00000000-0000-0000-0000-000000000005", "active", None, None, None, None, "bad"),
            ("00000000-0000-0000-0000-000000000006", "active", "66666666-6666-6666-6666-666666666666", None, None, None, "web"),
            ("00000000-0000-0000-0000-000000000007", "active", None, None, None, None, "web"),
        ]
        for task_id, status, action, attempt, request_hash, key, target_type in rows:
            target = {
                "type": target_type,
                "user_id": "44444444-4444-4444-4444-444444444444",
            }
            conn.execute(
                """INSERT INTO scheduled_tasks(
                    id,org_id,user_id,name,prompt,timezone,push_target,
                    max_credits,retry_count,timeout_sec,schedule_type,cron_expr,
                    next_run_at,status,runtime_action_id,runtime_attempt_id,
                    runtime_request_hash,runtime_idempotency_key
                ) VALUES(%s,'22222222-2222-2222-2222-222222222222',
                    '44444444-4444-4444-4444-444444444444','task','prompt','UTC',%s,10,1,180,
                    'cron','0 9 * * *',clock_timestamp(),%s,%s,%s,%s,%s)""",
                (task_id, psycopg.types.json.Jsonb(target), status, action, attempt, request_hash, key),
            )
        conn.execute(
            "INSERT INTO agent_runtime_scheduled_execution_profiles VALUES(%s)",
            ("00000000-0000-0000-0000-000000000007",),
        )
        conn.commit()


def test_apply_readback_rollback_reapply_and_owner_acl(database: str) -> None:
    _setup_tables(database)
    _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        report = conn.execute(
            "SELECT read_agent_runtime_scheduled_adoption_plan_v1(NULL, TRUE)"
        ).fetchone()[0]
        assert report["outcome"] == "dry_run"
        assert report["total_tasks"] == 7
        assert report["safe_to_adopt_count"] == 0
        categories = {item["category"] for item in report["tasks"]}
        assert "runtime_owned" in categories
        assert "candidate_runtime_source_required" in categories
        assert "blocked_running" in categories
        assert "blocked_invalid_task" in categories
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker', "
            "'read_agent_runtime_scheduled_adoption_plan_v1(uuid,boolean)', 'EXECUTE')"
        ).fetchone()[0] is False
    _rollback(database)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regprocedure('read_agent_runtime_scheduled_adoption_plan_v1(uuid,boolean)')"
        ).fetchone()[0] is None
    _apply(database, MIGRATION)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        assert conn.execute(
            "SELECT (read_agent_runtime_scheduled_adoption_plan_v1(NULL, TRUE)->>'total_tasks')::int"
        ).fetchone()[0] == 7
