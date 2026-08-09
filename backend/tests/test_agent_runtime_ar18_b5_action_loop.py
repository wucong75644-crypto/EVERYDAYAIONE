import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.domain import (
    ActionAttempt, ActionAttemptId, ActionAttemptStatus, ActionId,
    FencingToken, IdempotencyKey, Lease, RuntimeScope, ScopeKind,
)
from services.agent.runtime.domain.errors import StaleVersionError
from services.agent.runtime.domain.sandbox_job import (
    SandboxCleanupStatus, SandboxJobSnapshot, SandboxJobStatus,
    SandboxMaterializationStatus,
)
from services.agent.runtime.executors.capabilities import CapabilityBinding
from services.agent.runtime.executors.sandbox_job import (
    SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot, ActionRecoveryClaim, ActionRecoveryOperation,
    RecoveryOutcome,
)
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobOutcome, SandboxJobReceipt,
)
from services.agent.runtime.sandbox.capability import SandboxJobCapability


NOW = datetime.now(timezone.utc)
ATTEMPT = "11111111-1111-1111-1111-111111111111"
ACTION = "22222222-2222-2222-2222-222222222222"
TOKEN = "33333333-3333-3333-3333-333333333333"
JOB = "44444444-4444-4444-4444-444444444444"
SNAPSHOT = ActionDispatchSnapshot(
    attempt={"id": ATTEMPT, "action_id": ACTION, "status": "accepted",
             "execution_token": "55555555-5555-5555-5555-555555555555",
             "reconciliation_token": TOKEN, "state_version": 4,
             "request_hash": "a" * 64, "attempt_number": 1,
             "worker_id": "b5", "idempotency_key": "b5-attempt",
             "accepted_at": NOW, "lease_expires_at": NOW + timedelta(minutes=5),
             "reconciliation_lease_expires_at": NOW + timedelta(minutes=5),
             "external_receipt": {"sandbox_job_id": JOB}},
    action={"id": ACTION, "run_id": "66666666-6666-6666-6666-666666666666",
            "session_id": "77777777-7777-7777-7777-777777777777",
            "tool_name": "code_execute", "arguments": {}, "request_hash": "a" * 64,
            "policy_decision": "preauthorized", "retry_disposition": "retry_after_reconcile",
            "scope_kind": "user", "scope_id": "user-1", "user_id": "user-1",
            "org_id": "org-1"},
)


def _job(
    status: SandboxJobStatus, *,
    cleanup: SandboxCleanupStatus = SandboxCleanupStatus.NOT_REQUIRED,
    partial: bool = False,
) -> SandboxJobSnapshot:
    confirmed = status is SandboxJobStatus.CANCELLED
    return SandboxJobSnapshot(
        job_id=JOB, action_id=ACTION, attempt_id=ATTEMPT,
        dispatch_intent_id="88888888-8888-8888-8888-888888888888",
        external_idempotency_key="b5", request_hash="a" * 64,
        code_sha256="b" * 64, resource_limits={},
        input_manifest={"schema_revision": 1, "items": []}, status=status,
        state_version=9, fencing_token=1,
        cleanup_status=cleanup,
        materialization_status=SandboxMaterializationStatus.NOT_STARTED,
        queued_at=NOW, terminal_at=NOW if confirmed else None,
        partial_effects={
            "schema_revision": 1,
            "items": ([{"temporary_object_ref": "sandbox-temp:99999999-9999-9999-9999-999999999999"}]
                      if partial else []),
        },
        artifact_manifest={"schema_revision": 1, "items": []},
        cancel_requested_at=NOW if confirmed else None,
        cancel_accepted_at=NOW if confirmed else None,
        cancel_confirmed_at=NOW if confirmed else None,
        receipt_hash="c" * 64 if confirmed else None,
        cleanup_evidence=({"kind": "CLEANUP_CONFIRMED"}
                          if cleanup is SandboxCleanupStatus.COMPLETED else {}),
    )


class _Jobs:
    def __init__(self, status, delay=0, job=None):
        self.status, self.delay, self.job = status, delay, job
        self.calls = 0
        self.aborted = False

    async def request_runtime_cancel(self, **_kwargs):
        self.calls += 1
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.aborted = True
            raise
        return SandboxJobReceipt(
            outcome=(SandboxJobOutcome.CANCELLED
                     if self.status is SandboxJobStatus.CANCELLED
                     else SandboxJobOutcome.CANCEL_REQUESTED),
            job=self.job or _job(self.status),
        )


class _Issuer:
    def __init__(self, jobs):
        self.jobs = jobs
        self.phases = []

    def issue(self, *, attempt, phase, **_kwargs):
        self.phases.append(phase)
        capability = SandboxJobCapability(
            binding=CapabilityBinding(
                action_id=ACTION, attempt_id=ATTEMPT,
                expires_at=NOW + timedelta(minutes=5), obligations=frozenset(),
            ),
            _jobs=self.jobs, _workspace=object(), runtime_revision="sandbox-v1",
            allowed_operations=frozenset({"cancel"}),
        )
        return {"sandbox_job": capability}


class _Recovery:
    async def claim_action_reconciliation(self, **_kwargs):
        return ActionRecoveryClaim(
            outcome=RecoveryOutcome.CLAIMED, operation=ActionRecoveryOperation.CANCEL,
            parent_run_id=str(SNAPSHOT.action["run_id"]), parent_run_status="cancelled",
            parent_run_state_version=2, attempt_id=ATTEMPT,
            execution_token=TOKEN, state_version=4,
            lease_expires_at=NOW + timedelta(minutes=5), snapshot=SNAPSHOT,
        )


class _Actions:
    def __init__(self, renewal_error=None):
        self.renewal_error = renewal_error
        self.finalized = self.unknown = None
        self.renewals = 0

    async def renew_reconciliation(self, **kwargs):
        self.renewals += 1
        if self.renewal_error:
            raise self.renewal_error
        return type("Receipt", (), {"state_version": kwargs["expected_state_version"] + 1})()

    async def finalize_sandbox_cancel(self, **kwargs):
        self.finalized = kwargs

    async def resolve_reconciliation(self, **kwargs):
        self.unknown = kwargs


class _Resolver:
    def resolve(self, _snapshot):
        attempt = ActionAttempt(
            attempt_id=ActionAttemptId(ATTEMPT), action_id=ActionId(ACTION),
            scope=RuntimeScope(kind=ScopeKind.USER, scope_id="user-1",
                               user_id="user-1", org_id="org-1"),
            attempt_number=1, status=ActionAttemptStatus.ACCEPTED,
            worker_id="b5", idempotency_key=IdempotencyKey("b5-attempt"),
            request_hash="a" * 64,
            lease=Lease(fencing_token=FencingToken(TOKEN),
                        expires_at=NOW + timedelta(minutes=5)),
            started_at=NOW, accepted_at=NOW,
            session_id=str(SNAPSHOT.action["session_id"]),
            run_id=str(SNAPSHOT.action["run_id"]),
            external_receipt={"sandbox_job_id": JOB},
        )
        return type("Resolved", (), {"attempt": attempt,
                    "executor": SandboxJobExecutor(),
                    "descriptor": SANDBOX_JOB_DESCRIPTOR, "request": {}})()


def _driver(jobs, actions, issuer, interval=60):
    return ActionLoopDriver(
        recovery_repository=_Recovery(), action_repository=actions,
        authorization_repository=object(), resolver=_Resolver(), worker_id="b5",
        lease_seconds=120, renew_interval=interval, capability_issuer=issuer,
    )


@pytest.mark.asyncio
async def test_cancelled_sandbox_proof_finalizes_under_renewed_action_lease() -> None:
    jobs = _Jobs(SandboxJobStatus.CANCELLED, delay=0.03)
    actions, issuer = _Actions(), _Issuer(jobs)
    assert await _driver(jobs, actions, issuer, 0.005).reconcile_once()
    assert jobs.calls == 1 and actions.renewals > 0
    assert actions.finalized["expected_state_version"] > 4
    assert actions.unknown is None and issuer.phases == ["cancel"]


@pytest.mark.asyncio
async def test_unproven_sandbox_cancel_stays_unknown_without_finalize() -> None:
    jobs = _Jobs(SandboxJobStatus.CANCEL_REQUESTED)
    actions, issuer = _Actions(), _Issuer(jobs)
    assert await _driver(jobs, actions, issuer).reconcile_once()
    assert actions.finalized is None
    assert actions.unknown["resolution"] == "still_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(("cleanup", "partial", "finalized"), (
    (SandboxCleanupStatus.COMPLETED, True, True),
    (SandboxCleanupStatus.FAILED, True, False),
    (SandboxCleanupStatus.UNKNOWN, True, False),
))
async def test_partial_cancel_requires_completed_cleanup_proof(
    cleanup: SandboxCleanupStatus, partial: bool, finalized: bool,
) -> None:
    job = _job(SandboxJobStatus.CANCELLED, cleanup=cleanup, partial=partial)
    jobs = _Jobs(SandboxJobStatus.CANCELLED, job=job)
    actions, issuer = _Actions(), _Issuer(jobs)
    assert await _driver(jobs, actions, issuer).reconcile_once()
    assert (actions.finalized is not None) is finalized
    assert (actions.unknown is not None) is (not finalized)


@pytest.mark.asyncio
async def test_action_lease_loss_aborts_sandbox_cancel_and_never_finalizes() -> None:
    jobs = _Jobs(SandboxJobStatus.CANCELLED, delay=0.05)
    actions, issuer = _Actions(StaleVersionError("stale_version")), _Issuer(jobs)
    assert await _driver(jobs, actions, issuer, 0.001).reconcile_once()
    assert jobs.aborted is True
    assert actions.finalized is actions.unknown is None
