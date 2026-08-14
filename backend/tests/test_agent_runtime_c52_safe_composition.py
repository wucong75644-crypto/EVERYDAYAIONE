from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.production_composition import (
    build_safe_runtime_composition,
)
from services.agent.runtime.runtime_assembly import CapabilityReadinessState
from services.agent.runtime.status import RuntimeStatusSnapshot, RuntimeStatusState


class _ReadyBroker:
    def readiness(self):
        return SimpleNamespace(ready=True)


def test_safe_composition_registers_only_internal_and_erp_read_tools() -> None:
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        erp_dispatcher_factory=object(),
    )
    names = {
        name
        for descriptor in composition.registry.descriptors()
        for name in descriptor.action_kinds
    }

    assert "local_stock_query" in names
    assert "local_product_identify" in names
    assert len(names) == 23
    assert "file_search" not in names
    assert "erp_execute" not in names
    assert "trigger_erp_sync" not in names
    assert "generate_image" not in names
    assert "generate_video" not in names
    assert "erp_trade_query" in names
    assert "erp_taobao_query" not in names
    assert composition.readiness.production_ready is False
    assert composition.readiness.capabilities["runtime.read"].ready
    assert composition.readiness.capabilities["runtime.erp.read"].ready
    assert composition.readiness.capabilities["runtime.erp.write"].state is CapabilityReadinessState.DISABLED
    assert composition.readiness.capabilities["runtime.media"].state is CapabilityReadinessState.DISABLED


def test_safe_composition_does_not_claim_unwired_remote_erp_read() -> None:
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
    )
    assert composition.readiness.capabilities[
        "runtime.erp.read"
    ].state is CapabilityReadinessState.UNAVAILABLE


def test_safe_composition_registers_only_explicit_data_read_adapters() -> None:
    from services.agent.runtime.production_composition import (
        build_runtime_data_read_registry,
    )

    local_data = object()
    registry = build_runtime_data_read_registry(local_data=local_data)
    names = {
        name for descriptor in registry.descriptors()
        for name in descriptor.action_kinds
    }
    assert names == {"local_data"}
    assert registry.resolve("local_data")[0].mode.value == "local_render"

    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        local_data=local_data,
    )
    assert "local_data" in {
        name for descriptor in composition.registry.descriptors()
        for name in descriptor.action_kinds
    }
    assert composition.readiness.capabilities["runtime.data.read"].ready


def test_safe_composition_keeps_data_read_unavailable_without_injection() -> None:
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
    )
    names = {
        name for descriptor in composition.registry.descriptors()
        for name in descriptor.action_kinds
    }
    assert not names.intersection({"local_data", "file_analyze", "fetch_all_pages"})
    assert composition.readiness.capabilities[
        "runtime.data.read"
    ].state is CapabilityReadinessState.UNAVAILABLE


@pytest.mark.asyncio
async def test_composition_to_executor_dispatches_injected_local_data() -> None:
    from services.agent.runtime.domain import (
        ActionAttempt, ActionAttemptStatus, Lease, RuntimeScope, ScopeKind,
    )
    from services.agent.runtime.executors.contracts import canonical_request_hash
    from services.agent.runtime.ports.executor import ExecutionOutcome

    class LocalDataPort:
        async def prepare(self, attempt, request):
            assert attempt.scope.org_id == "org-a"
            assert request["operation"] == "local_data"
            return {"status": "success", "summary": "trend ready", "count": 2}

    request = {"doc_type": "daily_stats", "query_type": "trend"}
    now = datetime.now(timezone.utc)
    attempt = ActionAttempt(
        attempt_id="attempt-a", action_id="action-a",
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="user-a", user_id="user-a", org_id="org-a",
        ), attempt_number=1, status=ActionAttemptStatus.DISPATCHING,
        worker_id="worker-a", idempotency_key="attempt-a",
        request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token="fence-a", expires_at=now + timedelta(minutes=1)),
        started_at=now,
    )
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        local_data=LocalDataPort(),
    )
    descriptor, executor = composition.registry.resolve("local_data")
    receipt = await executor.dispatch(attempt, request)
    assert descriptor.executor_type == "runtime_artifact_job:local_data"
    assert receipt.outcome is ExecutionOutcome.COMPLETED
    assert receipt.result and receipt.result.data["summary"] == "trend ready"


@pytest.mark.asyncio
async def test_composition_dispatches_file_and_pagination_adapters() -> None:
    from services.agent.runtime.domain import (
        ActionAttempt, ActionAttemptStatus, Lease, RuntimeScope, ScopeKind,
    )
    from services.agent.runtime.executors.contracts import canonical_request_hash
    from services.agent.runtime.ports.executor import ExecutionOutcome

    class ReadPort:
        async def prepare(self, attempt, request):
            return {"status": "success", "summary": request["operation"]}

    requests = {
        "file_analyze": {"file_id": "asset-a"},
        "fetch_all_pages": {"tool_name": "erp_trade_query", "action": "query"},
    }
    now = datetime.now(timezone.utc)
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        file_analyze=ReadPort(), fetch_all_pages=ReadPort(),
    )
    for tool, request in requests.items():
        attempt = ActionAttempt(
            attempt_id=f"attempt-{tool}", action_id=f"action-{tool}",
            scope=RuntimeScope(
                kind=ScopeKind.USER, scope_id="user-a", user_id="user-a", org_id="org-a",
            ), attempt_number=1, status=ActionAttemptStatus.DISPATCHING,
            worker_id="worker-a", idempotency_key=f"attempt-{tool}",
            request_hash=canonical_request_hash(request),
            lease=Lease(fencing_token="fence-a", expires_at=now + timedelta(minutes=1)),
            started_at=now,
        )
        receipt = await composition.registry.resolve(tool)[1].dispatch(attempt, request)
        assert receipt.outcome is ExecutionOutcome.COMPLETED
        assert receipt.result and receipt.result.data["summary"] == tool


def test_safe_composition_requires_model_credential_boundary() -> None:
    without_broker = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        model_call_factory=lambda _snapshot: None,
    )
    assert without_broker.readiness.capabilities["runtime.model"].state is CapabilityReadinessState.UNAVAILABLE

    ready = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        model_call_factory=lambda _snapshot: None,
        credential_broker=_ReadyBroker(),
    )
    assert ready.readiness.capabilities["runtime.model"].ready


def test_safe_composition_does_not_claim_unstarted_runtime_owners() -> None:
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        action_loop=object(),
    )
    for name in (
        "runtime.worker", "runtime.projection", "runtime.authorization",
        "runtime.sandbox", "runtime.external_specialist",
    ):
        assert composition.readiness.capabilities[name].state is CapabilityReadinessState.DISABLED
    assert composition.readiness.capabilities["runtime.action"].ready


def test_production_safe_composition_is_ready_only_with_required_capabilities() -> None:
    required = frozenset({
        "runtime.read", "runtime.erp.read", "runtime.model",
        "runtime.action", "runtime.data.read",
    })
    composition = build_safe_runtime_composition(
        resources=RuntimeReadResources(database=object()),
        model_call_factory=lambda _snapshot: None,
        model_port=object(), action_loop=object(),
        erp_dispatcher_factory=object(), local_data=object(),
        file_analyze=object(), fetch_all_pages=object(),
        production_ready=True, required_capabilities=required,
    )

    assert composition.readiness.ready
    assert composition.readiness.production_ready
    assert composition.readiness.required_capabilities == required
    assert composition.readiness.error_code is None


def test_production_safe_composition_rejects_missing_required_capability() -> None:
    with pytest.raises(
        ValueError, match="RUNTIME_REQUIRED_CAPABILITY_NOT_READY",
    ):
        build_safe_runtime_composition(
            resources=RuntimeReadResources(database=object()),
            model_call_factory=lambda _snapshot: None, model_port=object(),
            action_loop=object(), erp_dispatcher_factory=object(),
            production_ready=True,
            required_capabilities=frozenset({"runtime.data.read"}),
        )


def test_safe_composition_missing_read_service_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="SAFE_READ_SERVICE_WIRING"):
        build_safe_runtime_composition(resources=None)


def test_safe_runtime_components_wire_base_loops_without_starting_them() -> None:
    from services.agent.runtime.composition import build_safe_runtime_components

    components = build_safe_runtime_components(
        object(), SimpleNamespace(agent_runtime_worker_id="c52-test"),
        credential_broker=_ReadyBroker(),
    )
    assert components.model_loop is not None
    assert components.action_loop is not None
    assert components.readiness.capabilities["runtime.model"].ready
    assert components.readiness.capabilities["runtime.action"].ready


def test_status_preserves_capability_states_and_failure_reasons() -> None:
    snapshot = RuntimeStatusSnapshot.from_admin_payload(
        {
            "control": {"production_enabled": False},
            "workers": [],
            "production_ready": False,
            "capabilities": {
                "runtime.read": {"state": "ready"},
                "runtime.media": {
                    "state": "disabled", "summary": {"provider": "not wired"},
                },
                "runtime.model": {
                    "state": "unavailable", "error_code": "CREDENTIAL_BACKEND_NOT_READY",
                },
            },
        },
        tenant_id="org-a",
    )
    output = snapshot.to_dict()
    assert output["capabilities"]["runtime.read"]["state"] == RuntimeStatusState.READY
    assert output["capabilities"]["runtime.media"]["state"] == RuntimeStatusState.DISABLED
    assert output["capabilities"]["runtime.model"]["state"] == RuntimeStatusState.UNAVAILABLE
    assert "CREDENTIAL_BACKEND_NOT_READY" in snapshot.failure_closed_reasons
