from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.application.action_loop import ActionLoopDriver
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
    ExecutorDispatchUnknown,
)


class _Recovery:
    async def claim_action_dispatch(self, **_kwargs):
        return (SNAPSHOT,)


class _Actions:
    def __init__(self) -> None:
        self.completed = False
        self.unknown_evidence = None

    async def renew(self, **_kwargs):
        return type("Receipt", (), {"state_version": 2})()

    async def complete(self, **_kwargs):
        self.completed = True

    async def fail(self, **_kwargs):
        self.completed = True

    async def record_unknown(self, **kwargs):
        self.unknown_evidence = kwargs["ambiguity_evidence"]


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
        assert request["_dispatch_context"] == {
            "dispatch_intent_id": "77777777-7777-7777-7777-777777777777",
            "expected_action_version": 0,
            "expected_attempt_version": 1,
        }
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
        self.attempt = ActionAttempt(
            attempt_id=ActionAttemptId(
                "11111111-1111-1111-1111-111111111111",
            ),
            action_id=ActionId(
                "22222222-2222-2222-2222-222222222222",
            ),
            scope=RuntimeScope(
                kind=ScopeKind.USER, scope_id="scope-1",
                user_id="user-1", org_id=None,
            ),
            attempt_number=1, status=ActionAttemptStatus.CLAIMED,
            worker_id="worker",
            idempotency_key=IdempotencyKey("action:key"),
            request_hash="a" * 64,
            lease=Lease(
                fencing_token=FencingToken(
                    "33333333-3333-3333-3333-333333333333",
                ),
                expires_at=now + timedelta(minutes=1),
            ),
            started_at=now,
        )
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
        "state_version": 0,
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


@pytest.mark.asyncio
async def test_submit_ambiguity_persists_exact_recovery_binding() -> None:
    authorization = _Authorization()
    actions = _Actions()
    evidence = {
        "kind": "SANDBOX_SUBMIT_RESULT_UNKNOWN",
        "external_idempotency_key": f"action:{'2' * 64}",
        "dispatch_intent_id": "77777777-7777-7777-7777-777777777777",
    }

    class _UnknownExecutor(_Executor):
        async def dispatch(self, attempt, request):
            raise ExecutorDispatchUnknown(evidence)

    driver = ActionLoopDriver(
        recovery_repository=_Recovery(),
        action_repository=actions,
        authorization_repository=authorization,
        resolver=_Resolver(_UnknownExecutor(authorization)),
        worker_id="worker",
        renew_interval=60,
    )
    assert await driver.dispatch_once() is True
    assert actions.unknown_evidence == evidence
    assert not actions.completed


@pytest.mark.asyncio
async def test_action_loop_not_executor_issues_dispatch_capability() -> None:
    authorization = _Authorization()
    issued = object()

    class _Issuer:
        def issue(self, **values):
            assert values["phase"] == "dispatch"
            assert values["dispatch_gate"].intent_id
            return {"sandbox_job": issued}

    class _CapabilityConsumer(_Executor):
        async def dispatch(self, attempt, request):
            assert attempt.capabilities == {"sandbox_job": issued}
            return await super().dispatch(attempt, request)

    driver = ActionLoopDriver(
        recovery_repository=_Recovery(),
        action_repository=_Actions(),
        authorization_repository=authorization,
        resolver=_Resolver(_CapabilityConsumer(authorization)),
        capability_issuer=_Issuer(),
        worker_id="worker", renew_interval=60,
    )
    assert await driver.dispatch_once() is True
