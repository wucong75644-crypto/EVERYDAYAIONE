"""定时任务 run fencing token 与租约回归测试。"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.scheduler.run_lease import (
    ScheduledRunLeaseLost,
    execute_with_scheduled_lease,
)
from services.scheduler.worker_store import (
    ScheduledRunLease,
    ScheduledWorkerStore,
)


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/179_scheduled_run_fencing.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/179_scheduled_run_fencing_rollback.sql"
).read_text()


def test_migration_binds_all_side_effects_to_active_fence() -> None:
    assert "ADD COLUMN execution_token UUID" in SQL
    assert "ADD COLUMN lease_expires_at TIMESTAMPTZ" in SQL
    assert "ADD COLUMN result_message_id UUID" in SQL
    assert "run.execution_token IS NOT DISTINCT FROM p_execution_token" in SQL
    assert "run.lease_expires_at > clock_timestamp()" in SQL
    for name in (
        "worker_renew_scheduled_run",
        "worker_append_scheduled_result_message",
        "worker_complete_scheduled_run",
        "worker_fail_scheduled_run",
        "worker_lock_scheduled_credits",
        "worker_settle_scheduled_credits",
    ):
        assert f"CREATE FUNCTION {name}" in SQL
    assert "BEFORE UPDATE OF status" in SQL
    assert "NEW.execution_token := NULL" in SQL
    assert "'outcome', 'already_stored'" in SQL
    assert "SET result_message_id = v_message_id" in SQL
    assert "DROP COLUMN IF EXISTS execution_token" in ROLLBACK


def test_get_scheduled_task_declares_idempotency_variables() -> None:
    body = SQL.split("CREATE FUNCTION worker_get_scheduled_task(", 1)[1]
    body = body.split(
        "CREATE FUNCTION worker_append_scheduled_result_message(", 1
    )[0]
    assert "v_conversation_id UUID;" in body
    assert "v_message_id UUID;" in body


def test_store_requires_token_from_create_result() -> None:
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {
        "outcome": "created",
        "run": {"id": "run-1"},
        "execution_token": "token-1",
    }
    store = ScheduledWorkerStore(db)

    run = store.create_run("task-1")

    assert run == ScheduledRunLease("run-1", "token-1")
    assert db.rpc.call_args.args[1] == {
        "p_task_id": "task-1",
        "p_lease_seconds": 90,
    }


@pytest.mark.asyncio
async def test_lease_loss_cancels_execution() -> None:
    store = MagicMock()
    store.renew.return_value = False
    cancelled = asyncio.Event()

    async def execution():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(ScheduledRunLeaseLost):
        await execute_with_scheduled_lease(
            store,
            "task-1",
            ScheduledRunLease("run-1", "token-1"),
            execution(),
            renew_interval_seconds=0.001,
        )

    assert cancelled.is_set()
