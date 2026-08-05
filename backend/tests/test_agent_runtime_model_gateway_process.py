from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_model_gateway_main import GatewayProcessSettings, PRODUCTION_READY
from services.agent.runtime.model_gateway.provider import provider_registry_available
from services.agent.runtime.model_gateway.service import ModelGatewayService


BASE_ENV = {
    "AGENT_MODEL_GATEWAY_DATABASE_URL": "postgresql://fixture.invalid/gateway",
    "AGENT_MODEL_GATEWAY_WORKER_ID": "gateway-worker",
    "AGENT_MODEL_GATEWAY_RELEASE_REVISION": "a" * 40,
    "AGENT_MODEL_GATEWAY_SOCKET": "/tmp/gateway-contract.sock",
    "AGENT_MODEL_GATEWAY_HEALTH_SOCKET": "/tmp/gateway-health-contract.sock",
    "AGENT_MODEL_GATEWAY_RUNTIME_UID": "1234",
    "AGENT_MODEL_GATEWAY_ISOLATED_HARNESS_ENABLED": "true",
}


def test_process_settings_are_flags_off_and_failure_closed() -> None:
    settings = GatewayProcessSettings.from_environment(BASE_ENV)
    assert settings.isolated_harness_enabled is True
    assert settings.production_enabled is False
    assert PRODUCTION_READY is False

    for key in (
        "AGENT_MODEL_GATEWAY_DATABASE_URL",
        "AGENT_MODEL_GATEWAY_RELEASE_REVISION",
        "AGENT_MODEL_GATEWAY_SOCKET",
    ):
        missing = dict(BASE_ENV)
        missing.pop(key)
        with pytest.raises(RuntimeError, match="GATEWAY_PROCESS_CONFIGURATION_INVALID"):
            GatewayProcessSettings.from_environment(missing)

    production = dict(BASE_ENV, AGENT_MODEL_GATEWAY_PRODUCTION_ENABLED="true")
    with pytest.raises(RuntimeError, match="GATEWAY_PROCESS_CONFIGURATION_INVALID"):
        GatewayProcessSettings.from_environment(production)


def test_process_health_contract_contains_no_paths_or_database_values() -> None:
    service = ModelGatewayService(
        object(), object(), object(), worker_id="worker", release="b" * 40,
    )
    payload = service.health({
        "db": "available", "kek": "available",
        "provider_registry": "available", "socket": "available",
    })
    serialized = json.dumps(payload)

    assert payload["ready"] is True
    assert set(payload) == {
        "version", "release", "ready", "draining", "dependencies",
        "in_flight", "heartbeat",
    }
    for forbidden in ("postgresql://", "/tmp/", "tenant", "request_id", "secret"):
        assert forbidden not in serialized.lower()


def test_only_gateway_boundary_imports_secret_and_provider_constructors() -> None:
    runtime = Path(__file__).parents[1] / "services/agent/runtime"
    files = tuple(runtime.rglob("*.py"))
    local_kek_importers = []
    material_importers = []
    adapter_factory_importers = []
    for path in files:
        text = path.read_text()
        relative = path.relative_to(runtime).as_posix()
        if "LocalKEKProvider" in text:
            local_kek_importers.append(relative)
        if "SecretMaterialService" in text:
            material_importers.append(relative)
        if "import (\n    create_runtime_chat_adapter" in text:
            adapter_factory_importers.append(relative)

    assert local_kek_importers == ["model_gateway/configuration.py"]
    assert material_importers == ["model_gateway/configuration.py"]
    assert adapter_factory_importers == ["model_gateway/provider.py"]
    assert provider_registry_available() is True
