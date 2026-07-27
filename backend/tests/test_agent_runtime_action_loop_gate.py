from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.ports.authorization import (
    DispatchGateReceipt,
    DispatchGateOutcome,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
)
from services.agent.runtime.ports.executor import (
    ExecutionOutcome,
    ExecutionReceipt,
)


class _Recovery:
    async def claim_action_dispatch(self, **_kwargs):
        return (SNAPSHOT,)


class _Actions:
    def __init__(self) -> None:
        self.completed = False

    async def renew(self, **_kwargs):
        return type("Receipt", (), {"state_version": 2})()

    async def complete(self, **_kwargs):
        self.completed = True

    async def fail(self, **_kwargs):
        self.completed = True


class _Authorization:
    def __init__(self) -> None:
        self.gated = False

    async def gate(self, **_kwargs):
        self.gated = True
        return DispatchGateReceipt(
            outcome=DispatchGateOutcome.AUTHORIZED,
            intent_id="77777777-7777-7777-7777-777777777777",
            state_version=1,
            external_idempotency_key=f"action:{'2' * 64}",
            recovery_mode="idempotent_replay",
        )


class _Executor:
    def __init__(self, authorization: _Authorization) -> None:
        self.authorization = authorization

    async def dispatch(self, attempt, request):
        assert self.authorization.gated
        assert request["external_idempotency_key"].startswith("action:")
        return ExecutionReceipt(
            outcome=ExecutionOutcome.FAILED,
            request_hash=attempt.request_hash,
            external_receipt={"error_code": "proven_failure"},
        )

    async def reconcile(self, attempt):
        raise NotImplementedError


class _Resolved:
    descriptor = type(
        "Descriptor",
        (),
        {
            "executor_type": "read",
            "revision": 1,
        },
    )()

    def __init__(self, executor):
        self.executor = executor
        self.attempt = type(
            "Attempt",
            (),
            {"request_hash": "a" * 64},
        )()
        self.request = {}


class _Resolver:
    def __init__(self, executor):
        self.executor = executor

    def resolve(self, _snapshot):
        return _Resolved(self.executor)


now = datetime.now(timezone.utc)
SNAPSHOT = ActionDispatchSnapshot(
    attempt={
        "id": "11111111-1111-1111-1111-111111111111",
        "action_id": "22222222-2222-2222-2222-222222222222",
        "execution_token": "33333333-3333-3333-3333-333333333333",
        "request_hash": "a" * 64,
        "state_version": 0,
        "lease_expires_at": now + timedelta(minutes=1),
    },
    action={
        "id": "22222222-2222-2222-2222-222222222222",
        "run_id": "44444444-4444-4444-4444-444444444444",
        "session_id": "55555555-5555-5555-5555-555555555555",
        "tool_name": "resource.read",
        "arguments": {},
        "request_hash": "a" * 64,
        "policy_decision": "preauthorized",
        "policy_revision": "v1",
        "retry_disposition": "retry_safe",
        "policy_receipt_id": "66666666-6666-6666-6666-666666666666",
    },
)


@pytest.mark.asyncio
async def test_action_loop_gates_before_executor_dispatch() -> None:
    authorization = _Authorization()
    actions = _Actions()
    driver = ActionLoopDriver(
        recovery_repository=_Recovery(),
        action_repository=actions,
        authorization_repository=authorization,
        resolver=_Resolver(_Executor(authorization)),
        worker_id="worker",
        renew_interval=60,
    )

    assert await driver.dispatch_once() is True
    assert authorization.gated is True
