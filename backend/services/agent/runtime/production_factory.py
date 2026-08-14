"""Code-owned production composition root for the Agent Runtime Worker."""

from __future__ import annotations

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
    "ProductionCompositionNotReady",
    "build_agent_runtime_production_components",
]
