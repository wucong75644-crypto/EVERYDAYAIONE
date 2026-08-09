from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.application.action_loop import ActionLoopDriver
from services.agent.runtime.application.action_loop_support import required_time
from services.agent.runtime.application.coordinator import RuntimeLoopCoordinator
from services.agent.runtime.domain import (
    ActionAttempt, ActionAttemptId, ActionAttemptStatus, ActionId,
    FencingToken, IdempotencyKey, Lease, RuntimeScope, ScopeKind,
)
from services.agent.runtime.executors.resource_support import ChildRunService
from services.agent.runtime.executors.specialist_contracts import (
    ProviderReceipt, ProviderState,
)
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.infrastructure.postgres.coordinator_recovery import (
    PostgresCoordinatorRecoveryRepository,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot, ActionRecoveryClaim, ActionRecoveryOperation,
    ChildCancelRecoveryClaim, RecoveryOutcome,
)
from services.agent.runtime.domain.errors import PersistenceContractError


NOW = datetime.now(timezone.utc)
ATTEMPT = "11111111-1111-1111-1111-111111111111"
ACTION = "22222222-2222-2222-2222-222222222222"
TOKEN = "33333333-3333-3333-3333-333333333333"
RUN = "44444444-4444-4444-4444-444444444444"
INTENT = "55555555-5555-5555-5555-555555555555"
SNAPSHOT = ActionDispatchSnapshot(
    attempt={
        "id": ATTEMPT, "action_id": ACTION, "status": "unknown",
        "execution_token": "66666666-6666-6666-6666-666666666666",
        "reconciliation_token": TOKEN, "state_version": 4,
        "request_hash": "a" * 64, "attempt_number": 1,
        "worker_id": "b6", "idempotency_key": "b6-attempt",
        "claimed_at": NOW, "lease_expires_at": NOW + timedelta(minutes=5),
        "reconciliation_lease_expires_at": NOW + timedelta(minutes=5),
        "external_receipt": {}, "ambiguity_evidence": {"kind": "create_unknown"},
    },
    action={
        "id": ACTION, "run_id": RUN,
        "session_id": "77777777-7777-7777-7777-777777777777",
        "tool_name": "image_agent",
        "arguments": {"child_ordinal": 0, "reserved_credits": 7},
        "request_hash": "a" * 64, "policy_decision": "preauthorized",
        "retry_disposition": "retry_after_reconcile", "scope_kind": "user",
        "scope_id": "user-1", "user_id": "user-1", "org_id": "org-1",
    },
)


def test_reconciliation_lease_time_accepts_postgres_json_timestamp() -> None:
    assert required_time("2026-08-09T10:30:00+00:00") == datetime(
        2026, 8, 9, 10, 30, tzinfo=timezone.utc,
    )
    with pytest.raises(RuntimeError, match="RECONCILIATION_LEASE_EXPIRY_INVALID"):
        required_time("2026-08-09T10:30:00")


def _attempt() -> SimpleNamespace:
    return SimpleNamespace(
        run_id=RUN, action_id=ACTION, attempt_id=ATTEMPT,
        request_hash="a" * 64,
        lease=SimpleNamespace(fencing_token=TOKEN),
    )


class _ChildRepository:
    def __init__(self, status="queued") -> None:
        self.status = status
        self.read_params = None

    async def read_child_run(self, **params):
        self.read_params = params
        return {
            "outcome": "readback", "child_run_id": RUN,
            "status": self.status,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "state"), (
    ("queued", "accepted"), ("running", "accepted"),
    ("waiting_actions", "accepted"), ("waiting_interaction", "accepted"),
    ("completed", "completed"), ("failed", "failed"),
    ("cancelled", "cancelled"), ("unknown", "unknown"),
))
async def test_child_readback_normalizes_run_status_and_recovers_missing_id(
    status: str, state: str,
) -> None:
    repository = _ChildRepository(status)
    result = await ChildRunService(repository=repository).readback(
        _attempt(), {
            "reconciliation_token": TOKEN,
            "reconciliation_state_version": 4,
        },
    )
    assert result["state"] == state
    assert repository.read_params["child_run_id"] is None
    assert repository.read_params["parent_action_id"] == ACTION


class _ChildRecovery:
    def __init__(self) -> None:
        self.applied = None

    async def claim_child_cancel(self, **_kwargs):
        return ChildCancelRecoveryClaim(
            outcome=RecoveryOutcome.CLAIMED, intent_id=INTENT,
            claim_token=TOKEN, state_version=3,
        )

    async def apply_child_cancel(self, **kwargs):
        self.applied = kwargs
        return RecoveryOutcome.APPLIED


@pytest.mark.asyncio
async def test_runtime_coordinator_runs_dedicated_child_cancel_scanner() -> None:
    recovery = _ChildRecovery()
    coordinator = RuntimeLoopCoordinator(
        recovery_repository=recovery, runtime_repository=object(),
        model_loop=object(), action_loop=object(), worker_id="b6",
    )
    assert await coordinator.child_cancel_once() is True
    assert recovery.applied == {
        "intent_id": INTENT, "claim_token": TOKEN,
        "expected_state_version": 3, "reason": "parent_run_cancelled",
    }


class _Provider:
    async def cancel(self, attempt, receipt):
        del receipt
        return ProviderReceipt(
            state=ProviderState.CANCELLED, provider="child_run",
            request_hash=attempt.request_hash,
            evidence={
                "cancel_confirmed": True, "fencing_confirmed": True,
                "child_cancel_intent_id": INTENT, "proof_hash": "b" * 64,
            },
        )


class _Resolver:
    def resolve(self, _snapshot):
        attempt = ActionAttempt(
            attempt_id=ActionAttemptId(ATTEMPT), action_id=ActionId(ACTION),
            scope=RuntimeScope(
                kind=ScopeKind.USER, scope_id="user-1",
                user_id="user-1", org_id="org-1",
            ),
            attempt_number=1, status=ActionAttemptStatus.UNKNOWN,
            worker_id="b6", idempotency_key=IdempotencyKey("b6-attempt"),
            request_hash="a" * 64,
            lease=Lease(
                fencing_token=FencingToken(TOKEN),
                expires_at=NOW + timedelta(minutes=5),
            ),
            started_at=NOW, session_id=str(SNAPSHOT.action["session_id"]),
            run_id=RUN, ambiguity_evidence={"kind": "create_unknown"},
        )
        return SimpleNamespace(
            attempt=attempt, request={},
            descriptor=SimpleNamespace(executor_type="runtime_child_run:image_agent"),
            executor=SpecialistExecutor(
                executor_type="runtime_child_run:image_agent", revision=1,
                provider=_Provider(),
            ),
        )


class _ActionRecovery:
    async def claim_action_reconciliation(self, **_kwargs):
        return ActionRecoveryClaim(
            outcome=RecoveryOutcome.CLAIMED,
            operation=ActionRecoveryOperation.CANCEL,
            parent_run_id=RUN, parent_run_status="cancelled",
            parent_run_state_version=2, attempt_id=ATTEMPT,
            execution_token=TOKEN, state_version=4,
            lease_expires_at=NOW + timedelta(minutes=5), snapshot=SNAPSHOT,
        )


class _Actions:
    def __init__(self) -> None:
        self.finalized = None

    async def finalize_child_cancel(self, **kwargs):
        self.finalized = kwargs

    async def renew_reconciliation(self, **kwargs):
        return SimpleNamespace(state_version=kwargs["expected_state_version"] + 1)


class _Facts:
    async def still_unknown(self, **_kwargs):
        raise AssertionError("confirmed child cancel cannot remain unknown")


@pytest.mark.asyncio
async def test_action_cancel_uses_child_proof_finalizer_not_provider_finalizer() -> None:
    actions = _Actions()
    driver = ActionLoopDriver(
        recovery_repository=_ActionRecovery(), action_repository=actions,
        authorization_repository=object(), resolver=_Resolver(),
        worker_id="b6", specialist_facts=_Facts(),
    )
    driver._try_specialist_finalize = _unexpected_finalize  # type: ignore[method-assign]
    assert await driver.reconcile_once() is True
    assert actions.finalized["intent_id"] == INTENT
    assert actions.finalized["proof_hash"] == "b" * 64
    assert actions.finalized["reserved_amount"] == 7


async def _unexpected_finalize(*_args, **_kwargs):
    raise AssertionError("child cancel must not use provider finalizer")


class _ResponseLossDB:
    def __init__(self, readback):
        self.readback = readback
        self.calls = []
        self.scope = DatabaseScope(
            None, None, DatabaseAccessKind.AGENT_RUNTIME, "b6-test",
        )

    def rpc(self, name, params):
        return _ResponseLossCall(self, name, params)


class _ResponseLossCall:
    def __init__(self, database, name, params):
        self.database = database
        self.name = name
        self.params = params

    async def execute(self):
        self.database.calls.append((self.name, self.params))
        if self.name == "claim_next_agent_child_run_cancel_intent_v1":
            from psycopg import OperationalError
            raise OperationalError("response lost")
        return SimpleNamespace(data=self.database.readback)


@pytest.mark.asyncio
async def test_child_cancel_claim_response_loss_uses_authoritative_readback() -> None:
    database = _ResponseLossDB({
        "outcome": "found", "intent": {
            "id": INTENT, "claim_token": TOKEN, "state_version": 8,
        },
    })
    claim = await PostgresCoordinatorRecoveryRepository(database).claim_child_cancel(
        worker_id="b6", lease_seconds=120,
    )
    assert claim == ChildCancelRecoveryClaim(
        outcome=RecoveryOutcome.CLAIMED, intent_id=INTENT,
        claim_token=TOKEN, state_version=8,
    )
    assert [item[0] for item in database.calls] == [
        "claim_next_agent_child_run_cancel_intent_v1",
        "get_claimed_agent_child_run_cancel_intent_v1",
    ]


@pytest.mark.asyncio
async def test_child_cancel_claim_missing_binding_fails_closed() -> None:
    database = _ResponseLossDB({
        "outcome": "found", "intent": {"id": INTENT, "state_version": 8},
    })
    with pytest.raises(PersistenceContractError):
        await PostgresCoordinatorRecoveryRepository(database).claim_child_cancel(
            worker_id="b6", lease_seconds=120,
        )


@pytest.mark.asyncio
async def test_child_cancel_apply_parses_ownership_lost_as_normal_outcome() -> None:
    database = _ResponseLossDB({"outcome": "ownership_lost"})
    outcome = await PostgresCoordinatorRecoveryRepository(database).apply_child_cancel(
        intent_id=INTENT, claim_token=TOKEN,
        expected_state_version=8, reason="parent_run_cancelled",
    )
    assert outcome is RecoveryOutcome.OWNERSHIP_LOST


class _TakeoverRecovery:
    def __init__(self) -> None:
        self.index = 0

    async def claim_child_cancel(self, **_kwargs):
        self.index += 1
        return ChildCancelRecoveryClaim(
            outcome=RecoveryOutcome.CLAIMED,
            intent_id=(INTENT if self.index == 1 else RUN),
            claim_token=TOKEN, state_version=self.index,
        )

    async def apply_child_cancel(self, **_kwargs):
        return (
            RecoveryOutcome.OWNERSHIP_LOST
            if self.index == 1 else RecoveryOutcome.CONFIRMED
        )


@pytest.mark.asyncio
async def test_child_cancel_scanner_continues_after_ownership_lost() -> None:
    recovery = _TakeoverRecovery()
    coordinator = RuntimeLoopCoordinator(
        recovery_repository=recovery, runtime_repository=object(),
        model_loop=object(), action_loop=object(), worker_id="b6",
    )
    assert await coordinator.child_cancel_once() is True
    assert await coordinator.child_cancel_once() is True
    assert recovery.index == 2
