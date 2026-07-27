"""AR-14 private PostgreSQL adapter and Runtime loop contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from psycopg import OperationalError

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.application.coordinator import RuntimeLoopCoordinator
from services.agent.runtime.application.model_loop import _result_draft
from services.agent.runtime.domain import StopReason
from services.agent.runtime.infrastructure.postgres.coordinator_recovery import (
    PostgresCoordinatorRecoveryRepository,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ModelResultDraft,
    RecoveryOutcome,
    RunAggregateSnapshot,
    RunRecoveryClaim,
)
from services.agent.runtime.ports.model import (
    ModelOutput,
    ModelOutputKind,
    ModelResponseReceipt,
    ModelStepResult,
    ModelUsage,
    ProviderAttemptOutcome,
    ProviderAttemptReceipt,
)
from services.agent.runtime.ports.repository import (
    MutationOutcome,
    MutationReceipt,
)


RUN_ID = "11111111-1111-1111-1111-111111111111"
TOKEN = "22222222-2222-2222-2222-222222222222"


class _Response:
    def __init__(self, data: object) -> None:
        self.data = data


class _Call:
    def __init__(
        self, database: "_Database", name: str, params: dict[str, object],
    ) -> None:
        self.database = database
        self.name = name
        self.params = params

    async def execute(self) -> _Response:
        self.database.calls.append((self.name, self.params))
        value = self.database.responses[self.name]
        if isinstance(value, BaseException):
            raise value
        return _Response(value)


class _Database:
    def __init__(self, responses: dict[str, object]) -> None:
        self.scope = DatabaseScope(
            None, None, DatabaseAccessKind.WORKER, "ar14-test",
        )
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def rpc(self, name: str, params: dict[str, object]) -> _Call:
        return _Call(self, name, params)


@pytest.mark.asyncio
async def test_run_claim_and_aggregate_are_strictly_typed() -> None:
    database = _Database({
        "claim_next_agent_run": {
            "outcome": "claimed", "entity_id": RUN_ID,
            "execution_token": TOKEN, "state_version": 2,
        },
        "get_agent_run_aggregate": {
            "outcome": "found",
            "run": {
                "id": RUN_ID, "status": "running", "state_version": 2,
                "execution_token": TOKEN,
            },
            "latest_model_step": None,
            "unresolved_model_attempt": None,
            "latest_model_result": None,
            "model_steps": [],
            "actions": [],
        },
    })
    repository = PostgresCoordinatorRecoveryRepository(database)
    claim = await repository.claim_next_run(worker_id="worker-1")
    snapshot = await repository.get_run_aggregate(
        run_id=RUN_ID, worker_id="worker-1", execution_token=TOKEN,
    )

    assert claim.outcome is RecoveryOutcome.CLAIMED
    assert claim.run_id == RUN_ID
    assert snapshot.run["state_version"] == 2


@pytest.mark.asyncio
async def test_uncertain_run_claim_reads_back_worker_token() -> None:
    database = _Database({
        "claim_next_agent_run": OperationalError("response lost"),
        "get_claimed_agent_run": {
            "outcome": "found", "entity_id": RUN_ID,
            "execution_token": TOKEN, "state_version": 2,
        },
    })

    claim = await PostgresCoordinatorRecoveryRepository(
        database,
    ).claim_next_run(worker_id="worker-1")

    assert claim.outcome is RecoveryOutcome.CLAIMED
    assert claim.execution_token == TOKEN
    assert [name for name, _ in database.calls] == [
        "claim_next_agent_run", "get_claimed_agent_run",
    ]


@pytest.mark.asyncio
async def test_action_reconciliation_includes_typed_snapshot() -> None:
    database = _Database({
        "claim_next_agent_action_reconciliation": {
            "outcome": "claimed",
            "attempt_id": "33333333-3333-3333-3333-333333333333",
            "execution_token": TOKEN,
            "state_version": 4,
            "lease_expires_at": datetime(
                2026, 7, 28, tzinfo=timezone.utc,
            ),
            "snapshot": {
                "id": "33333333-3333-3333-3333-333333333333",
                "action_id": "44444444-4444-4444-4444-444444444444",
                "execution_token": TOKEN,
                "request_hash": "a" * 64,
                "action": {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "run_id": RUN_ID,
                    "session_id": "55555555-5555-5555-5555-555555555555",
                    "tool_name": "fake", "arguments": {},
                    "request_hash": "a" * 64,
                    "policy_decision": "preauthorized",
                    "retry_disposition": "retry_safe",
                },
            },
        },
    })
    claim = await PostgresCoordinatorRecoveryRepository(
        database,
    ).claim_action_reconciliation(worker_id="worker-1")

    assert claim.snapshot is not None
    assert claim.snapshot.action["tool_name"] == "fake"


@pytest.mark.asyncio
async def test_cancel_winning_model_terminal_returns_late_receipt_route() -> None:
    database = _Database({
        "complete_model_attempt_with_result": {
            "outcome": "run_cancelled_use_late_receipt",
        },
    })
    outcome = await PostgresCoordinatorRecoveryRepository(
        database,
    ).complete_model_with_result(
        attempt_id="33333333-3333-3333-3333-333333333333",
        run_execution_token=TOKEN,
        expected_attempt_version=1, expected_step_version=0,
        request_hash="a" * 64, response_receipt={},
        response_hash="b" * 64, stop_reason="final",
        provider_stop_reason="stop", usage={}, actual_credits=1,
        result=ModelResultDraft(
            output_kind="text", text_content="done",
            content_hash="c" * 64,
        ),
    )

    assert outcome is RecoveryOutcome.RUN_CANCELLED_USE_LATE_RECEIPT


@pytest.mark.asyncio
async def test_uncertain_reconciliation_claim_reads_back_worker_token() -> None:
    snapshot = {
        "id": "33333333-3333-3333-3333-333333333333",
        "action_id": "44444444-4444-4444-4444-444444444444",
        "execution_token": TOKEN,
        "request_hash": "a" * 64,
        "action": {
            "id": "44444444-4444-4444-4444-444444444444",
            "run_id": RUN_ID,
            "session_id": "55555555-5555-5555-5555-555555555555",
            "tool_name": "fake", "arguments": {},
            "request_hash": "a" * 64,
            "policy_decision": "preauthorized",
            "retry_disposition": "retry_safe",
        },
    }
    database = _Database({
        "claim_next_agent_action_reconciliation": OperationalError(
            "response lost",
        ),
        "get_claimed_agent_action_reconciliation": {
            "outcome": "found",
            "attempt_id": "33333333-3333-3333-3333-333333333333",
            "execution_token": TOKEN, "state_version": 4,
            "lease_expires_at": datetime(
                2026, 7, 28, tzinfo=timezone.utc,
            ),
            "snapshot": snapshot,
        },
    })

    claim = await PostgresCoordinatorRecoveryRepository(
        database,
    ).claim_action_reconciliation(worker_id="worker-1")

    assert claim.outcome is RecoveryOutcome.CLAIMED
    assert [name for name, _ in database.calls] == [
        "claim_next_agent_action_reconciliation",
        "get_claimed_agent_action_reconciliation",
    ]


def test_runtime_scope_cannot_construct_recovery_repository() -> None:
    database = _Database({})
    database.scope = DatabaseScope(
        None, None, DatabaseAccessKind.RUNTIME, "runtime",
    )
    with pytest.raises(ValueError, match="WORKER_SCOPED"):
        PostgresCoordinatorRecoveryRepository(database)


def test_structured_model_result_has_stable_canonical_content_hash() -> None:
    usage = ModelUsage()
    result = ModelStepResult(
        stop_reason=StopReason.STRUCTURED_FINAL,
        provider_stop_reason="stop",
        response_hash="a" * 64,
        response_receipt=ModelResponseReceipt(
            output_kind=ModelOutputKind.STRUCTURED,
            output_characters=13, tool_call_count=0,
            invalid_tool_call_count=0, usage=usage, provider="fake",
        ),
        output=ModelOutput(
            kind=ModelOutputKind.STRUCTURED,
            content='{"b":2,"a":1}', schema_revision="schema-v1",
        ),
        tool_calls=(), usage=usage,
        attempts=(ProviderAttemptReceipt(
            attempt_number=1, provider="fake",
            outcome=ProviderAttemptOutcome.COMPLETED,
        ),),
    )

    draft = _result_draft(result)

    assert draft.structured_content == {"a": 1, "b": 2}
    assert draft.content_hash == (
        "d8497d9d82770a70729261095aa98f7ef5154d7af499f"
        "8037b6ca250296785a6"
    )


class _Recovery:
    def __init__(self, snapshots: list[RunAggregateSnapshot]) -> None:
        self.snapshots = snapshots

    async def claim_next_run(self, **_kwargs: object) -> RunRecoveryClaim:
        return RunRecoveryClaim(
            outcome=RecoveryOutcome.CLAIMED, run_id=RUN_ID,
            execution_token=TOKEN, state_version=1,
        )

    async def get_run_aggregate(
        self, **_kwargs: object,
    ) -> RunAggregateSnapshot:
        return self.snapshots.pop(0)


class _Runtime:
    def __init__(self) -> None:
        self.completed: list[tuple[str, str]] = []

    async def renew_run(self, *_args: object) -> MutationReceipt:
        return MutationReceipt(MutationOutcome.RENEWED)

    async def complete_run(
        self, run_id: str, _token: str, _version: int, result_hash: str,
    ) -> MutationReceipt:
        self.completed.append((run_id, result_hash))
        return MutationReceipt(MutationOutcome.COMPLETED)


class _ModelLoop:
    async def advance(self, **_kwargs: object) -> None:
        return None


class _ActionLoop:
    async def dispatch_once(self) -> bool:
        return False

    async def reconcile_once(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_final_model_result_completes_run_by_authoritative_hash() -> None:
    before = RunAggregateSnapshot(
        run={"id": RUN_ID, "state_version": 1},
        latest_model_step=None, unresolved_model_attempt=None,
        latest_model_result=None, model_steps=(), actions=(),
    )
    after = RunAggregateSnapshot(
        run={"id": RUN_ID, "state_version": 2},
        latest_model_step={
            "id": "55555555-5555-5555-5555-555555555555",
            "status": "completed", "stop_reason": "final",
        },
        unresolved_model_attempt=None,
        latest_model_result={"content_hash": "b" * 64},
        model_steps=(), actions=(),
    )
    runtime = _Runtime()
    coordinator = RuntimeLoopCoordinator(
        recovery_repository=_Recovery([before, after]),
        runtime_repository=runtime,
        model_loop=_ModelLoop(), action_loop=_ActionLoop(),
        worker_id="worker-1",
    )

    assert await coordinator.run_once() is True
    assert runtime.completed == [(RUN_ID, "b" * 64)]
