from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.domain import (
    ActionAttempt,
    ActionAttemptStatus,
    Lease,
    RuntimeScope,
    ScopeKind,
)
from services.agent.runtime.executors.capabilities import (
    CapabilityBinding,
    RestrictedDatabaseCapability,
    RestrictedNetworkCapability,
    RestrictedSecretCapability,
    RestrictedWorkspaceCapability,
)
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.immediate_read import ImmediateReadExecutor
from services.agent.runtime.executors.types import (
    AuthorizationRequirement,
    CancellationSupport,
    ExecutionMode,
    ExecutorDescriptor,
    IdempotencySupport,
)


class _Executor:
    async def dispatch(self, attempt, request):
        raise NotImplementedError

    async def reconcile(self, attempt):
        raise NotImplementedError

    async def cancel(self, attempt):
        raise NotImplementedError


def _descriptor(
    executor_type: str = "resource_read",
    action_kind: str = "resource.read",
) -> ExecutorDescriptor:
    return ExecutorDescriptor(
        executor_type=executor_type,
        revision=1,
        action_kinds=frozenset({action_kind}),
        mode=ExecutionMode.IMMEDIATE_READ,
        authorization=AuthorizationRequirement.NONE,
        required_capabilities=frozenset({"database.read.resource"}),
        max_inline_ms=500,
        prepare_timeout_ms=100,
        submit_timeout_ms=500,
        execution_timeout_ms=1000,
        reconcile_timeout_ms=100,
        idempotency=IdempotencySupport.NATIVE,
        cancellation=CancellationSupport.UNSUPPORTED,
        query_status=False,
        progress=False,
        callback=False,
        result_schema_revision=1,
    )


def test_registry_is_deterministic_and_fail_closed() -> None:
    registry = ExecutorRegistry(
        [(_descriptor("z", "z.read"), _Executor()),
         (_descriptor("a", "a.read"), _Executor())],
    )

    assert [item.executor_type for item in registry.descriptors()] == ["a", "z"]
    assert registry.resolve("a.read")[0].executor_type == "a"
    with pytest.raises(LookupError, match="not registered"):
        registry.resolve("unknown")


def test_registry_rejects_duplicate_types_and_action_kinds() -> None:
    registry = ExecutorRegistry([(_descriptor(), _Executor())])

    with pytest.raises(ValueError, match="duplicate executor type"):
        registry.register(_descriptor(), _Executor())
    with pytest.raises(ValueError, match="duplicate action kinds"):
        registry.register(_descriptor("other", "resource.read"), _Executor())


@pytest.mark.asyncio
async def test_capabilities_are_action_bound_and_allowlisted() -> None:
    binding = CapabilityBinding(
        action_id="action-1",
        attempt_id="attempt-1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    database = RestrictedDatabaseCapability(
        binding=binding,
        allowed_operations=frozenset({"resource.lookup"}),
        _execute=lambda operation, parameters: _async_value(
            {"summary": parameters["key"]},
        ),
    )
    workspace = RestrictedWorkspaceCapability(
        binding=binding, allowed_refs=frozenset({"artifact:1"}),
        _read=lambda resource_ref: _async_value(b"safe"),
    )
    secret = RestrictedSecretCapability(
        binding=binding, allowed_handles=frozenset({"provider:key"}),
        _resolve=lambda handle: _async_value("resolved"),
    )
    network = RestrictedNetworkCapability(
        binding=binding, allowed_origins=frozenset({"https://api.example"}),
        allowed_methods=frozenset({"POST"}),
        _request=lambda method, origin, body: _async_value((202, b"ok")),
    )

    assert await database.read(
        "action-1", "attempt-1", "resource.lookup", {"key": "value"},
    ) == {"summary": "value"}
    assert await workspace.read_ref(
        "action-1", "attempt-1", "artifact:1",
    ) == b"safe"
    assert await secret.resolve_handle(
        "action-1", "attempt-1", "provider:key",
    ) == "resolved"
    assert await network.request(
        "action-1", "attempt-1", "post", "https://api.example",
    ) == (202, b"ok")
    with pytest.raises(PermissionError, match="BINDING_MISMATCH"):
        await database.read(
            "other", "attempt-1", "resource.lookup", {},
        )
    with pytest.raises(PermissionError, match="ORIGIN_NOT_ALLOWED"):
        await network.request(
            "action-1", "attempt-1", "POST", "https://other.example",
        )


@pytest.mark.asyncio
async def test_representative_read_adapter_uses_only_restricted_operation() -> None:
    now = datetime.now(timezone.utc)
    binding = CapabilityBinding(
        action_id="action-1", attempt_id="attempt-1",
        expires_at=now + timedelta(minutes=1),
    )
    capability = RestrictedDatabaseCapability(
        binding=binding, allowed_operations=frozenset({"resource.lookup"}),
        _execute=lambda operation, parameters: _async_value(
            {"summary": f"{operation}:{parameters['key']}"},
        ),
    )
    attempt = ActionAttempt(
        attempt_id="attempt-1", action_id="action-1",
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="user-1",
            user_id="user-1", org_id=None,
        ),
        attempt_number=1, status=ActionAttemptStatus.DISPATCHING,
        worker_id="worker", idempotency_key="idem",
        request_hash="b" * 64,
        lease=Lease(
            fencing_token="token", expires_at=now + timedelta(minutes=1),
        ),
        started_at=now,
    )

    receipt = await ImmediateReadExecutor(
        capability, "resource.lookup",
    ).dispatch(attempt, {"key": "one"})

    assert receipt.result is not None
    assert receipt.result.summary == "resource.lookup:one"
    assert receipt.request_hash == "b" * 64


async def _async_value(value):
    return value
