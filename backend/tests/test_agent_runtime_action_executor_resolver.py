from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.resolver import (
    PostgresActionExecutorResolver,
)
from services.agent.runtime.executors.types import (
    AuthorizationRequirement,
    CancellationSupport,
    ExecutionMode,
    ExecutorDescriptor,
    IdempotencySupport,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
)


class _Executor:
    async def dispatch(self, attempt, request):
        raise NotImplementedError

    async def reconcile(self, attempt):
        raise NotImplementedError

    async def cancel(self, attempt):
        raise NotImplementedError


def _descriptor(
    *,
    mode: ExecutionMode = ExecutionMode.IMMEDIATE_READ,
    idempotency: IdempotencySupport = IdempotencySupport.NATIVE,
    query_status: bool = False,
) -> ExecutorDescriptor:
    return ExecutorDescriptor(
        executor_type="resource_read",
        revision=1,
        action_kinds=frozenset({"resource.read"}),
        mode=mode,
        authorization=AuthorizationRequirement.NONE,
        required_capabilities=frozenset(),
        max_inline_ms=500,
        prepare_timeout_ms=100,
        submit_timeout_ms=500,
        execution_timeout_ms=1000,
        reconcile_timeout_ms=100,
        idempotency=idempotency,
        cancellation=CancellationSupport.UNSUPPORTED,
        query_status=query_status,
        progress=False,
        callback=False,
        result_schema_revision=1,
    )


def _snapshot() -> ActionDispatchSnapshot:
    now = datetime.now(timezone.utc)
    return ActionDispatchSnapshot(
        attempt={
            "id": "11111111-1111-1111-1111-111111111111",
            "action_id": "22222222-2222-2222-2222-222222222222",
            "attempt_number": 1,
            "status": "claimed",
            "worker_id": "worker",
            "idempotency_key": "action:attempt:1",
            "request_hash": "a" * 64,
            "execution_token": "33333333-3333-3333-3333-333333333333",
            "lease_expires_at": now + timedelta(minutes=1),
            "claimed_at": now,
        },
        action={
            "id": "22222222-2222-2222-2222-222222222222",
            "run_id": "44444444-4444-4444-4444-444444444444",
            "session_id": "55555555-5555-5555-5555-555555555555",
            "tool_name": "resource.read",
            "arguments": {"key": "value"},
            "request_hash": "a" * 64,
            "policy_decision": "preauthorized",
            "retry_disposition": "retry_safe",
            "scope_kind": "user",
            "scope_id": "66666666-6666-6666-6666-666666666666",
            "user_id": "66666666-6666-6666-6666-666666666666",
            "org_id": None,
        },
    )


def test_resolver_uses_registry_as_the_only_mapping_ssot() -> None:
    executor = _Executor()
    resolver = PostgresActionExecutorResolver(
        ExecutorRegistry([(_descriptor(), executor)]),
    )

    resolved = resolver.resolve(_snapshot())

    assert resolved.descriptor.executor_type == "resource_read"
    assert resolved.executor is executor
    assert str(resolved.attempt.action_id) == (
        "22222222-2222-2222-2222-222222222222"
    )
    assert resolved.request == {"key": "value"}


def test_registry_rejects_unrecoverable_side_effect_executor() -> None:
    with pytest.raises(ValueError, match="recovery capability"):
        ExecutorRegistry([(
            _descriptor(
                mode=ExecutionMode.EXTERNAL_ACTION,
                idempotency=IdempotencySupport.NONE,
                query_status=False,
            ),
            _Executor(),
        )])
