"""Typed Sandbox Job domain, parser, and scoped adapter contracts."""

from __future__ import annotations

from typing import Any

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.domain.errors import (
    IdempotencyConflictError,
    PersistenceContractError,
)
from services.agent.runtime.infrastructure.postgres.sandbox_job_parsing import (
    parse_sandbox_job_receipt,
)
from services.agent.runtime.infrastructure.postgres.sandbox_job_repository import (
    PostgresSandboxJobRepository,
)
from services.agent.runtime.ports.sandbox_job import SandboxJobOutcome


JOB_ID = "11111111-1111-1111-1111-111111111111"
ACTION_ID = "22222222-2222-2222-2222-222222222222"
ATTEMPT_ID = "33333333-3333-3333-3333-333333333333"
INTENT_ID = "44444444-4444-4444-4444-444444444444"
TOKEN = "55555555-5555-5555-5555-555555555555"


def _job(status: str = "queued") -> dict[str, object]:
    return {
        "id": JOB_ID,
        "action_id": ACTION_ID,
        "attempt_id": ATTEMPT_ID,
        "dispatch_intent_id": INTENT_ID,
        "external_idempotency_key": "action:key",
        "request_hash": "a" * 64,
        "code_sha256": "b" * 64,
        "resource_limits": {
            "timeout_seconds": 120,
            "cpu_millis": 800,
            "memory_bytes": 536870912,
            "pids": 64,
            "disk_bytes": 268435456,
            "file_count": 100,
        },
        "input_manifest": {"schema_revision": 1, "items": []},
        "status": status,
        "state_version": 1,
        "fencing_token": 0,
        "cleanup_status": "not_required",
        "materialization_status": "not_started",
        "queued_at": "2026-07-28T12:00:00+00:00",
        "claim_token": None,
        "lease_expires_at": None,
        "reconciliation_token": None,
        "reconciliation_lease_expires_at": None,
        "terminal_at": None,
        "artifact_manifest": {"schema_revision": 1, "items": []},
        "partial_effects": {"schema_revision": 1, "items": []},
    }


class _Response:
    def __init__(self, data: object) -> None:
        self.data = data


class _Call:
    def __init__(self, database: "_Database", name: str, params: dict) -> None:
        self.database, self.name, self.params = database, name, params

    async def execute(self) -> _Response:
        self.database.calls.append((self.name, self.params))
        return _Response(self.database.responses[self.name])


class _Database:
    def __init__(
        self, access_kind: DatabaseAccessKind,
        responses: dict[str, object],
    ) -> None:
        self.scope = DatabaseScope(
            actor_user_id=None, org_id=None, access_kind=access_kind,
            request_id="sandbox-job-test",
        )
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict) -> _Call:
        return _Call(self, name, params)


def test_unknown_and_conflict_outcomes_fail_closed() -> None:
    with pytest.raises(PersistenceContractError):
        parse_sandbox_job_receipt({"outcome": "future_success"})
    with pytest.raises(IdempotencyConflictError):
        parse_sandbox_job_receipt({"outcome": "idempotency_conflict"})


def test_snapshot_rejects_unknown_status() -> None:
    job = _job("future")
    with pytest.raises(PersistenceContractError, match="SandboxJobStatus"):
        parse_sandbox_job_receipt({"outcome": "found", "job": job})


@pytest.mark.asyncio
async def test_runtime_adapter_forwards_immutable_create_binding() -> None:
    database = _Database(DatabaseAccessKind.AGENT_RUNTIME, {
        "create_or_get_sandbox_job": {
            "outcome": "created", "job": _job(),
        },
    })
    repository = PostgresSandboxJobRepository(database)
    receipt = await repository.create_or_get(
        action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
        dispatch_intent_id=INTENT_ID,
        expected_action_version=1, expected_attempt_version=2,
        external_idempotency_key="action:key", request_hash="a" * 64,
        executor_type="sandbox.python", executor_revision=1,
        runtime_revision="python-v1", workspace_scope_ref="ws-scope:user",
        code_sha256="b" * 64,
        input_manifest={"schema_revision": 1, "items": []},
        resource_limits={"timeout_seconds": 120},
    )
    assert receipt.outcome is SandboxJobOutcome.CREATED
    assert database.calls[0][1]["p_dispatch_intent_id"] == INTENT_ID
    assert "p_code" not in database.calls[0][1]


@pytest.mark.asyncio
async def test_worker_adapter_cannot_call_runtime_mutation() -> None:
    database = _Database(DatabaseAccessKind.SANDBOX_WORKER, {})
    repository = PostgresSandboxJobRepository(database)
    with pytest.raises(PermissionError, match="SCOPE_MISMATCH"):
        await repository.request_cancel(job_id=JOB_ID, expected_version=1)


@pytest.mark.asyncio
async def test_runtime_adapter_cannot_claim() -> None:
    database = _Database(DatabaseAccessKind.AGENT_RUNTIME, {})
    repository = PostgresSandboxJobRepository(database)
    with pytest.raises(PermissionError, match="SCOPE_MISMATCH"):
        await repository.claim(worker_id="sandbox-1")


@pytest.mark.asyncio
async def test_worker_adapter_maps_narrow_rpc_arguments() -> None:
    rpc_names = (
        "get_sandbox_job",
        "get_owned_sandbox_job",
        "claim_next_sandbox_job",
        "renew_sandbox_job_lease",
        "mark_sandbox_job_started",
        "recover_expired_sandbox_job",
        "record_sandbox_cancel_signal",
        "finish_sandbox_job",
        "record_sandbox_job_unknown",
        "claim_sandbox_job_reconciliation",
        "renew_sandbox_job_reconciliation",
        "resolve_sandbox_job_reconciliation",
        "record_sandbox_job_cleanup",
    )
    responses = {
        name: {"outcome": "found", "job": _job()} for name in rpc_names
    }
    database = _Database(DatabaseAccessKind.SANDBOX_WORKER, responses)
    repository = PostgresSandboxJobRepository(database)
    await repository.get(job_id=JOB_ID)
    await repository.get_owned(
        job_id=JOB_ID, worker_id="sandbox-1",
        claim_token=TOKEN, fencing_token=1,
    )
    await repository.claim(worker_id="sandbox-1")
    ownership = {
        "job_id": JOB_ID, "claim_token": TOKEN, "fencing_token": 1,
        "expected_version": 2,
    }
    await repository.renew(**ownership, lease_seconds=60)
    await repository.mark_started(**ownership, phase="starting")
    await repository.recover_expired(job_id=JOB_ID, expected_version=2)
    await repository.record_cancel_signal(**ownership, signal_state="accepted")
    await repository.finish(
        **ownership, terminal_status="failed", terminal_reason="FAILED",
        receipt_hash="c" * 64, receipt={"receipt_revision": 1},
    )
    await repository.record_unknown(
        **ownership, ambiguity_evidence={"kind": "LEASE_LOST"},
        partial_effects={"schema_revision": 1, "items": []},
    )
    await repository.claim_reconciliation(
        job_id=JOB_ID, expected_version=2, worker_id="scanner",
        lease_seconds=60,
    )
    reconciliation = {
        "job_id": JOB_ID, "reconciliation_token": TOKEN,
        "expected_version": 2,
    }
    await repository.renew_reconciliation(
        **reconciliation, lease_seconds=60,
    )
    await repository.resolve_reconciliation(
        **reconciliation, resolution="still_unknown",
        terminal_reason="STILL_UNKNOWN", receipt_hash="d" * 64,
        receipt={"receipt_revision": 1},
    )
    await repository.record_cleanup(
        **reconciliation, cleanup_status="completed",
        cleanup_evidence={"kind": "CLEANUP_CONFIRMED"},
    )
    assert [name for name, _ in database.calls] == list(rpc_names)
    unknown_params = dict(database.calls)[
        "record_sandbox_job_unknown"
    ]
    assert unknown_params["p_partial_effects"]["items"] == []


@pytest.mark.asyncio
async def test_worker_adapter_missing_required_argument_fails_closed() -> None:
    database = _Database(DatabaseAccessKind.SANDBOX_WORKER, {})
    repository = PostgresSandboxJobRepository(database)
    with pytest.raises(ValueError, match="missing Sandbox Job arguments"):
        await repository.finish(job_id=JOB_ID)


def test_generic_worker_scope_cannot_construct_adapter() -> None:
    database = _Database(DatabaseAccessKind.WORKER, {})
    with pytest.raises(ValueError, match="SANDBOX_JOB_SCOPED"):
        PostgresSandboxJobRepository(database)
