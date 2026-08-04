"""Tenant-scoped production service contracts for AR-17.4.

This module contains only secret-free binding resolution.  Provider clients and
legacy services are injected behind a builder; no global credential fallback is
permitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from services.agent.runtime.domain import RuntimeScope
from services.agent.runtime.executors.provider_adapters import (
    TenantProviderBinding, TenantProviderResolver,
)
from services.agent.runtime.executors.specialist_contracts import SpecialistProvider


@dataclass(frozen=True, kw_only=True)
class ReadinessResult:
    service_wiring_ready: bool
    tenant_binding_ready: bool
    credential_available: bool
    capability_enabled: bool
    probe_passed: bool
    provider_revision: str | None = None
    error_code: str | None = None

    @property
    def ready(self) -> bool:
        return all((
            self.service_wiring_ready, self.tenant_binding_ready,
            self.credential_available, self.capability_enabled,
            self.probe_passed,
        ))


@dataclass(frozen=True, kw_only=True)
class ProductionServicePorts:
    """Explicit service ports; no legacy discovery or global fallback."""

    erp_dispatcher: object
    erp_search: object
    transport: object
    media_task: object
    artifact: object
    workspace: object
    scheduler: object
    child_run: object
    local_data: object | None = None
    file_analyze: object | None = None
    fetch_all_pages: object | None = None
    sync: object | None = None

    def __post_init__(self) -> None:
        for name in (
            "erp_dispatcher", "erp_search", "transport", "media_task",
            "artifact", "workspace", "scheduler", "child_run",
        ):
            if getattr(self, name) is None:
                raise RuntimeError(f"SERVICE_WIRING_NOT_READY:{name}")


@dataclass(frozen=True, kw_only=True)
class ProductionServiceBundle:
    """Internal composition result shared by all Runtime executors."""

    ports: ProductionServicePorts
    provider_resolver: TenantProviderResolver
    readiness: ReadinessResult
    service_readiness: Mapping[str, ReadinessResult] | None = None
    credential_broker: object | None = None

    def require_ready(self) -> None:
        if not self.readiness.ready:
            raise RuntimeError(
                self.readiness.error_code or "PRODUCTION_SERVICE_NOT_READY",
            )


def build_production_service_bundle(
    *, ports: ProductionServicePorts, provider_resolver: TenantProviderResolver,
    readiness: ReadinessResult, credential_broker: object | None = None,
) -> ProductionServiceBundle:
    """Validate the one explicit service assembly boundary."""
    if provider_resolver is None:
        raise RuntimeError("SERVICE_WIRING_NOT_READY:provider_resolver")
    if not readiness.service_wiring_ready:
        raise RuntimeError(readiness.error_code or "SERVICE_WIRING_NOT_READY")
    if readiness.credential_available and credential_broker is None:
        raise RuntimeError("CREDENTIAL_BROKER_REQUIRED")
    return ProductionServiceBundle(
        ports=ports, provider_resolver=provider_resolver,
        readiness=readiness, credential_broker=credential_broker,
    )


@dataclass(frozen=True, kw_only=True)
class FactBoundWorkspacePort:
    """Workspace adapter requiring the Runtime facts owner on every mutation."""

    service: object
    facts: object

    def __post_init__(self) -> None:
        if getattr(self.service, "facts", None) is not self.facts:
            raise RuntimeError("WORKSPACE_FACTS_OWNER_REQUIRED")

    async def delete(self, resource_id, relative_path, oss_key, *, attempt=None):
        _require_run_scope(attempt, "WORKSPACE")
        return await self.service.delete(
            resource_id, relative_path, oss_key, attempt=attempt,
        )

    async def restore(self, resource_id, relative_path, oss_key, *, attempt=None):
        _require_run_scope(attempt, "WORKSPACE")
        return await self.service.restore(
            resource_id, relative_path, oss_key, attempt=attempt,
        )


@dataclass(frozen=True, kw_only=True)
class FactBoundArtifactPort:
    """Artifact adapter requiring facts, object-store verification and lineage."""

    service: object
    facts: object

    def __post_init__(self) -> None:
        if getattr(self.service, "facts", None) is not self.facts:
            raise RuntimeError("ARTIFACT_FACTS_OWNER_REQUIRED")
        if getattr(self.service, "objects", None) is None:
            raise RuntimeError("ARTIFACT_OBJECT_STORE_WIRING_NOT_READY")

    async def prepare(self, attempt, request):
        _require_run_scope(attempt, "ARTIFACT")
        if not request.get("artifact_id"):
            raise RuntimeError("ARTIFACT_LINEAGE_ID_REQUIRED")
        return await self.service.prepare(attempt, request)


@dataclass(frozen=True, kw_only=True)
class FactBoundChildRunPort:
    """Child Run adapter that cannot bypass the facts repository."""

    service: object
    facts: object

    def __post_init__(self) -> None:
        if getattr(self.service, "repository", None) is not self.facts:
            raise RuntimeError("CHILD_RUN_FACTS_OWNER_REQUIRED")

    async def create(self, attempt, request):
        _require_run_scope(attempt, "CHILD_RUN")
        return await self.service.create(attempt, request)

    async def readback(self, attempt, receipt):
        _require_run_scope(attempt, "CHILD_RUN")
        return await self.service.readback(attempt, receipt)

    async def complete(self, attempt, receipt, result):
        _require_run_scope(attempt, "CHILD_RUN")
        return await self.service.complete(attempt, receipt, result)

    async def cancel(self, attempt, receipt):
        _require_run_scope(attempt, "CHILD_RUN")
        return await self.service.cancel(attempt, receipt)


def _require_run_scope(attempt: object, service: str) -> None:
    scope = getattr(attempt, "scope", None)
    if scope is None or not getattr(scope, "scope_id", None):
        raise RuntimeError(f"{service}_TENANT_SCOPE_REQUIRED")
    if not getattr(attempt, "run_id", None):
        raise RuntimeError(f"{service}_RUN_CONTEXT_REQUIRED")


@dataclass(frozen=True, kw_only=True)
class CredentialLease:
    """Opaque credential metadata; secret material never crosses this type."""

    handle: str
    provider: str
    revision: str


class CredentialResolver(Protocol):
    async def resolve(
        self, *, scope: RuntimeScope, credential_handle: str, purpose: str,
    ) -> CredentialLease: ...


class CapabilityResolver(Protocol):
    async def readiness(
        self, *, scope: RuntimeScope, capability: str,
        provider_revision: str,
    ) -> ReadinessResult: ...


ProviderBuilder = Callable[[RuntimeScope, str, str | None], SpecialistProvider]


class PostgresTenantProviderResolver(TenantProviderResolver):
    """Resolve secret-free binding facts through one narrow SECURITY DEFINER RPC."""

    def __init__(
        self, database, *, catalog_revision: str,
        builders: Mapping[str, ProviderBuilder],
    ) -> None:
        self._database = database
        self._catalog_revision = catalog_revision
        self._builders = dict(builders)

    async def resolve(
        self, scope: RuntimeScope, tool_name: str,
    ) -> TenantProviderBinding:
        result = await self._database.rpc(
            "resolve_agent_runtime_tenant_provider_binding", {
                "p_catalog_revision": self._catalog_revision,
                "p_tool_name": tool_name,
                "p_scope_kind": scope.kind.value,
                "p_scope_id": scope.scope_id,
                "p_org_id": scope.org_id,
            },
        ).execute()
        payload = result.data if isinstance(result.data, Mapping) else {}
        if payload.get("outcome") != "found":
            raise RuntimeError("TENANT_PROVIDER_BINDING_NOT_FOUND")
        if payload.get("ready") is not True:
            raise RuntimeError(_readiness_error(payload))
        revision = _text(payload, "provider_revision")
        readiness_hash = _text(payload, "readiness_hash")
        builder = self._builders.get(tool_name)
        if builder is None:
            raise RuntimeError("SERVICE_WIRING_NOT_READY")
        provider = builder(scope, tool_name, _optional_text(payload.get("credential_handle")))
        return TenantProviderBinding(
            provider=provider, provider_revision=revision,
            readiness_hash=readiness_hash,
            credential_handle=_optional_text(payload.get("credential_handle")),
            ready=True,
        )


def _readiness_error(payload: Mapping[str, object]) -> str:
    for key, code in (
        ("service_wiring_ready", "SERVICE_WIRING_NOT_READY"),
        ("credential_available", "CREDENTIAL_UNAVAILABLE"),
        ("capability_enabled", "CAPABILITY_NOT_ENABLED"),
        ("probe_passed", "CAPABILITY_PROBE_FAILED"),
    ):
        if payload.get(key) is not True:
            return code
    return "PROVIDER_NOT_READY"


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"TENANT_PROVIDER_{key.upper()}_MISSING")
    return value


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = [
    "CapabilityResolver", "CredentialLease", "CredentialResolver",
    "FactBoundArtifactPort", "FactBoundChildRunPort",
    "FactBoundWorkspacePort",
    "PostgresTenantProviderResolver", "ProductionServiceBundle",
    "ProductionServicePorts", "ReadinessResult",
    "build_production_service_bundle",
]
