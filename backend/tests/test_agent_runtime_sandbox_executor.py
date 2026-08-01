from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.agent.runtime.domain import (
    ActionAttempt,
    ActionAttemptId,
    ActionAttemptStatus,
    ActionId,
    FencingToken,
    IdempotencyKey,
    Lease,
    RuntimeScope,
    ScopeKind,
)
from services.agent.runtime.domain.sandbox_job import (
    SandboxCleanupStatus,
    SandboxJobSnapshot,
    SandboxJobStatus,
    SandboxMaterializationStatus,
)
from services.agent.runtime.domain.errors import IdempotencyConflictError
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.capabilities import CapabilityBinding
from services.agent.runtime.executors.sandbox_job import (
    SANDBOX_JOB_DESCRIPTOR,
    SandboxJobExecutor,
    register_sandbox_job_executor,
)
from services.agent.runtime.ports.executor import ExecutionOutcome
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobOutcome,
    SandboxJobReceipt,
)
from services.agent.runtime.sandbox.launcher import IsolationProbe
from services.agent.runtime.sandbox.capability import SandboxJobCapability
from services.agent.runtime.sandbox.contracts import SandboxResourceLimits
from services.agent.runtime.sandbox.workspace import SandboxWorkspaceStore
from services.agent.runtime.sandbox.composition import (
    build_sandbox_executor_components,
)
from core.db_scope import DatabaseAccessKind, DatabaseScope


ACTION_ID = "11111111-1111-1111-1111-111111111111"
ATTEMPT_ID = "22222222-2222-2222-2222-222222222222"
JOB_ID = "33333333-3333-3333-3333-333333333333"


def _attempt(
    *, status: ActionAttemptStatus = ActionAttemptStatus.DISPATCHING,
    external_receipt=None, ambiguity_evidence=None, capability=None,
) -> ActionAttempt:
    return ActionAttempt(
        attempt_id=ActionAttemptId(ATTEMPT_ID),
        action_id=ActionId(ACTION_ID),
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="scope-1",
            user_id="user-1", org_id="org-1",
        ),
        attempt_number=1, status=status, worker_id="runtime-1",
        idempotency_key=IdempotencyKey("action:key"),
        request_hash="a" * 64,
        lease=Lease(
            fencing_token=FencingToken("fence-1"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
        started_at=datetime.now(timezone.utc),
        accepted_at=(
            datetime.now(timezone.utc)
            if status is ActionAttemptStatus.ACCEPTED else None
        ),
        external_receipt=external_receipt or {},
        ambiguity_evidence=ambiguity_evidence or {},
        session_id="55555555-5555-5555-5555-555555555555",
        run_id="66666666-6666-6666-6666-666666666666",
        capabilities=(
            {"sandbox_job": capability} if capability is not None else {}
        ),
    )


def _job(status: SandboxJobStatus) -> SandboxJobSnapshot:
    return SandboxJobSnapshot(
        job_id=JOB_ID, action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
        dispatch_intent_id="44444444-4444-4444-4444-444444444444",
        external_idempotency_key="action:key",
        request_hash="a" * 64, code_sha256="b" * 64,
        resource_limits={
            "timeout_seconds": 10, "cpu_millis": 500,
            "memory_bytes": 128 * 1024 * 1024,
            "pids": 16, "disk_bytes": 1024 * 1024, "file_count": 10,
        },
        input_manifest={"schema_revision": 1, "items": []},
        status=status, state_version=3, fencing_token=1,
        cleanup_status=SandboxCleanupStatus.NOT_REQUIRED,
        materialization_status=(
            SandboxMaterializationStatus.COMPLETED
            if status is SandboxJobStatus.SUCCEEDED
            else SandboxMaterializationStatus.NOT_STARTED
        ),
        queued_at=datetime.now(timezone.utc),
        terminal_at=(
            datetime.now(timezone.utc)
            if status in {
                SandboxJobStatus.SUCCEEDED, SandboxJobStatus.FAILED,
                SandboxJobStatus.TIMED_OUT, SandboxJobStatus.CANCELLED,
            } else None
        ),
        artifact_manifest={"schema_revision": 1, "items": []},
        partial_effects={"schema_revision": 1, "items": []},
        terminal_reason=(
            "EXECUTION_FAILED" if status is SandboxJobStatus.FAILED else None
        ),
        stdout_summary="bounded output",
    )


class _Capability(SandboxJobCapability):
    def __init__(self, job: SandboxJobSnapshot) -> None:
        object.__setattr__(self, "runtime_revision", "python-nsjail-v1")
        object.__setattr__(self, "job", job)
        object.__setattr__(self, "submit", AsyncMock(return_value=SandboxJobReceipt(
            outcome=SandboxJobOutcome.CREATED, job=job,
        )))
        object.__setattr__(self, "get", AsyncMock(return_value=SandboxJobReceipt(
            outcome=SandboxJobOutcome.FOUND, job=job,
        )))
        object.__setattr__(self, "readback_after_submit_loss", AsyncMock(
            return_value=SandboxJobReceipt(
                outcome=SandboxJobOutcome.FOUND, job=job,
            ),
        ))
        object.__setattr__(self, "request_cancel", AsyncMock(return_value=SandboxJobReceipt(
            outcome=SandboxJobOutcome.CANCEL_REQUESTED, job=job,
        )))
        object.__setattr__(
            self, "cleanup_staged_attempt", lambda **_kwargs: True,
        )


@pytest.mark.asyncio
async def test_dispatch_submits_once_and_returns_persistent_job_identity() -> None:
    capability = _Capability(_job(SandboxJobStatus.QUEUED))
    executor = SandboxJobExecutor()
    receipt = await executor.dispatch(_attempt(capability=capability), {
        "code": "print(1)",
        "external_idempotency_key": "action:key",
        "_dispatch_context": {
            "dispatch_intent_id": (
                "44444444-4444-4444-4444-444444444444"
            ),
            "expected_action_version": 4,
            "expected_attempt_version": 5,
        },
    })
    assert receipt.outcome is ExecutionOutcome.ACCEPTED
    assert receipt.external_receipt["sandbox_job_id"] == JOB_ID
    capability.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_queries_and_never_resubmits() -> None:
    capability = _Capability(_job(SandboxJobStatus.SUCCEEDED))
    executor = SandboxJobExecutor()
    receipt = await executor.reconcile(_attempt(
        status=ActionAttemptStatus.ACCEPTED,
        external_receipt={"sandbox_job_id": JOB_ID},
        capability=capability,
    ))
    assert receipt.outcome is ExecutionOutcome.COMPLETED
    assert receipt.result is not None
    capability.get.assert_awaited_once()
    capability.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_job_remains_reconcile_only() -> None:
    capability = _Capability(_job(SandboxJobStatus.UNKNOWN))
    executor = SandboxJobExecutor()
    receipt = await executor.reconcile(_attempt(
        status=ActionAttemptStatus.ACCEPTED,
        external_receipt={"sandbox_job_id": JOB_ID},
        capability=capability,
    ))
    assert receipt.outcome is ExecutionOutcome.UNKNOWN
    capability.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_is_accepted_request_not_terminal_cancelled() -> None:
    capability = _Capability(_job(SandboxJobStatus.RUNNING))
    capability.request_cancel.return_value = SandboxJobReceipt(
        outcome=SandboxJobOutcome.CANCEL_REQUESTED,
        job=_job(SandboxJobStatus.CANCEL_REQUESTED),
    )
    executor = SandboxJobExecutor()
    receipt = await executor.cancel(_attempt(
        status=ActionAttemptStatus.ACCEPTED,
        external_receipt={"sandbox_job_id": JOB_ID},
        capability=capability,
    ))
    assert receipt.outcome is ExecutionOutcome.ACCEPTED
    assert receipt.external_receipt["status"] == "cancel_requested"
    capability.request_cancel.assert_awaited_once()
    capability.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_idempotency_conflict_fails_without_unknown_retry() -> None:
    capability = _Capability(_job(SandboxJobStatus.QUEUED))
    capability.submit.side_effect = IdempotencyConflictError("conflict")
    executor = SandboxJobExecutor()
    receipt = await executor.dispatch(_attempt(capability=capability), {
        "code": "print(1)", "external_idempotency_key": "action:key",
        "_dispatch_context": {
            "dispatch_intent_id": (
                "44444444-4444-4444-4444-444444444444"
            ),
            "expected_action_version": 4,
            "expected_attempt_version": 5,
        },
    })
    assert receipt.outcome is ExecutionOutcome.FAILED
    assert (
        receipt.external_receipt["error_code"]
        == "SANDBOX_SUBMIT_IDEMPOTENCY_CONFLICT"
    )


@pytest.mark.asyncio
async def test_submit_response_loss_reads_back_without_second_submit() -> None:
    capability = _Capability(_job(SandboxJobStatus.QUEUED))
    executor = SandboxJobExecutor()
    binding = {
        "kind": "SANDBOX_SUBMIT_RESULT_UNKNOWN",
        "external_idempotency_key": "action:key",
        "action_id": ACTION_ID, "attempt_id": ATTEMPT_ID,
        "dispatch_intent_id": "44444444-4444-4444-4444-444444444444",
        "request_hash": "a" * 64, "org_id": "org-1",
        "user_id": "user-1",
        "session_id": "55555555-5555-5555-5555-555555555555",
        "run_id": "66666666-6666-6666-6666-666666666666",
        "executor_type": "sandbox_job", "executor_revision": 1,
        "runtime_revision": "python-nsjail-v1",
    }
    receipt = await executor.reconcile(_attempt(
        status=ActionAttemptStatus.UNKNOWN,
        ambiguity_evidence=binding,
        capability=capability,
    ))
    assert receipt.outcome is ExecutionOutcome.ACCEPTED
    capability.readback_after_submit_loss.assert_awaited_once()
    capability.submit.assert_not_awaited()


def test_registry_has_exact_professional_code_execute_mapping() -> None:
    registry = ExecutorRegistry()
    executor = SandboxJobExecutor()
    register_sandbox_job_executor(registry, executor)
    descriptor, resolved = registry.resolve("code_execute")
    assert descriptor == SANDBOX_JOB_DESCRIPTOR
    assert resolved is executor


def test_local_isolation_probe_fails_closed_without_linux() -> None:
    probe = IsolationProbe.inspect()
    assert not probe.ready
    assert probe.code in {
        "SANDBOX_LINUX_REQUIRED",
        "SANDBOX_NSJAIL_REQUIRED",
        "SANDBOX_CGROUP_V2_REQUIRED",
        "SANDBOX_CGROUP_CONTROLLERS_MISSING",
        "SANDBOX_CGROUP_UNAVAILABLE",
    }


def test_executor_result_hash_is_stable() -> None:
    payload = {
        "sandbox_job_id": JOB_ID,
        "artifact_manifest": {"schema_revision": 1, "items": []},
    }
    expected = hashlib.sha256(
        __import__("json").dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode(),
    ).hexdigest()
    assert len(expected) == 64


@pytest.mark.asyncio
async def test_capability_stages_hash_bound_code_without_exposing_authority(
    tmp_path: Path,
) -> None:
    jobs = type("_Jobs", (), {})()
    jobs.create_or_get = AsyncMock(return_value=SandboxJobReceipt(
        outcome=SandboxJobOutcome.CREATED,
        job=_job(SandboxJobStatus.QUEUED),
    ))
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    capability = SandboxJobCapability(
        binding=CapabilityBinding(
            action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ),
        _jobs=jobs, _workspace=workspace,
        runtime_revision="python-nsjail-v1",
        allowed_operations=frozenset({"submit"}),
    )
    receipt = await capability.submit(
        action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
        dispatch_intent_id="44444444-4444-4444-4444-444444444444",
        expected_action_version=1, expected_attempt_version=2,
        external_idempotency_key="action:key", request_hash="a" * 64,
        executor_type="sandbox_job", executor_revision=1,
        workspace_scope_ref="ws-scope:user:scope-1",
        code="print(1)",
        input_manifest={"schema_revision": 1, "items": []},
        resource_limits=SandboxResourceLimits.from_request({}),
    )
    assert receipt.outcome is SandboxJobOutcome.CREATED
    assert not hasattr(capability, "jobs")
    assert not hasattr(capability, "workspace")
    values = jobs.create_or_get.await_args.kwargs
    assert values["code_sha256"] == hashlib.sha256(b"print(1)").hexdigest()
    assert workspace.read_code(
        action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
        expected_sha256=values["code_sha256"],
    ) == b"print(1)"


@pytest.mark.asyncio
async def test_capability_rejects_ungranted_artifact_before_read(
    tmp_path: Path,
) -> None:
    jobs = type("_Jobs", (), {"create_or_get": AsyncMock()})()
    read = AsyncMock(return_value=b"data")
    capability = SandboxJobCapability(
        binding=CapabilityBinding(
            action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ),
        _jobs=jobs, _workspace=SandboxWorkspaceStore(tmp_path.resolve()),
        runtime_revision="python-nsjail-v1",
        allowed_operations=frozenset({"submit"}),
        _read_artifact=read,
    )
    with pytest.raises(PermissionError, match="REF_NOT_ALLOWED"):
        await capability.submit(
            action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
            dispatch_intent_id="44444444-4444-4444-4444-444444444444",
            expected_action_version=1, expected_attempt_version=2,
            external_idempotency_key="action:key", request_hash="a" * 64,
            executor_type="sandbox_job", executor_revision=1,
            workspace_scope_ref="ws-scope:user:scope-1",
            code="print(1)",
            input_manifest={"schema_revision": 1, "items": [{
                "artifact_ref": "artifact:not-granted",
                "content_sha256": hashlib.sha256(b"data").hexdigest(),
                "size_bytes": 4, "media_type": "text/plain",
            }]},
            resource_limits=SandboxResourceLimits.from_request({}),
        )
    read.assert_not_awaited()
    jobs.create_or_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_create_rejection_removes_attempt_scoped_staging(
    tmp_path: Path,
) -> None:
    jobs = type("_Jobs", (), {
        "create_or_get": AsyncMock(
            side_effect=IdempotencyConflictError("conflict"),
        ),
    })()
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    capability = SandboxJobCapability(
        binding=CapabilityBinding(
            action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ),
        _jobs=jobs, _workspace=workspace,
        runtime_revision="python-nsjail-v1",
        allowed_operations=frozenset({"submit", "cleanup"}),
    )
    receipt = await SandboxJobExecutor().dispatch(
        _attempt(capability=capability),
        {
            "code": "private business text",
            "external_idempotency_key": "action:key",
            "_dispatch_context": {
                "dispatch_intent_id": (
                    "44444444-4444-4444-4444-444444444444"
                ),
                "expected_action_version": 4,
                "expected_attempt_version": 5,
            },
        },
    )
    assert receipt.outcome is ExecutionOutcome.FAILED
    assert not (
        tmp_path / "inputs" / ACTION_ID / ATTEMPT_ID
    ).exists()


@pytest.mark.asyncio
async def test_submit_loss_not_found_removes_staging_without_resubmit(
    tmp_path: Path,
) -> None:
    jobs = type("_Jobs", (), {
        "readback_by_binding": AsyncMock(return_value=SandboxJobReceipt(
            outcome=SandboxJobOutcome.NOT_FOUND,
        )),
    })()
    workspace = SandboxWorkspaceStore(tmp_path.resolve())
    code = b"private business text"
    digest = hashlib.sha256(code).hexdigest()
    await workspace.stage_code(
        action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
        content=code, expected_sha256=digest,
    )
    capability = SandboxJobCapability(
        binding=CapabilityBinding(
            action_id=ACTION_ID, attempt_id=ATTEMPT_ID,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ),
        _jobs=jobs, _workspace=workspace,
        runtime_revision="python-nsjail-v1",
        allowed_operations=frozenset({"readback", "cleanup"}),
    )
    binding = {
        "external_idempotency_key": "action:key",
        "action_id": ACTION_ID, "attempt_id": ATTEMPT_ID,
        "dispatch_intent_id": "44444444-4444-4444-4444-444444444444",
        "request_hash": "a" * 64, "org_id": "org-1",
        "user_id": "user-1",
        "session_id": "55555555-5555-5555-5555-555555555555",
        "run_id": "66666666-6666-6666-6666-666666666666",
        "executor_type": "sandbox_job", "executor_revision": 1,
        "runtime_revision": "python-nsjail-v1",
        "code_sha256": digest,
    }
    receipt = await SandboxJobExecutor().reconcile(_attempt(
        status=ActionAttemptStatus.UNKNOWN,
        ambiguity_evidence=binding, capability=capability,
    ))
    assert receipt.outcome is ExecutionOutcome.FAILED
    jobs.readback_by_binding.assert_awaited_once()
    assert not (tmp_path / "inputs" / ACTION_ID / ATTEMPT_ID).exists()


def test_runtime_composition_registers_executor_without_worker_authority(
    tmp_path: Path,
) -> None:
    database = type("_Database", (), {
        "scope": DatabaseScope(
            actor_user_id=None, org_id=None,
            access_kind=DatabaseAccessKind.AGENT_RUNTIME,
            request_id="sandbox-composition-test",
        ),
    })()
    registry = ExecutorRegistry()
    components = build_sandbox_executor_components(
        runtime_database=database, workspace_root=tmp_path.resolve(),
        runtime_revision="python-nsjail-v1", registry=registry,
    )
    assert components.executor is registry.resolve("code_execute")[1]
    assert components.capability_issuer is not None
    assert not hasattr(components, "worker")
    assert not hasattr(components, "launcher")
