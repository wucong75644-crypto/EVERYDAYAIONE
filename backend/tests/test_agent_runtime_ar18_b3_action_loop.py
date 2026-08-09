import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.domain import (
    ActionAttempt, ActionAttemptId, ActionAttemptStatus, ActionId,
    FencingToken, IdempotencyKey, Lease, RuntimeScope, ScopeKind,
)
from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt, ProviderState,
)
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot, ActionRecoveryClaim, ActionRecoveryOperation,
    RecoveryOutcome,
)


NOW = datetime.now(timezone.utc)
ATTEMPT_ID = "11111111-1111-1111-1111-111111111111"
ACTION_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
TOKEN = "44444444-4444-4444-4444-444444444444"
SNAPSHOT = ActionDispatchSnapshot(
    attempt={"id": ATTEMPT_ID, "action_id": ACTION_ID, "status": "accepted",
             "execution_token": "55555555-5555-5555-5555-555555555555",
             "reconciliation_token": TOKEN, "state_version": 4,
             "request_hash": "a" * 64, "attempt_number": 1,
             "worker_id": "b3", "idempotency_key": "b3-attempt",
             "accepted_at": NOW, "reconciliation_lease_expires_at": NOW + timedelta(minutes=5),
             "lease_expires_at": NOW + timedelta(minutes=5),
             "external_receipt": {"submission_id": "fact-1", "state_version": 2}},
    action={"id": ACTION_ID, "run_id": RUN_ID,
            "session_id": "66666666-6666-6666-6666-666666666666",
            "tool_name": "generate_image", "arguments": {},
            "request_hash": "a" * 64, "policy_decision": "preauthorized",
            "retry_disposition": "retry_after_reconcile", "scope_kind": "user",
            "scope_id": "user-1", "user_id": "user-1", "org_id": "org-1"},
)


class _Recovery:
    async def claim_action_reconciliation(self, **_kwargs):
        return ActionRecoveryClaim(
            outcome=RecoveryOutcome.CLAIMED,
            operation=ActionRecoveryOperation.CANCEL,
            parent_run_id=RUN_ID, parent_run_status="cancelled",
            parent_run_state_version=7, attempt_id=ATTEMPT_ID,
            execution_token=TOKEN, state_version=4,
            lease_expires_at=NOW + timedelta(minutes=5), snapshot=SNAPSHOT,
        )


class _Actions:
    def __init__(self):
        self.renewals = 0

    async def renew_reconciliation(self, **kwargs):
        self.renewals += 1
        return type("Receipt", (), {"state_version": kwargs["expected_state_version"] + 1})()


class _Provider:
    def __init__(self, state: ProviderState, delay: float = 0):
        self.state, self.delay = state, delay
        self.cancel_calls = self.reconcile_calls = self.submit_calls = 0

    async def submit(self, *_args, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("cancel recovery must not submit")

    async def reconcile(self, *_args, **_kwargs):
        self.reconcile_calls += 1
        raise AssertionError("cancel recovery must not reconcile")

    async def cancel(self, attempt, _receipt):
        self.cancel_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        evidence = ({"cancel_confirmed": True, "submission_id": "fact-1", "state_version": 3}
                    if self.state is ProviderState.CANCELLED else {"error_code": "still_unknown"})
        return ProviderReceipt(
            state=self.state, provider="mock", request_hash=attempt.request_hash,
            evidence=evidence,
        )


class _Facts:
    def __init__(self):
        self.finalized = self.unknown = None

    async def finalize(self, **kwargs):
        self.finalized = kwargs

    async def still_unknown(self, **kwargs):
        self.unknown = kwargs


class _Resolver:
    def __init__(self, provider, facts):
        self.specialist_facts = facts
        self.executor = SpecialistExecutor(
            executor_type="runtime_media_generation:generate_image",
            revision=1, provider=provider,
        )

    def resolve(self, _snapshot):
        attempt = ActionAttempt(
            attempt_id=ActionAttemptId(ATTEMPT_ID), action_id=ActionId(ACTION_ID),
            scope=RuntimeScope(kind=ScopeKind.USER,scope_id="user-1",user_id="user-1",org_id="org-1"),
            attempt_number=1,status=ActionAttemptStatus.ACCEPTED,worker_id="b3",
            idempotency_key=IdempotencyKey("b3-attempt"),request_hash="a"*64,
            lease=Lease(fencing_token=FencingToken(TOKEN),expires_at=NOW+timedelta(minutes=5)),
            started_at=NOW,accepted_at=NOW,session_id="session-1",run_id=RUN_ID,
            external_receipt=SNAPSHOT.attempt["external_receipt"],
        )
        return type("Resolved", (), {"attempt": attempt, "executor": self.executor,
                    "descriptor": type("Descriptor", (), {"executor_type": "runtime_media_generation:generate_image", "revision": 1})(),
                    "request": {}})()


def _driver(provider, facts, actions, renew_interval=60):
    resolver = _Resolver(provider, facts)
    return ActionLoopDriver(
        recovery_repository=_Recovery(), action_repository=actions,
        authorization_repository=object(), resolver=resolver, worker_id="b3",
        lease_seconds=120, renew_interval=renew_interval, specialist_facts=facts,
    )


@pytest.mark.asyncio
async def test_cancel_operation_uses_cancel_under_renewed_lease() -> None:
    provider = _Provider(ProviderState.CANCELLED, delay=0.03)
    facts, actions = _Facts(), _Actions()
    assert await _driver(provider, facts, actions, 0.005).reconcile_once()
    assert provider.cancel_calls == 1
    assert provider.reconcile_calls == provider.submit_calls == 0
    assert actions.renewals > 0
    assert facts.finalized["expected_state_version"] > 4


@pytest.mark.asyncio
async def test_cancel_unknown_stays_reconcile_only_with_safe_due_time() -> None:
    provider = _Provider(ProviderState.UNKNOWN)
    facts, actions = _Facts(), _Actions()
    assert await _driver(provider, facts, actions).reconcile_once()
    assert facts.finalized is None
    assert facts.unknown["next_reconcile_at"] > datetime.now(timezone.utc)
    assert provider.reconcile_calls == provider.submit_calls == 0
