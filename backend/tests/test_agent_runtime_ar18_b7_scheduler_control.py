from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.agent.runtime.scheduler_cas import (
    MockSchedulerControlStore,
    PostgresSchedulerControlStore,
    SchedulerCasError,
    scheduler_control_result,
)
from services.agent.runtime.scheduler_control_payload import (
    normalize_scheduler_control_payload, scheduler_resume_next_run,
)
from services.agent.runtime.application.action_cancel import _settle_specialist
from services.agent.runtime.ports.executor import ExecutionOutcome
from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.provider_adapters import PortBackedProvider
from services.agent.runtime.executors.resource_contracts import (
    RuntimeResourceMutationService, ScheduledTaskService,
)
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor


def _attempt(*, scope_id="org-a", key="scheduler-key"):
    return SimpleNamespace(
        attempt_id="attempt-a", action_id="action-a", run_id="run-a",
        idempotency_key=key, request_hash="a" * 64,
        scope=RuntimeScope(
            kind=ScopeKind.CHANNEL, scope_id=scope_id,
            user_id="user-a", org_id=scope_id,
        ),
        lease=SimpleNamespace(
            fencing_token="execution-a",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
    )


def _payload(name="x"):
    return {
        "name": name, "prompt": "work", "cron_expr": "0 9 * * *",
        "push_target": {"type": "web", "user_id": "user-a"},
    }


@pytest.mark.asyncio
async def test_mock_control_covers_five_operations_and_readback() -> None:
    store = MockSchedulerControlStore()
    attempt = _attempt()
    common = {"dispatch_intent_id": "dispatch-a", "attempt_state_version": 1}
    created = await store.mutate(
        attempt=attempt, task_id="task-a", expected_version=0,
        operation="create", payload=_payload(), **common,
    )
    assert created["outcome"] == "committed"
    updated = await store.mutate(
        attempt=_attempt(key="update"), task_id="task-a", expected_version=1,
        operation="update", payload={"name": "y"}, **common,
    )
    assert updated["task"]["name"] == "y"
    paused = await store.mutate(
        attempt=_attempt(key="pause"), task_id="task-a", expected_version=2,
        operation="pause", payload={}, **common,
    )
    assert paused["task"]["status"] == "paused"
    assert paused["task"]["next_run_at"] is None
    resumed = await store.mutate(
        attempt=_attempt(key="resume"), task_id="task-a", expected_version=3,
        operation="resume", payload={}, **common,
    )
    assert resumed["task"]["status"] == "active"
    assert datetime.fromisoformat(resumed["task"]["next_run_at"]) > datetime.now(timezone.utc)
    deleted = await store.mutate(
        attempt=_attempt(key="delete"), task_id="task-a", expected_version=4,
        operation="delete", payload={}, **common,
    )
    assert deleted["task"]["deleted"] is True
    assert (await store.readback(
        attempt=attempt, idempotency_key="scheduler-key",
        ownership_token="execution-a", expected_state_version=0,
    ))["outcome"] == "readback"


@pytest.mark.asyncio
async def test_mock_control_is_tenant_bound_idempotent_and_single_winner() -> None:
    store = MockSchedulerControlStore()
    attempts = [_attempt(key=f"create-{index}") for index in range(2)]
    results = await __import__("asyncio").gather(*(
        store.mutate(
            attempt=item, task_id="task-a", expected_version=0,
            operation="create", payload=_payload(), dispatch_intent_id="d",
            attempt_state_version=1,
        ) for item in attempts
    ))
    assert sorted(result["outcome"] for result in results) == ["cas_conflict", "committed"]
    replay = await store.mutate(
        attempt=attempts[0], task_id="task-a", expected_version=0,
        operation="create", payload=_payload(), dispatch_intent_id="d",
        attempt_state_version=1,
    )
    assert replay["outcome"] == "readback"
    with pytest.raises(SchedulerCasError, match="IDEMPOTENCY_CONFLICT"):
        await store.mutate(
            attempt=_attempt(key="create-0"), task_id="task-other", expected_version=0,
            operation="create", payload=_payload(), dispatch_intent_id="d",
            attempt_state_version=1,
        )
    with pytest.raises(SchedulerCasError, match="TENANT_SCOPE_MISMATCH"):
        await store.mutate(
            attempt=_attempt(scope_id="org-b", key="cross"), task_id="task-a",
            expected_version=1, operation="pause", payload={},
            dispatch_intent_id="d", attempt_state_version=1,
        )


@pytest.mark.asyncio
async def test_cancel_before_commit_blocks_and_after_commit_is_readback() -> None:
    store = MockSchedulerControlStore()
    attempt = _attempt(key="cancel-before")
    cancelled = await store.cancel(attempt=attempt, idempotency_key=attempt.idempotency_key)
    assert cancelled["outcome"] == "cancelled"
    blocked = await store.mutate(
        attempt=attempt, task_id="task-a", expected_version=0,
        operation="create", payload=_payload(), dispatch_intent_id="d",
        attempt_state_version=1,
    )
    assert blocked["outcome"] == "readback"

    committed_attempt = _attempt(key="cancel-after")
    committed = await store.mutate(
        attempt=committed_attempt, task_id="task-b", expected_version=0,
        operation="create", payload=_payload(), dispatch_intent_id="d",
        attempt_state_version=1,
    )
    assert committed["outcome"] == "committed"
    after = await store.cancel(
        attempt=committed_attempt, idempotency_key=committed_attempt.idempotency_key,
    )
    assert after["outcome"] == "committed_readback"


def test_scheduler_provider_state_mapping_is_failure_closed() -> None:
    assert scheduler_control_result({"outcome": "committed"})["state"] == "completed"
    assert scheduler_control_result({"outcome": "cancelled"})["state"] == "cancelled"
    assert scheduler_control_result({"outcome": "cas_conflict"})["state"] == "failed"
    assert scheduler_control_result({
        "outcome": "readback", "receipt_outcome": "cas_conflict",
    })["state"] == "failed"
    assert scheduler_control_result({"outcome": "reconcile_required"})["state"] == "unknown"


@pytest.mark.asyncio
async def test_specialist_dispatch_preserves_scheduler_gate_context_only() -> None:
    class _Facts:
        def __init__(self):
            self.context = None

        async def mutate_scheduler_task(self, **kwargs):
            self.context = kwargs
            return {
                "state": "completed", "summary": "scheduled",
                "task_id": kwargs["task_id"],
            }

        async def reconcile_scheduler_task(self, **kwargs):
            self.context = kwargs
            return {"state": "completed", "summary": "read back"}

    facts = _Facts()
    scheduler = ScheduledTaskService(store=SimpleNamespace(), facts=facts)
    resources = RuntimeResourceMutationService(
        workspace=SimpleNamespace(), scheduler=scheduler,
    )
    provider = PortBackedProvider(
        port=resources, operation="manage_scheduled_task", provider="scheduler",
    )
    executor = SpecialistExecutor(
        executor_type="runtime_scheduled_task:manage_scheduled_task",
        revision=1, provider=provider,
    )
    request = {
        "operation": "create", "payload": _payload(),
        "_dispatch_context": {
            "dispatch_intent_id": "dispatch-a",
            "expected_attempt_version": 7,
        },
    }
    attempt = _attempt()
    attempt.request_hash = canonical_request_hash(request)
    receipt = await executor.dispatch(attempt, request)
    assert receipt.outcome is ExecutionOutcome.COMPLETED
    assert facts.context["dispatch_intent_id"] == "dispatch-a"
    assert facts.context["attempt_state_version"] == 7
    assert "_dispatch_context" not in receipt.result.data
    reconciled = await provider.reconcile(attempt, {
        "reconciliation_token": "reconcile-a",
        "reconciliation_state_version": 9,
    })
    assert reconciled.state.value == "completed"
    assert facts.context["ownership_token"] == "reconcile-a"
    assert facts.context["expected_state_version"] == 9


class _RpcCall:
    def __init__(self, result):
        self.result = result

    async def execute(self):
        return SimpleNamespace(data=self.result)


class _Database:
    scope = DatabaseScope(
        actor_user_id=None, org_id="00000000-0000-0000-0000-000000000001",
        access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="b7-test",
    )

    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RpcCall({"outcome": "committed", "state_version": 1})


class _ResumeDatabase(_Database):
    def rpc(self, name, params):
        self.calls.append((name, params))
        if name == "get_agent_runtime_scheduled_task_resume_context_v1":
            return _RpcCall({
                "schedule_type": "daily", "cron_expr": "0 9 * * *",
                "timezone": "Asia/Shanghai", "schedule_hash": "b" * 64,
                "calculation_revision": "services.scheduler.cron_utils.calc_next_run:v1",
            })
        return _RpcCall({"outcome": "committed", "state_version": 2})


@pytest.mark.asyncio
async def test_postgres_control_store_uses_only_narrow_worker_rpc() -> None:
    database = _Database()
    store = PostgresSchedulerControlStore(database)
    await store.mutate(
        attempt=_attempt(), task_id="task-a", expected_version=0,
        operation="create", payload=_payload(), dispatch_intent_id="dispatch-a",
        attempt_state_version=1,
    )
    assert database.calls[0][0] == "mutate_agent_runtime_scheduled_task_control_v1"
    assert database.calls[0][1]["p_dispatch_intent_id"] == "dispatch-a"
    assert all("scheduled_tasks" not in name for name, _ in database.calls)


@pytest.mark.asyncio
async def test_postgres_resume_uses_authoritative_context_before_atomic_mutation() -> None:
    database = _ResumeDatabase()
    store = PostgresSchedulerControlStore(database)
    await store.mutate(
        attempt=_attempt(), task_id="task-a", expected_version=1,
        operation="resume", payload={"next_run_at": "model-value-forbidden"},
        dispatch_intent_id="dispatch-a", attempt_state_version=1,
    )
    assert [name for name, _ in database.calls] == [
        "get_agent_runtime_scheduled_task_resume_context_v1",
        "mutate_agent_runtime_scheduled_task_control_v1",
    ]
    mutation = database.calls[1][1]
    assert mutation["p_payload"] == {}
    assert mutation["p_resume_schedule_hash"] == "b" * 64
    assert datetime.fromisoformat(mutation["p_resume_next_run_at"]) > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_postgres_control_store_routes_readback_cancel_and_reconcile() -> None:
    database = _Database()
    store = PostgresSchedulerControlStore(database)
    attempt = _attempt()
    await store.readback(
        attempt=attempt, idempotency_key=attempt.idempotency_key,
        ownership_token="execution-a", expected_state_version=0,
    )
    await store.cancel(
        attempt=attempt, idempotency_key=attempt.idempotency_key,
        ownership_token="reconcile-a", expected_state_version=2,
    )
    await store.reconcile(
        attempt=attempt, idempotency_key=attempt.idempotency_key,
        ownership_token="reconcile-a", expected_state_version=2,
    )
    assert [name for name, _ in database.calls] == [
        "read_agent_runtime_scheduled_task_control_v1",
        "cancel_agent_runtime_scheduled_task_control_v1",
        "reconcile_agent_runtime_scheduled_task_control_v1",
    ]
    assert database.calls[2][1]["p_execution_token"] == "reconcile-a"
    assert database.calls[2][1]["p_expected_state_version"] == 2


def test_scheduler_update_payload_rejects_invalid_partial_state() -> None:
    with pytest.raises(SchedulerCasError, match="MAX_CREDITS_INVALID"):
        normalize_scheduler_control_payload("update", {"max_credits": -1})
    with pytest.raises(SchedulerCasError, match="SCHEDULE_TYPE_REQUIRED"):
        normalize_scheduler_control_payload("update", {"cron_expr": "0 8 * * *"})
    with pytest.raises(SchedulerCasError, match="PROMPT_INVALID"):
        normalize_scheduler_control_payload("update", {"prompt": ""})
    with pytest.raises(SchedulerCasError, match="TIMEZONE_INVALID"):
        normalize_scheduler_control_payload("update", {
            "schedule_type": "cron", "cron_expr": "0 8 * * *",
            "timezone": "Not/AZone",
        })


def test_resume_time_uses_authoritative_schedule_and_rejects_expired_once() -> None:
    base = datetime(2030, 1, 1, tzinfo=timezone.utc)
    next_run = scheduler_resume_next_run({
        "schedule_type": "weekly", "cron_expr": "0 9 * * 1,3,5",
        "timezone": "Asia/Shanghai",
    }, base=base)
    assert datetime.fromisoformat(next_run) > base
    with pytest.raises(SchedulerCasError, match="ONCE_RESUME_EXPIRED"):
        scheduler_resume_next_run({
            "schedule_type": "once", "run_at": "2029-12-31T23:00:00+00:00",
            "timezone": "Asia/Shanghai",
        }, base=base)


@pytest.mark.asyncio
async def test_committed_before_cancel_finalizes_completed_exactly_once() -> None:
    class _Driver:
        def __init__(self):
            self.calls = 0

        async def _try_specialist_finalize(self, *args, **kwargs):
            self.calls += 1
            assert kwargs["reconciliation"] is True
            return True

    driver = _Driver()
    snapshot = SimpleNamespace(
        attempt={"id": "attempt-a", "request_hash": "a" * 64},
        action={"arguments": {"reserved_credits": 3}},
    )
    receipt = SimpleNamespace(outcome=ExecutionOutcome.COMPLETED)
    await _settle_specialist(
        driver, snapshot, receipt, "reconcile-a", 2, child_run=False,
    )
    assert driver.calls == 1
