from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.scheduler_cas import (
    MockTenantScopedSchedulerCasStore,
    RuntimeSchedulerCasBridge,
    SchedulerCasError,
)


class Facts:
    async def mutate_resource(self, operation, **params):
        assert operation == "manage_scheduled_task"
        assert params["p_execution_token"] == "execution-a"
        return {"outcome": "bound"}


def _attempt(*, scope_id="user-a", run_id="run-a", key="schedule-key-a"):
    return SimpleNamespace(
        attempt_id="attempt-a", action_id="action-a", run_id=run_id,
        idempotency_key=key, request_hash="c" * 64,
        scope=RuntimeScope(kind=ScopeKind.USER, scope_id=scope_id, user_id=scope_id, org_id="org-a"),
        lease=SimpleNamespace(fencing_token="execution-a", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)),
    )


def _bridge(store=None):
    actual = store or MockTenantScopedSchedulerCasStore()
    return RuntimeSchedulerCasBridge(facts=Facts(), store=actual), actual


@pytest.mark.asyncio
async def test_scheduler_cas_is_not_production_ready_and_requires_tenant_store():
    bridge, _ = _bridge()
    assert bridge.readiness.ready is False
    assert bridge.readiness.error_code == "SCHEDULER_FACTS_STORE_NOT_READY"

    class UnsafeStore(MockTenantScopedSchedulerCasStore):
        tenant_scoped = False

    with pytest.raises(RuntimeError, match="TENANT_SCOPED_STORE_REQUIRED"):
        RuntimeSchedulerCasBridge(facts=Facts(), store=UnsafeStore())


@pytest.mark.asyncio
async def test_scheduler_cas_binds_scope_and_is_idempotent():
    bridge, store = _bridge()
    created = await bridge.mutate(attempt=_attempt(), task_id="task-a", expected_version=0,
                                  operation="create", payload={"schedule": "daily"})
    replay = await bridge.mutate(attempt=_attempt(), task_id="task-a", expected_version=0,
                                 operation="create", payload={"schedule": "daily"})
    assert created["outcome"] == "created"
    assert replay["outcome"] == "already_applied"
    assert created["version"] == replay["version"] == 1
    assert store.production_ready is False


@pytest.mark.asyncio
async def test_scheduler_cross_tenant_access_and_stale_version_fail_closed():
    bridge, _ = _bridge()
    await bridge.mutate(attempt=_attempt(), task_id="task-b", expected_version=0,
                        operation="create", payload={})
    with pytest.raises(SchedulerCasError, match="TENANT_SCOPE_MISMATCH"):
        await bridge.mutate(attempt=_attempt(scope_id="user-b", key="schedule-key-b"), task_id="task-b",
                            expected_version=1, operation="update", payload={"x": 1})
    with pytest.raises(SchedulerCasError, match="VERSION_CONFLICT"):
        await bridge.mutate(attempt=_attempt(key="schedule-key-c"), task_id="task-b",
                            expected_version=0, operation="update", payload={"x": 1})


@pytest.mark.asyncio
async def test_scheduler_concurrent_cas_allows_one_winner():
    bridge, _ = _bridge()
    await bridge.mutate(attempt=_attempt(), task_id="task-c", expected_version=0,
                        operation="create", payload={})
    attempts = [_attempt(key=f"schedule-key-{index}") for index in range(2)]
    results = await pytest_asyncio_gather(
        bridge.mutate(attempt=attempts[0], task_id="task-c", expected_version=1, operation="pause", payload={}),
        bridge.mutate(attempt=attempts[1], task_id="task-c", expected_version=1, operation="resume", payload={}),
    )
    assert sum(result[0] == "ok" for result in results) == 1
    assert sum(result[0] == "error" and "VERSION_CONFLICT" in result[1] for result in results) == 1


async def pytest_asyncio_gather(*coroutines):
    results = []
    for coroutine in await __import__("asyncio").gather(*coroutines, return_exceptions=True):
        if isinstance(coroutine, Exception):
            results.append(("error", str(coroutine)))
        else:
            results.append(("ok", coroutine))
    return results


@pytest.mark.asyncio
async def test_scheduler_missing_context_or_facts_fails_closed():
    bridge, _ = _bridge()
    with pytest.raises(SchedulerCasError, match="RUN_CONTEXT_REQUIRED"):
        await bridge.mutate(attempt=_attempt(run_id=None), task_id="task-d", expected_version=0,
                            operation="create", payload={})
    with pytest.raises(SchedulerCasError, match="FACTS_STORE_NOT_READY"):
        await RuntimeSchedulerCasBridge(facts=object(), store=MockTenantScopedSchedulerCasStore()).mutate(
            attempt=_attempt(), task_id="task-d", expected_version=0, operation="create", payload={})
