"""定时任务企微 Outbox 的数据库契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "242_scheduled_task_delivery_outbox.sql"
ROLLBACK = MIGRATIONS / "rollback/242_scheduled_task_delivery_outbox_rollback.sql"


def _function(sql: str, name: str, next_name: str | None = None) -> str:
    start = sql.index(f"CREATE OR REPLACE FUNCTION {name}")
    end = sql.index(
        f"CREATE OR REPLACE FUNCTION {next_name}", start + 1,
    ) if next_name else len(sql)
    return sql[start:end]


def test_outbox_keeps_delivery_identity_and_recovery_state() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS scheduled_task_deliveries" in sql
    assert "UNIQUE (run_id, delivery_key)" in sql
    assert "status IN ('pending', 'delivering', 'delivered', 'dead')" in sql
    assert "delivery_kind IN ('result', 'owner_alert')" in sql
    assert "idx_scheduled_task_deliveries_claim" in sql
    assert "jsonb_typeof(target_context) = 'object'" in sql
    assert "jsonb_typeof(payload) = 'object'" in sql


def test_success_commits_run_state_and_outbox_in_one_rpc() -> None:
    function = _function(
        MIGRATION.read_text(encoding="utf-8"),
        "complete_scheduled_task_success",
        "enqueue_scheduled_task_owner_alert",
    )

    assert "FOR UPDATE" in function
    assert "INSERT INTO scheduled_task_deliveries" in function
    assert "UPDATE scheduled_tasks" in function
    assert "UPDATE scheduled_task_runs" in function
    assert "refresh_scheduled_task_run_push_status(p_run_id)" in function
    assert "ON CONFLICT (run_id, delivery_key) DO NOTHING" in function


def test_claim_is_lease_fenced_and_uses_skip_locked() -> None:
    function = _function(
        MIGRATION.read_text(encoding="utf-8"),
        "claim_scheduled_task_delivery",
        "complete_scheduled_task_delivery",
    )

    assert "FOR UPDATE SKIP LOCKED" in function
    assert "ORDER BY next_attempt_at, created_at, id" in function
    assert "lease_expires_at <= NOW()" in function
    assert "attempt_count = attempt_count + 1" in function
    assert "lease_token = v_token" in function


def test_delivery_completion_and_failure_require_current_lease() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    complete = _function(sql, "complete_scheduled_task_delivery", "fail_scheduled_task_delivery")
    failed = _function(sql, "fail_scheduled_task_delivery", "claim_scheduled_task_now")

    for function in (complete, failed):
        assert "lease_token IS DISTINCT FROM p_lease_token" in function
        assert "lease_expires_at <= NOW()" in function
        assert "'ownership_lost'" in function
    assert "LEAST(" in failed
    assert "900" in failed
    assert "CASE WHEN v_dead THEN 'dead' ELSE 'pending' END" in failed


def test_manual_claim_prevents_concurrent_scheduler_execution() -> None:
    function = _function(MIGRATION.read_text(encoding="utf-8"), "claim_scheduled_task_now")

    assert "FOR UPDATE" in function
    assert "v_task.status = 'running'" in function
    assert "'already_running'" in function
    assert "status = 'running'" in function


def test_rpcs_are_not_public_and_rollback_removes_all_objects() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")

    for name in (
        "complete_scheduled_task_success",
        "enqueue_scheduled_task_owner_alert",
        "claim_scheduled_task_delivery",
        "complete_scheduled_task_delivery",
        "fail_scheduled_task_delivery",
        "claim_scheduled_task_now",
    ):
        assert f"REVOKE ALL ON FUNCTION {name}" in sql
        assert f"DROP FUNCTION IF EXISTS {name}" in rollback
    assert "DROP TABLE IF EXISTS scheduled_task_deliveries" in rollback
