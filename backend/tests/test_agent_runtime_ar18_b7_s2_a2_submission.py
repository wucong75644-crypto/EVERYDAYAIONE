"""Unit contracts for AR-18 B7-S2-A2 owner routing."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.routes.scheduled_task_support import request_runtime_scheduled_execution
from services.scheduler.scanner import ScheduledTaskScanner
from services.scheduler.task_executor import ScheduledTaskExecutor
from services.scheduler.worker_store import ScheduledWorkerStore


def _rpc_db(data):
    db = MagicMock()
    db.rpc.return_value.execute.return_value = MagicMock(data=data)
    return db


def test_legacy_immediate_execution_does_not_call_runtime_rpc():
    db = _rpc_db({})

    result = request_runtime_scheduled_execution(
        db, task={"id": "legacy"}, task_id="legacy",
        org_id="org", user_id="user", idempotency_key=None,
    )

    assert result is None
    db.rpc.assert_not_called()


def test_runtime_immediate_execution_returns_command_readback():
    db = _rpc_db({
        "owner_kind": "runtime", "outcome": "already_submitted",
        "command_id": "command-1",
    })

    result = request_runtime_scheduled_execution(
        db,
        task={"id": "runtime", "runtime_action_id": "action-1",
              "runtime_state_version": 3},
        task_id="runtime", org_id="org", user_id="user",
        idempotency_key="manual-request-1",
    )

    assert result and result["command_id"] == "command-1"
    name, params = db.rpc.call_args.args
    assert name == "request_agent_runtime_scheduled_execution_v1"
    assert params["p_expected_task_version"] == 3
    assert params["p_request_id"] == "manual-request-1"


def test_runtime_immediate_execution_fails_closed_on_owner_mismatch():
    db = _rpc_db({"owner_kind": "legacy", "outcome": "legacy_owner"})

    with pytest.raises(HTTPException, match="Owner"):
        request_runtime_scheduled_execution(
            db, task={"runtime_action_id": "action-1"}, task_id="runtime",
            org_id="org", user_id="user", idempotency_key="manual-request-2",
        )


def test_runtime_immediate_execution_requires_stable_idempotency_key():
    db = _rpc_db({})

    with pytest.raises(HTTPException, match="Idempotency-Key"):
        request_runtime_scheduled_execution(
            db, task={"runtime_action_id": "action-1"}, task_id="runtime",
            org_id="org", user_id="user", idempotency_key=None,
        )

    db.rpc.assert_not_called()


def test_worker_store_normalizes_mixed_owner_claims():
    db = _rpc_db([
        {"owner_kind": "legacy", "task": {"id": "legacy"}},
        {"owner_kind": "runtime", "command_id": "command-1"},
    ])

    claims = ScheduledWorkerStore(db).claim_due(
        datetime.now(timezone.utc), 5,
    )

    assert claims == [
        {"id": "legacy"},
        {"_execution_owner": "runtime", "command_id": "command-1"},
    ]


def test_worker_store_rejects_unknown_owner_claim():
    db = _rpc_db([{"owner_kind": "unknown"}])

    with pytest.raises(RuntimeError, match="unknown owner"):
        ScheduledWorkerStore(db).claim_due(datetime.now(timezone.utc), 5)


@pytest.mark.asyncio
async def test_scanner_never_sends_runtime_claim_to_legacy_executor():
    executor = MagicMock()
    executor.execute = AsyncMock()
    scanner = ScheduledTaskScanner(MagicMock(), executor=executor)
    scanner._store = MagicMock()
    scanner._store.list_stale.return_value = []
    scanner._store.claim_due.return_value = [
        {"_execution_owner": "runtime", "command_id": "command-1"},
    ]

    assert await scanner.poll() == 1
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_executor_gate_prevents_run_and_credit_entrypoints():
    executor = ScheduledTaskExecutor(MagicMock())
    executor._store = MagicMock()
    executor._store.legacy_owner_allowed.return_value = False
    executor._create_run = AsyncMock()
    executor._credits = MagicMock()

    await executor.execute({"id": "runtime-task"})

    executor._create_run.assert_not_called()
    executor._credits.assert_not_called()
