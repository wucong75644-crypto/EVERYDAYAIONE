from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.domain import ActionAttempt, ActionAttemptStatus, ActionStatus, Lease, RuntimeScope, ScopeKind
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.materializer import ArtifactMaterializer, MaterializeCheckpoint
from services.agent.runtime.executors.specialist_contracts import (
    CostReservation, NetworkRule, ProviderReceipt, ProviderState,
    validate_public_request,
)
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.reconciler import assert_reconcile_only
from services.agent.runtime.executors.specialist_registry import (
    SPECIALIST_TOOLS, build_specialist_registry, specialist_descriptor,
)
from services.agent.runtime.executors.read_registry import READ_TOOL_SPECS, build_read_executor_registry
from services.agent.runtime.executors.read_only import CallableReadCapability
from services.agent.runtime.executors.sandbox_job import SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.catalog.consistency import build_nonproduction_full_catalog
from services.agent.runtime.costs import InMemoryActionCostLedger
from services.agent.runtime.providers.callback_inbox import CallbackInbox


def _attempt(request: dict[str, object], status: ActionAttemptStatus = ActionAttemptStatus.DISPATCHING) -> ActionAttempt:
    now = datetime.now(timezone.utc)
    scope = RuntimeScope(kind=ScopeKind.USER, scope_id="user-1", user_id="user-1", org_id=None)
    return ActionAttempt(
        attempt_id="attempt-1", action_id="action-1", scope=scope,
        attempt_number=1, status=status, worker_id="worker-1",
        idempotency_key="attempt-1", request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token="fence-1", expires_at=now + timedelta(minutes=1)),
        started_at=now,
    )


class _Provider:
    async def submit(self, attempt, request, *, idempotency_key):
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="fake", request_hash=attempt.request_hash, result={"summary": "ok", "count": 1})

    async def reconcile(self, attempt, receipt):
        return ProviderReceipt(state=ProviderState.COMPLETED, provider="fake", request_hash=attempt.request_hash, result={"summary": "reconciled"})

    async def cancel(self, attempt, receipt):
        return ProviderReceipt(state=ProviderState.CANCELLED, provider="fake", request_hash=attempt.request_hash, evidence={"cancelled": True})


class _UnknownProvider(_Provider):
    async def submit(self, attempt, request, *, idempotency_key):
        raise TimeoutError("response lost")


class _AcceptedProvider(_Provider):
    async def submit(self, attempt, request, *, idempotency_key):
        return ProviderReceipt(state=ProviderState.ACCEPTED, provider="fake", request_hash=attempt.request_hash, provider_task_ref="task-1", evidence={"accepted": True})


async def _value(value):
    return value


def test_specialist_registry_has_exact_23_unique_descriptors() -> None:
    assert len(SPECIALIST_TOOLS) == 23
    registry = build_specialist_registry({name: _Provider() for name in SPECIALIST_TOOLS})
    assert len(registry.descriptors()) == 23
    assert {name for descriptor in registry.descriptors() for name in descriptor.action_kinds} == SPECIALIST_TOOLS
    assert specialist_descriptor("erp_execute").mode.value == "external_action"
    assert specialist_descriptor("generate_video").callback is True


def test_nonproduction_catalog_gate_merges_18_reads_code_and_23_specialists() -> None:
    read_registry = build_read_executor_registry({
        name: CallableReadCapability(lambda snapshot, request: _value({"summary": "ok"}))
        for name in READ_TOOL_SPECS
    })
    specialist_registry = build_specialist_registry({name: _Provider() for name in SPECIALIST_TOOLS})
    sandbox_registry = ExecutorRegistry([(SANDBOX_JOB_DESCRIPTOR, SandboxJobExecutor())])
    catalog = build_nonproduction_full_catalog(read_registry, specialist_registry, sandbox_registry)
    assert len(catalog.definitions()) == 42
    assert SPECIALIST_TOOLS.issubset({item.canonical_name for item in catalog.definitions()})


@pytest.mark.asyncio
async def test_specialist_executor_converts_completed_receipt_and_unknown_is_reconcile_only() -> None:
    request = {"query": "orders"}
    executor = SpecialistExecutor(executor_type="runtime_remote_read", revision=1, provider=_Provider())
    receipt = await executor.dispatch(_attempt(request), request)
    assert receipt.outcome.value == "completed"
    assert receipt.result and receipt.result.data == {"summary": "ok", "count": 1}
    with pytest.raises(RuntimeError, match="RECONCILE_STATUS_REQUIRED"):
        await executor.reconcile(_attempt(request))


def test_callback_cost_and_materialize_contracts_are_idempotent_and_redacted() -> None:
    inbox = CallbackInbox()
    event = inbox.record("kie", "event-1", "corr-1", {"token": "secret", "status": "done"}, signature_valid=True)
    assert event.payload_redacted["token"] == "[redacted]"
    assert inbox.record("kie", "event-1", "corr-1", {"token": "secret", "status": "done"}, signature_valid=True) == event

    ledger = InMemoryActionCostLedger()
    item = CostReservation(action_id="a", attempt_id="t", kind="reserve", reserved_amount=2)
    import asyncio
    asyncio.run(ledger.reserve(item))
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        asyncio.run(ledger.reserve(CostReservation(action_id="a", attempt_id="t", kind="reserve", reserved_amount=3)))

    materializer = ArtifactMaterializer()
    checkpoint = materializer.checkpoint(b"artifact")
    assert checkpoint.status == "materialized" and len(checkpoint.content_hash) == 64
    with pytest.raises(ValueError, match="RETRY_MATERIALIZE_ONLY"):
        materializer.retry_materialize(checkpoint)


def test_secret_boundary_requires_opaque_handles() -> None:
    with pytest.raises(PermissionError, match="SECRET_HANDLE_REQUIRED"):
        validate_public_request({"api_token": "plaintext"})
    validate_public_request({"credential_handle": "secret:kie-prod"})


@pytest.mark.asyncio
async def test_submit_timeout_is_unknown_and_accepted_reconciles_without_resubmit() -> None:
    request = {"prompt": "safe"}
    unknown = SpecialistExecutor(executor_type="runtime_media_generation:generate_image", revision=1, provider=_UnknownProvider())
    lost = await unknown.dispatch(_attempt(request), request)
    assert lost.outcome.value == "unknown"

    accepted_executor = SpecialistExecutor(executor_type="runtime_media_generation:generate_image", revision=1, provider=_AcceptedProvider())
    accepted = await accepted_executor.dispatch(_attempt(request), request)
    assert accepted.outcome.value == "accepted"
    accepted_attempt = _attempt(request)
    accepted_attempt = ActionAttempt(**{
        **accepted_attempt.__dict__, "status": ActionAttemptStatus.ACCEPTED,
        "accepted_at": datetime.now(timezone.utc),
        "external_receipt": accepted.external_receipt,
    })
    reconciled = await accepted_executor.reconcile(accepted_attempt)
    assert reconciled.outcome.value == "completed"


def test_network_and_reconcile_guards_fail_closed() -> None:
    rule = NetworkRule(provider="dashscope", method="POST", paths=frozenset({"/search"}))
    assert rule.allows("dashscope", "POST", "/search")
    with pytest.raises(PermissionError, match="NETWORK_NOT_ALLOWED"):
        rule.assert_allowed("dashscope", "GET", "/search")
    with pytest.raises(ValueError, match="RECONCILE_STATUS_REQUIRED"):
        assert_reconcile_only(ActionStatus.RUNNING, ActionAttemptStatus.DISPATCHING)


def test_226_lanes_are_additive_and_rollbacks_fail_closed() -> None:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for number in range(1, 7):
        migration = next((root / "migrations").glob(f"226_{number:02d}_*.sql"))
        rollback = next((root / "migrations/rollback").glob(f"226_{number:02d}_*_rollback.sql"))
        sql = migration.read_text()
        down = rollback.read_text()
        assert "SECURITY DEFINER" in sql and "SET search_path" in sql
        assert "GRANT EXECUTE" in sql and "REVOKE ALL" in sql
        assert "ROLLBACK_GUARD_FACTS_EXIST" in down
    assert not any("226_" in path.read_text() for path in (root / "migrations").glob("21[2-9]_*.sql"))
