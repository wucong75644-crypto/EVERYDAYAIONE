"""Code-owned production composition root for the Agent Runtime Worker."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.runtime_assembly import (
    CapabilityReadiness,
    CapabilityReadinessState,
    RuntimeAssemblyReadiness,
)

if TYPE_CHECKING:
    from services.agent.runtime.production_composition import (
        ProductionRuntimeComponents,
    )


_REQUIRED_CAPABILITIES = frozenset((
    "runtime.read",
    "runtime.model",
    "runtime.action",
    "runtime.sandbox",
))


@dataclass(frozen=True, kw_only=True)
class ProductionModelGatewayComponents:
    model: object
    repository: object


class ProductionCompositionNotReady(RuntimeError):
    """Structured failure for an incomplete production object graph."""

    def __init__(self, readiness: RuntimeAssemblyReadiness) -> None:
        self.readiness = readiness
        super().__init__(
            "RUNTIME_PRODUCTION_COMPOSITION_NOT_READY:"
            f"{readiness.error_code or 'SERVICE_WIRING_NOT_READY'}"
        )


def build_agent_runtime_production_components(
    database: Any, settings: Any, sandbox_registry: ExecutorRegistry,
) -> "ProductionRuntimeComponents":
    """Build the production graph without dynamic hooks or Provider access.

    B3.1 owns only the composition spine. Required production services remain
    deliberately unwired, so this root reports typed, failure-closed readiness
    until a later approved batch supplies those concrete code-owned adapters.
    """
    sandbox_error = _sandbox_error(sandbox_registry)
    if database is None:
        _raise_not_ready("DATABASE_SERVICE_REQUIRED", sandbox_error=sandbox_error)
    if settings is None:
        _raise_not_ready("RUNTIME_SETTINGS_REQUIRED", sandbox_error=sandbox_error)
    if sandbox_error is not None:
        _raise_not_ready(sandbox_error, sandbox_error=sandbox_error)
    _raise_not_ready("SAFETY_SERVICE_WIRING_NOT_READY", sandbox_error=None)


def build_production_model_gateway_components(
    database: Any, settings: Any,
) -> ProductionModelGatewayComponents:
    """Build the explicit Runtime-only Gateway lane or fail closed."""
    if not bool(getattr(settings, "agent_runtime_model_gateway_enabled", False)):
        raise RuntimeError("RUNTIME_MODEL_GATEWAY_DISABLED")
    socket_path = _absolute_socket(
        getattr(settings, "agent_runtime_model_gateway_socket", ""),
        "RUNTIME_MODEL_GATEWAY_SOCKET_REQUIRED",
    )
    health_path = _absolute_socket(
        getattr(settings, "agent_runtime_model_gateway_health_socket", ""),
        "RUNTIME_MODEL_GATEWAY_HEALTH_SOCKET_REQUIRED",
    )
    if socket_path == health_path:
        raise RuntimeError("RUNTIME_MODEL_GATEWAY_SOCKET_CONFLICT")
    release = str(getattr(settings, "agent_runtime_release_revision", "") or "")
    if not release:
        raise RuntimeError("RUNTIME_RELEASE_REVISION_REQUIRED")
    _require_gateway_health(health_path, release)
    from services.agent.runtime.infrastructure.postgres.model_gateway import (
        PostgresModelGatewayRepository,
    )
    from services.agent.runtime.model_gateway.runtime_client import ModelGatewayClient

    repository = PostgresModelGatewayRepository(database)
    return ProductionModelGatewayComponents(
        model=ModelGatewayClient(socket_path, repository),
        repository=repository,
    )


def _absolute_socket(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise RuntimeError(code)
    return value


def _require_gateway_health(path: str, release: str) -> None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(path)
            client.sendall(b"health\n")
            payload = _read_health_payload(client)
        health = json.loads(payload)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("RUNTIME_MODEL_GATEWAY_HEALTH_UNAVAILABLE") from None
    dependencies = health.get("dependencies") if isinstance(health, dict) else None
    valid = (
        isinstance(health, dict)
        and set(health) == {
            "version", "release", "ready", "draining", "dependencies",
            "in_flight", "heartbeat",
        }
        and health["version"] == "agent-model-gateway.v2"
        and health["release"] == release and health["ready"] is True
        and health["draining"] is False and isinstance(dependencies, dict)
        and set(dependencies) == {"db", "kek", "provider_registry", "socket"}
        and all(value == "available" for value in dependencies.values())
        and isinstance(health["in_flight"], int)
        and not isinstance(health["in_flight"], bool)
        and health["in_flight"] >= 0
        and isinstance(health["heartbeat"], (int, float))
        and not isinstance(health["heartbeat"], bool)
    )
    if not valid:
        raise RuntimeError("RUNTIME_MODEL_GATEWAY_HEALTH_NOT_READY")


def _read_health_payload(client: socket.socket) -> bytes:
    payload = bytearray()
    while b"\n" not in payload:
        chunk = client.recv(4096)
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > 16_384:
            raise ValueError
    if not payload.endswith(b"\n") or payload.count(b"\n") != 1:
        raise ValueError
    return bytes(payload)


def _sandbox_error(sandbox_registry: object) -> str | None:
    if not isinstance(sandbox_registry, ExecutorRegistry):
        return "SANDBOX_REGISTRY_REQUIRED"
    try:
        sandbox_registry.resolve("code_execute")
    except LookupError:
        return "SANDBOX_EXECUTOR_REQUIRED"
    return None


def _raise_not_ready(
    error_code: str, *, sandbox_error: str | None,
) -> NoReturn:
    unavailable = CapabilityReadinessState.UNAVAILABLE
    disabled = CapabilityReadinessState.DISABLED
    capabilities = {
        "runtime.read": CapabilityReadiness(
            state=unavailable, error_code="READ_SERVICE_WIRING_NOT_READY",
        ),
        "runtime.model": CapabilityReadiness(
            state=unavailable, error_code="MODEL_SERVICE_WIRING_NOT_READY",
        ),
        "runtime.action": CapabilityReadiness(
            state=unavailable, error_code="ACTION_SERVICE_WIRING_NOT_READY",
        ),
        "runtime.sandbox": CapabilityReadiness(
            state=(
                CapabilityReadinessState.READY
                if sandbox_error is None else unavailable
            ),
            error_code=sandbox_error,
        ),
        "runtime.erp.write": CapabilityReadiness(state=disabled),
        "runtime.media": CapabilityReadiness(state=disabled),
        "runtime.external_specialist": CapabilityReadiness(state=disabled),
        "runtime.object_store": CapabilityReadiness(state=disabled),
        "runtime.scheduler": CapabilityReadiness(state=disabled),
    }
    raise ProductionCompositionNotReady(RuntimeAssemblyReadiness(
        service_wiring_ready=False,
        tenant_binding_ready=False,
        credential_available=False,
        capability_enabled=False,
        probe_passed=False,
        production_ready=False,
        error_code=error_code,
        capabilities=capabilities,
        required_capabilities=_REQUIRED_CAPABILITIES,
    ))


__all__ = [
    "ProductionModelGatewayComponents",
    "ProductionCompositionNotReady",
    "build_agent_runtime_production_components",
    "build_production_model_gateway_components",
]
