from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.agents import AgentDefinition
from services.agent.runtime.catalog import EffectiveToolset, RuntimeToolCatalog
from services.agent.runtime.domain import (
    ActionAttempt, ActionAttemptStatus, Lease, RuntimeScope, ScopeKind,
)
from services.agent.runtime.executors.contracts import (
    ResultPolicy, canonical_json, canonical_request_hash, safe_result,
)
from services.agent.runtime.executors.read_only import (
    CallableReadCapability, ReadOnlyExecutor, ScopedReadCapability,
)
from services.agent.runtime.executors.capabilities import (
    CapabilityBinding, RestrictedArtifactCapability,
)
from services.agent.runtime.executors.read_registry import (
    READ_TOOL_SPECS, build_read_executor_registry,
)
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.types import (
    AuthorizationRequirement, CancellationSupport, ExecutionMode,
    ExecutorDescriptor, IdempotencySupport,
)
from services.agent.runtime.ports.executor import ExecutionOutcome


def _attempt(request: dict, *, kind: ScopeKind = ScopeKind.USER) -> ActionAttempt:
    now = datetime.now(timezone.utc)
    scope = RuntimeScope(
        kind=kind, scope_id="org-1" if kind is ScopeKind.CHANNEL else "user-1",
        user_id="user-1" if kind is ScopeKind.USER else None,
        org_id="org-1" if kind is ScopeKind.CHANNEL else None,
    )
    return ActionAttempt(
        attempt_id="attempt-1", action_id="action-1", scope=scope,
        attempt_number=1, status=ActionAttemptStatus.DISPATCHING,
        worker_id="worker-1", idempotency_key="idem-1",
        request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token="token-1", expires_at=now + timedelta(minutes=1)),
        started_at=now,
    )


def _descriptor(name: str = "read") -> ExecutorDescriptor:
    return ExecutorDescriptor(
        executor_type=name, revision=1, action_kinds=frozenset({name}),
        mode=ExecutionMode.IMMEDIATE_READ,
        authorization=AuthorizationRequirement.NONE,
        required_capabilities=frozenset({"database.read"}), max_inline_ms=800,
        prepare_timeout_ms=100, submit_timeout_ms=800,
        execution_timeout_ms=5_000, reconcile_timeout_ms=100,
        idempotency=IdempotencySupport.NATIVE,
        cancellation=CancellationSupport.UNSUPPORTED, query_status=False,
        progress=False, callback=False, result_schema_revision=1,
    )


@pytest.mark.asyncio
async def test_read_executor_uses_snapshot_hash_and_bounded_json() -> None:
    request = {"query": "orders", "filters": {"status": "ready"}}
    seen = []

    async def read(snapshot, params):
        seen.append((snapshot.scope.org_id, params))
        return {"summary": "ok", "count": 1, "rows": [{"id": "r1"}]}

    executor = ReadOnlyExecutor(
        executor_type="read", executor_revision=1,
        capability=CallableReadCapability(read),
    )
    receipt = await executor.dispatch(_attempt(request), request)
    assert receipt.outcome is ExecutionOutcome.COMPLETED
    assert receipt.result and receipt.result.data == {
        "summary": "ok", "count": 1, "rows": [{"id": "r1"}],
    }
    assert seen[0][0] is None
    with pytest.raises(ValueError, match="REQUEST_HASH_CONFLICT"):
        await executor.dispatch(_attempt({"query": "different"}), request)


@pytest.mark.asyncio
async def test_scope_capability_rejects_wrong_scope_without_backend_call() -> None:
    called = False

    async def read(snapshot, params):
        nonlocal called
        called = True
        return {"summary": "should not run"}

    capability = ScopedReadCapability(
        read, allowed_scope_kinds=frozenset({"channel"}),
    )
    executor = ReadOnlyExecutor(
        executor_type="read", executor_revision=1, capability=capability,
    )
    receipt = await executor.dispatch(_attempt({}, kind=ScopeKind.USER), {})
    assert receipt.outcome is ExecutionOutcome.FAILED
    assert receipt.external_receipt["error_code"] == "READ_PERMISSION_DENIED"
    assert not called


@pytest.mark.asyncio
async def test_read_capability_failure_is_terminal_not_unknown() -> None:
    async def read(snapshot, params):
        raise RuntimeError("database unavailable")

    executor = ReadOnlyExecutor(
        executor_type="read", executor_revision=1,
        capability=CallableReadCapability(read),
    )
    receipt = await executor.dispatch(_attempt({}), {})
    assert receipt.outcome is ExecutionOutcome.FAILED
    assert receipt.external_receipt["error_code"] == "READ_CAPABILITY_FAILED"


def test_json_result_contract_rejects_nonfinite_custom_and_sensitive_values() -> None:
    with pytest.raises(ValueError, match="NON_FINITE"):
        canonical_json({"value": float("nan")})
    with pytest.raises(ValueError, match="VALUE_INVALID"):
        canonical_json({"value": object()})
    value = safe_result({
        "summary": "ok", "token": "secret", "path": "/var/private/a",
    }, ResultPolicy())
    assert value["token"] == "[redacted]"
    assert value["path"] == "[redacted-path]"


def test_large_result_requires_controlled_artifact_reference() -> None:
    policy = ResultPolicy(max_inline_bytes=256)
    with pytest.raises(ValueError, match="TOO_LARGE"):
        safe_result({"summary": "x", "rows": ["x" * 600]}, policy)
    bounded = safe_result({
        "summary": "x", "artifact_ref": "artifact:a1", "rows": ["x" * 600],
    }, policy)
    assert set(bounded) == {"artifact_ref", "byte_size", "content_hash"}


@pytest.mark.asyncio
async def test_artifact_capability_is_reference_allowlisted() -> None:
    binding = CapabilityBinding(
        action_id="action-1", attempt_id="attempt-1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    capability = RestrictedArtifactCapability(
        binding=binding, allowed_refs=frozenset({"artifact:a1"}),
        _read=lambda operation, ref: _value({"summary": ref}),
    )
    assert await capability.read(
        "action-1", "attempt-1", "get", "artifact:a1",
    ) == {"summary": "artifact:a1"}
    with pytest.raises(PermissionError, match="ARTIFACT_REF_NOT_ALLOWED"):
        await capability.read("action-1", "attempt-1", "get", "artifact:a2")


def test_read_registry_is_unique_and_requires_capabilities() -> None:
    capabilities = {
        name: CallableReadCapability(
            lambda snapshot, request: _value({"summary": name}),
        ) for name in READ_TOOL_SPECS
    }
    registry = build_read_executor_registry(capabilities)
    assert len(registry.descriptors()) == len(READ_TOOL_SPECS)
    assert all(registry.safety_level(name) == "safe" for name in READ_TOOL_SPECS)
    with pytest.raises(ValueError, match="CAPABILITY_MISSING"):
        build_read_executor_registry({})


def test_registry_and_catalog_fail_closed_on_read_safety_or_schema() -> None:
    descriptor = _descriptor("unsafe")
    registry = ExecutorRegistry()
    with pytest.raises(ValueError, match="safety"):
        registry.register_read(descriptor, _Executor(), safety_level="confirm")

    registry.register_read(descriptor, _Executor(), safety_level="safe")
    with pytest.raises(ValueError, match="SCHEMA_MISSING"):
        RuntimeToolCatalog.from_executor_registry(registry)


def test_runtime_catalog_only_contains_registered_read_executors() -> None:
    capabilities = {
        name: CallableReadCapability(
            lambda snapshot, request: _value({"summary": "ok"}),
        ) for name in READ_TOOL_SPECS
    }
    catalog = RuntimeToolCatalog.from_executor_registry(
        build_read_executor_registry(capabilities),
    )
    names = {item.canonical_name for item in catalog.definitions()}
    assert names == set(READ_TOOL_SPECS)
    definition = AgentDefinition(
        canonical_key="test", revision="r1", prompt_revision="p1",
        requested_tool_groups=frozenset({"artifact"}),
        channel_restrictions=frozenset({"web"}),
    )
    toolset = EffectiveToolset.build(
        agent=definition, catalog=catalog, scope="user", channel="web",
        entitled_groups=frozenset({"artifact"}),
        authorized_names=frozenset({"artifact_search"}),
    )
    assert [item.canonical_name for item in toolset.definitions] == ["artifact_search"]


@pytest.mark.asyncio
async def test_accepted_and_unknown_are_not_represented_by_read_executor() -> None:
    executor = ReadOnlyExecutor(
        executor_type="read", executor_revision=1,
        capability=CallableReadCapability(
            lambda snapshot, request: _value({"summary": "ok"}),
        ),
    )
    with pytest.raises(RuntimeError, match="RECONCILIATION_UNSUPPORTED"):
        await executor.reconcile(_attempt({}))
    with pytest.raises(RuntimeError, match="CANCELLATION_UNSUPPORTED"):
        await executor.cancel(_attempt({}))
    # The Runtime ActionLoop owns accepted/unknown recovery; immediate reads
    # are terminal and never self-reconcile or redispatch.


class _Executor:
    async def dispatch(self, attempt, request):
        raise NotImplementedError

    async def reconcile(self, attempt):
        raise NotImplementedError

    async def cancel(self, attempt):
        raise NotImplementedError


async def _value(value):
    return value
