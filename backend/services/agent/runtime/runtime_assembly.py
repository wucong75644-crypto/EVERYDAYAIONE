"""A6 Runtime-owned production assembly and failure-closed readiness."""
from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["RuntimeAssemblyReadiness", "RuntimeProductionAssembly",
           "build_runtime_production_assembly", "first_readiness_error"]
