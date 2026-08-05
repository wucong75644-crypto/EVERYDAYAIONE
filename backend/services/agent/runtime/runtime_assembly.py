"""A6 Runtime-owned production assembly and failure-closed readiness."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


@dataclass(frozen=True, kw_only=True)
class RuntimeAssemblyReadiness:
    service_wiring_ready: bool
    tenant_binding_ready: bool
    credential_available: bool
    capability_enabled: bool
    probe_passed: bool
    production_ready: bool
    error_code: str | None = None
    capabilities: Mapping[str, "CapabilityReadiness"] = field(default_factory=dict)
    required_capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        missing = self.required_capabilities - self.capabilities.keys()
        if missing:
            raise ValueError("RUNTIME_REQUIRED_CAPABILITY_NOT_REGISTERED")
        if any(
            self.capabilities[name].state is CapabilityReadinessState.DISABLED
            for name in self.required_capabilities
        ):
            raise ValueError("RUNTIME_REQUIRED_CAPABILITY_DISABLED")
        dependencies_ready = all((
            self.service_wiring_ready, self.tenant_binding_ready,
            self.credential_available, self.capability_enabled,
            self.probe_passed,
        ))
        if self.production_ready and not dependencies_ready:
            raise ValueError("RUNTIME_PRODUCTION_READINESS_INCONSISTENT")
        if self.production_ready and any(
            not self.capabilities[name].ready
            for name in self.required_capabilities
        ):
            raise ValueError("RUNTIME_REQUIRED_CAPABILITY_NOT_READY")

    @property
    def ready(self) -> bool:
        return all((self.service_wiring_ready, self.tenant_binding_ready,
                    self.credential_available, self.capability_enabled,
                    self.probe_passed, self.production_ready))


@dataclass(frozen=True, kw_only=True)
class RuntimeProductionAssembly:
    """One explicit object graph; Worker receives no legacy service objects."""

    credential_broker: object
    provider_facts: object
    erp: object
    media: object
    scheduler: object
    artifact: object
    workspace: object
    child_run: object
    provider_resolver: object
    readiness: RuntimeAssemblyReadiness

    def require_ready(self) -> None:
        if not self.readiness.ready:
            raise RuntimeError(self.readiness.error_code or "SERVICE_WIRING_NOT_READY")


class CapabilityReadinessState(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


@dataclass(frozen=True, kw_only=True)
class CapabilityReadiness:
    """Readiness for one capability, independent of production activation."""

    state: CapabilityReadinessState
    error_code: str | None = None

    @property
    def ready(self) -> bool:
        return self.state is CapabilityReadinessState.READY

    def __post_init__(self) -> None:
        if self.state is CapabilityReadinessState.UNAVAILABLE and not self.error_code:
            raise ValueError("RUNTIME_CAPABILITY_UNAVAILABLE_REASON_REQUIRED")

    def to_dict(self) -> dict[str, str]:
        result = {"state": self.state.value}
        if self.error_code:
            result["error_code"] = self.error_code
        return result


def build_runtime_production_assembly(*, credential_broker: object | None,
                                      provider_facts: object | None,
                                      erp: object | None, media: object | None,
                                      scheduler: object | None, artifact: object | None,
                                      workspace: object | None, child_run: object | None,
                                      provider_resolver: object | None) -> RuntimeProductionAssembly:
    """Build only an explicit graph; no defaults, globals, or provider I/O."""
    values = {
        "credential_broker": credential_broker, "provider_facts": provider_facts,
        "erp": erp, "media": media, "scheduler": scheduler,
        "artifact": artifact, "workspace": workspace, "child_run": child_run,
        "provider_resolver": provider_resolver,
    }
    missing = next((name for name, value in values.items() if value is None), None)
    if missing is not None:
        raise RuntimeError(f"SERVICE_WIRING_NOT_READY:{missing}")

    readiness = RuntimeAssemblyReadiness(
        service_wiring_ready=True,
        tenant_binding_ready=False,
        credential_available=_credential_ready(credential_broker),
        capability_enabled=False,
        probe_passed=False,
        production_ready=False,
        error_code="TENANT_PROVIDER_BINDING_NOT_READY",
    )
    return RuntimeProductionAssembly(**values, readiness=readiness)


def _credential_ready(broker: object) -> bool:
    status = getattr(broker, "readiness", None)
    status = status() if callable(status) else status
    return bool(getattr(status, "ready", False))


def first_readiness_error(readiness: RuntimeAssemblyReadiness) -> str | None:
    """Return the stable error in the same order as the readiness contract."""
    for field, code in (
        ("service_wiring_ready", "SERVICE_WIRING_NOT_READY"),
        ("tenant_binding_ready", "TENANT_PROVIDER_BINDING_NOT_READY"),
        ("credential_available", "CREDENTIAL_BACKEND_NOT_READY"),
        ("capability_enabled", "CAPABILITY_NOT_ENABLED"),
        ("probe_passed", "CAPABILITY_PROBE_FAILED"),
        ("production_ready", "PRODUCTION_READINESS_DISABLED"),
    ):
        if not getattr(readiness, field):
            return code
    return None


__all__ = ["CapabilityReadiness", "CapabilityReadinessState",
           "RuntimeAssemblyReadiness", "RuntimeProductionAssembly",
           "build_runtime_production_assembly", "first_readiness_error"]
