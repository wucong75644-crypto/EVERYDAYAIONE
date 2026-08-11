from types import SimpleNamespace

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
