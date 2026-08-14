import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError, StaleVersionError,
)
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
    def __init__(self, renewal_error=None):
        self.renewals = 0
        self.renewal_error = renewal_error

    async def renew_reconciliation(self, **kwargs):
        self.renewals += 1
        if self.renewal_error is not None:
            raise self.renewal_error
        return type("Receipt", (), {"state_version": kwargs["expected_state_version"] + 1})()


class _Provider:
    def __init__(self, state: ProviderState, delay: float = 0):
        self.state, self.delay = state, delay
        self.cancel_calls = self.reconcile_calls = self.submit_calls = 0
        self.cancel_aborted = False

    async def submit(self, *_args, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("cancel recovery must not submit")

    async def reconcile(self, *_args, **_kwargs):
        self.reconcile_calls += 1
        raise AssertionError("cancel recovery must not reconcile")

    async def cancel(self, attempt, _receipt):
        self.cancel_calls += 1
        if self.delay:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                self.cancel_aborted = True
                raise
        evidence = ({"cancel_confirmed": True, "submission_id": "fact-1", "state_version": 3}
                    if self.state is ProviderState.CANCELLED else {"error_code": "still_unknown"})
        return ProviderReceipt(
            state=self.state, provider="mock", request_hash=attempt.request_hash,
            evidence=evidence,
        )


class _Facts:
    def __init__(self):
        self.finalized = self.unknown = self.accepted = None

    async def finalize(self, **kwargs):
        self.finalized = kwargs

    async def media_cancel_readback_terminal(self, **kwargs):
        self.finalized = {**kwargs, "media_cancel_readback": True}

    async def still_unknown(self, **kwargs):
        self.unknown = kwargs

    async def media_cancel_unproven(self, **kwargs):
        self.unknown = {**kwargs, "media_cancel_unproven": True}

    async def still_accepted(self, **kwargs):
        self.accepted = kwargs


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


@pytest.mark.asyncio
@pytest.mark.parametrize("renewal_error", [
    StaleVersionError("stale_version"),
    FencingTokenMismatchError("ownership_lost"),
])
async def test_cancel_lease_loss_aborts_provider_and_never_finalizes(
    renewal_error: Exception,
) -> None:
    provider = _Provider(ProviderState.CANCELLED, delay=0.05)
    facts = _Facts()
    actions = _Actions(renewal_error)
    assert await _driver(provider, facts, actions, 0.001).reconcile_once()
    assert provider.cancel_calls == 1
    assert provider.cancel_aborted is True
    assert facts.finalized is facts.unknown is None


class _SequenceRecovery:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def claim_action_reconciliation(self, **_kwargs):
        return ActionRecoveryClaim(
            outcome=RecoveryOutcome.CLAIMED,
            operation=ActionRecoveryOperation.CANCEL,
            parent_run_id=RUN_ID, parent_run_status="cancelled",
            parent_run_state_version=7, attempt_id=ATTEMPT_ID,
            execution_token=TOKEN,
            state_version=self.snapshot.attempt["state_version"],
            lease_expires_at=NOW + timedelta(minutes=5),
            snapshot=self.snapshot,
        )


class _KieReadbackProvider:
    def __init__(self, state):
        self.state = state
        self.cancel_calls = self.reconcile_calls = self.submit_calls = 0

    async def submit(self, *_args, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("cancel recovery must never redispatch")

    async def cancel(self, attempt, receipt):
        self.cancel_calls += 1
        evidence = {
            **dict(receipt["evidence"]), "error_code": "CANCEL_UNPROVEN",
            "cancel_unproven": True, "provider_fact_state": "cancel_requested",
            "state_version": 3,
        }
        return ProviderReceipt(
            state=ProviderState.UNKNOWN, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=receipt["provider_task_ref"], evidence=evidence,
        )

    async def reconcile(self, attempt, receipt):
        self.reconcile_calls += 1
        returned_state = (
            ProviderState.UNKNOWN
            if self.state is ProviderState.ACCEPTED else self.state
        )
        evidence = {
            **dict(receipt["evidence"]), "provider_state": self.state.value,
            "state_version": 4,
        }
        if self.state is ProviderState.ACCEPTED:
            evidence["error_code"] = "KIE_CANCEL_UNPROVEN_PROVIDER_PENDING"
        if self.state is ProviderState.CANCELLED:
            evidence["cancel_confirmed"] = True
        return ProviderReceipt(
            state=returned_state, provider="kie",
            request_hash=attempt.request_hash,
            provider_task_ref=receipt["provider_task_ref"],
            result=(
                {"image_urls": ["https://cdn.example/result.png"]}
                if self.state is ProviderState.COMPLETED else {}
            ),
            evidence=evidence,
        )


class _SequenceResolver(_Resolver):
    def resolve(self, snapshot):
        raw = snapshot.attempt
        status = ActionAttemptStatus(raw["status"])
        attempt = ActionAttempt(
            attempt_id=ActionAttemptId(ATTEMPT_ID), action_id=ActionId(ACTION_ID),
            scope=RuntimeScope(kind=ScopeKind.USER,scope_id="user-1",user_id="user-1",org_id="org-1"),
            attempt_number=1,status=status,worker_id="b3",
            idempotency_key=IdempotencyKey("b3-attempt"),request_hash="a"*64,
            lease=Lease(fencing_token=FencingToken("55555555-5555-5555-5555-555555555555"),expires_at=NOW+timedelta(minutes=5)),
            started_at=NOW,accepted_at=NOW,session_id="session-1",run_id=RUN_ID,
            state_version=raw["state_version"],
            external_receipt=raw["external_receipt"],
            ambiguity_evidence=raw.get("ambiguity_evidence", {}),
        )
        return type("Resolved", (), {
            "attempt": attempt, "executor": self.executor,
            "descriptor": type("Descriptor", (), {
                "executor_type": "runtime_media_generation:generate_image",
                "revision": 1,
            })(), "request": {},
        })()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [
    ProviderState.COMPLETED, ProviderState.FAILED,
    ProviderState.CANCELLED, ProviderState.ACCEPTED,
])
async def test_cancel_unproven_switches_to_readback_and_converges_without_submit(
    terminal,
) -> None:
    provider = _KieReadbackProvider(terminal)
    facts, actions = _Facts(), _Actions()
    initial_receipt = {
        "provider": "kie", "provider_task_ref": "kie-task-1",
        "evidence": {
            "submission_id": "provider-fact-1", "state_version": 2,
            "provider_fact_state": "submitted",
            "provider_request_hash": "b" * 64,
            "provider_idempotency_key": "c" * 64,
        },
    }
    accepted = ActionDispatchSnapshot(
        attempt={**SNAPSHOT.attempt, "external_receipt": initial_receipt},
        action=SNAPSHOT.action,
    )
    recovery = _SequenceRecovery(accepted)
    resolver = _SequenceResolver(provider, facts)
    driver = ActionLoopDriver(
        recovery_repository=recovery, action_repository=actions,
        authorization_repository=object(), resolver=resolver, worker_id="b3",
        lease_seconds=120, renew_interval=60, specialist_facts=facts,
    )
    assert await driver.reconcile_once()
    assert facts.unknown["provider_receipt"]["evidence"][
        "cancel_unproven"
    ] is True
    assert facts.unknown["media_cancel_unproven"] is True
    recovery.snapshot = ActionDispatchSnapshot(
        attempt={
            **accepted.attempt, "status": "unknown", "state_version": 5,
            "external_receipt": facts.unknown["provider_receipt"],
            "ambiguity_evidence": facts.unknown["ambiguity_evidence"],
        },
        action={**accepted.action, "status": "unknown"},
    )
    assert await driver.reconcile_once()
    assert provider.cancel_calls == provider.reconcile_calls == 1
    assert provider.submit_calls == 0
    if terminal is ProviderState.ACCEPTED:
        assert facts.unknown["ambiguity_evidence"]["evidence"][
            "error_code"
        ] == "KIE_CANCEL_UNPROVEN_PROVIDER_PENDING"
        assert facts.finalized is None
    else:
        assert facts.finalized["terminal_state"] == terminal.value
        if terminal in {ProviderState.COMPLETED, ProviderState.FAILED}:
            assert facts.finalized["media_cancel_readback"] is True
