from types import SimpleNamespace

import pytest

from services.agent.runtime.runtime_assembly import (
    RuntimeAssemblyReadiness,
    build_runtime_production_assembly,
    first_readiness_error,
)


class NotReadyBroker:
    def readiness(self):
        return SimpleNamespace(ready=False)


def _kwargs(**overrides):
    values = {
        "credential_broker": NotReadyBroker(), "provider_facts": object(),
        "erp": object(), "media": object(), "scheduler": object(),
        "artifact": object(), "workspace": object(), "child_run": object(),
        "provider_resolver": object(),
    }
    values.update(overrides)
    return values


def test_a6_assembly_requires_every_explicit_boundary():
    for name in _kwargs():
        with pytest.raises(RuntimeError, match=f"SERVICE_WIRING_NOT_READY:{name}"):
            build_runtime_production_assembly(**_kwargs(**{name: None}))


def test_a6_readiness_is_ordered_and_mock_boundaries_never_ready():
    assembly = build_runtime_production_assembly(**_kwargs())
    assert assembly.readiness.service_wiring_ready is True
    assert assembly.readiness.tenant_binding_ready is False
    assert assembly.readiness.credential_available is False
    assert assembly.readiness.capability_enabled is False
    assert assembly.readiness.probe_passed is False
    assert assembly.readiness.production_ready is False
    assert assembly.readiness.ready is False
    assert first_readiness_error(assembly.readiness) == "TENANT_PROVIDER_BINDING_NOT_READY"
    with pytest.raises(RuntimeError, match="TENANT_PROVIDER_BINDING_NOT_READY"):
        assembly.require_ready()


def test_a6_readiness_error_order_is_stable():
    readiness = RuntimeAssemblyReadiness(
        service_wiring_ready=True, tenant_binding_ready=True,
        credential_available=True, capability_enabled=False,
        probe_passed=False, production_ready=False,
    )
    assert first_readiness_error(readiness) == "CAPABILITY_NOT_ENABLED"


def test_a6_composition_module_has_no_legacy_or_secret_entrypoint():
    from pathlib import Path

    text = Path(__file__).parents[1].joinpath(
        "services/agent/runtime/runtime_assembly.py",
    ).read_text()
    for forbidden in ("WorkerMediaTasks", "get_oss_service", "RedisClient", "ErpDispatcher", "KieMediaProvider"):
        assert forbidden not in text
