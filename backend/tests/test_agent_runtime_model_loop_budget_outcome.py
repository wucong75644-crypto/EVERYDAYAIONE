"""ModelLoop budget-exhausted closure contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.agent.runtime.application.model_loop import (
    ModelLoopDriver,
    PreparedModelCall,
)
from services.agent.runtime.context import ProviderContextPlan
from services.agent.runtime.ports.coordinator_recovery import RunAggregateSnapshot
from services.agent.runtime.ports.model import (
    ModelInputReceipt,
    ModelRequestOptions,
    ModelStepRequest,
)
from services.agent.runtime.ports.model_attempt import (
    ModelAttemptOutcome,
    ModelAttemptReceipt,
)
from services.agent.runtime.ports.repository import (
    MutationOutcome,
    MutationReceipt,
)


STEP_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
TOKEN = "33333333-3333-4333-8333-333333333333"


class _Runtime:
    def __init__(self, outcome: MutationOutcome = MutationOutcome.FAILED) -> None:
        self.outcome = outcome
        self.creates = 0
        self.failures: list[tuple[object, ...]] = []

    async def create_model_step(
        self, *_args: object, **_kwargs: object,
    ) -> MutationReceipt:
        self.creates += 1
        return MutationReceipt(
            outcome=MutationOutcome.CREATED,
            entity_id=STEP_ID,
            state_version=7,
        )

    async def fail_model_step(self, *values: object) -> MutationReceipt:
        self.failures.append(values)
        return MutationReceipt(outcome=self.outcome, entity_id=STEP_ID)


class _Attempts:
    def __init__(self, outcome: ModelAttemptOutcome) -> None:
        self.outcome = outcome
        self.prepare_calls = 0
        self.dispatch_calls = 0

    async def prepare(self, **_values: object) -> ModelAttemptReceipt:
        self.prepare_calls += 1
        if self.outcome in {
            ModelAttemptOutcome.PREPARED,
            ModelAttemptOutcome.ALREADY_PREPARED,
        }:
            return ModelAttemptReceipt(
                outcome=self.outcome,
                attempt_id="44444444-4444-4444-8444-444444444444",
                state_version=3,
                execution_token=TOKEN,
            )
        return ModelAttemptReceipt(outcome=self.outcome)

    async def start_dispatch(self, **_values: object) -> ModelAttemptReceipt:
        self.dispatch_calls += 1
        return ModelAttemptReceipt(
            outcome=ModelAttemptOutcome.DISPATCHING,
            state_version=4,
        )


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("provider must not be called")


def _request(step_id: str) -> ModelStepRequest:
    context = ProviderContextPlan.build(
        messages=[{"role": "user", "content": "safe fixture"}],
        tools=[], context_epoch_id="context-1", model_step=1,
        stable_prefix_blocks=0,
    )
    return ModelStepRequest(
        model_step_id=step_id, model_id="model-1", request_hash="a" * 64,
        input_receipt=ModelInputReceipt(
            receipt_id="receipt-1", receipt_hash="b" * 64,
            context_plan_hash=context.plan_hash,
        ),
        context_plan=context, model_revision="model-revision-1",
        prompt_revision="prompt-1", tool_catalog_revision="tools-1",
        options=ModelRequestOptions(),
    )


def _plan() -> PreparedModelCall:
    return PreparedModelCall(
        model_id="model-1", provider="provider",
        model_revision="model-revision-1", prompt_revision="prompt-1",
        tool_catalog_revision="tools-1", request_receipt={},
        reserved_credits=10, build_request=_request,
        actual_credits=lambda _result: 0,
        build_actions=lambda _result: ("c" * 64, ()),
    )


def _snapshot(*, with_step: bool = True) -> RunAggregateSnapshot:
    return RunAggregateSnapshot(
        run={"id": RUN_ID},
        latest_model_step=(
            {"id": STEP_ID, "status": "running", "state_version": 7}
            if with_step else None
        ),
        unresolved_model_attempt=None, latest_model_result=None,
        model_steps=(), actions=(),
    )


def _driver(runtime: _Runtime, attempts: _Attempts, model: _Model) -> ModelLoopDriver:
    async def call_factory(_snapshot: RunAggregateSnapshot) -> PreparedModelCall:
        return _plan()

    async def reconcile(_snapshot: RunAggregateSnapshot) -> None:
        raise AssertionError("reconcile must not be called")

    return ModelLoopDriver(
        runtime_repository=runtime, attempt_repository=attempts,
        action_repository=SimpleNamespace(),
        recovery_repository=SimpleNamespace(), model=model,
        call_factory=call_factory, reconciler=reconcile,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_outcome",
    (MutationOutcome.FAILED, MutationOutcome.ALREADY_FAILED),
)
async def test_budget_exhausted_terminalizes_step_without_dispatch(
    failure_outcome: MutationOutcome,
) -> None:
    runtime = _Runtime(failure_outcome)
    attempts = _Attempts(ModelAttemptOutcome.BUDGET_EXHAUSTED)
    model = _Model()

    await _driver(runtime, attempts, model).advance(
        snapshot=_snapshot(), worker_id="worker-1",
        run_id=RUN_ID, run_execution_token=TOKEN,
    )

    assert runtime.failures == [(STEP_ID, TOKEN, 7, "budget_exhausted")]
    assert attempts.dispatch_calls == 0
    assert model.calls == 0


@pytest.mark.asyncio
async def test_rejected_budget_failure_receipt_fails_closed() -> None:
    runtime = _Runtime(MutationOutcome.NOT_READY)
    attempts = _Attempts(ModelAttemptOutcome.BUDGET_EXHAUSTED)
    model = _Model()

    with pytest.raises(
        RuntimeError, match="MODEL_STEP_BUDGET_FAILURE_RECEIPT_REJECTED",
    ):
        await _driver(runtime, attempts, model).advance(
            snapshot=_snapshot(), worker_id="worker-1",
            run_id=RUN_ID, run_execution_token=TOKEN,
        )

    assert len(runtime.failures) == 1
    assert attempts.dispatch_calls == 0
    assert model.calls == 0


@pytest.mark.asyncio
async def test_new_step_budget_exhaustion_uses_created_step_version() -> None:
    runtime = _Runtime()
    attempts = _Attempts(ModelAttemptOutcome.BUDGET_EXHAUSTED)
    model = _Model()

    await _driver(runtime, attempts, model).advance(
        snapshot=_snapshot(with_step=False), worker_id="worker-1",
        run_id=RUN_ID, run_execution_token=TOKEN,
    )

    assert runtime.creates == 1
    assert runtime.failures == [(STEP_ID, TOKEN, 7, "budget_exhausted")]
    assert attempts.dispatch_calls == 0
    assert model.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prepare_outcome",
    (ModelAttemptOutcome.PREPARED, ModelAttemptOutcome.ALREADY_PREPARED),
)
async def test_prepared_path_still_starts_dispatch(
    prepare_outcome: ModelAttemptOutcome,
) -> None:
    runtime = _Runtime()
    attempts = _Attempts(prepare_outcome)
    model = _Model()

    active = await _driver(runtime, attempts, model)._prepare(
        snapshot=_snapshot(), worker_id="worker-1",
        run_id=RUN_ID, run_execution_token=TOKEN,
    )

    assert active is not None
    assert attempts.dispatch_calls == 1
    assert not runtime.failures
    assert model.calls == 0
