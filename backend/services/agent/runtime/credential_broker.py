"""Runtime-owned opaque credential leasing.

This module deliberately stops at the credential boundary.  It does not
discover settings, read legacy token caches, or connect to a secret backend.
Provider builders receive a short-lived lease and consume it through the
controlled callback only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Awaitable, Callable, Generic, Mapping, Protocol, TypeVar

from services.agent.runtime.domain import RuntimeScope

T = TypeVar("T")


class CredentialBrokerError(RuntimeError):
    """Stable failure-closed error without backend details."""


class CredentialBackend(Protocol):
    @property
    def operational(self) -> bool: ...

    @property
    def production_ready(self) -> bool: ...

    async def resolve(
        self, *, tenant_id: str, handle: str, provider: str,
        revision: str, purpose: str,
    ) -> "BackendCredential": ...


class CredentialAuditSink(Protocol):
    async def record(self, event: "CredentialAuditEvent") -> None: ...


@dataclass(frozen=True, kw_only=True)
class BackendCredential:
    """Internal backend result; never returned to Worker or provider code."""

    tenant_id: str
    handle: str
    provider: str
    revision: str
    purpose: str
    expires_at: datetime
    _material: object

    def __repr__(self) -> str:
        return (
            "BackendCredential(tenant_id=%r, handle=%r, provider=%r, "
            "revision=%r, purpose=%r, expires_at=%r)"
            % (self.tenant_id, self.handle, self.provider, self.revision,
               self.purpose, self.expires_at)
        )


@dataclass(frozen=True, kw_only=True)
class CredentialAuditEvent:
    tenant_id: str
    handle: str
    provider: str
    revision: str
    purpose: str
    outcome: str
    occurred_at: datetime


@dataclass(frozen=True, kw_only=True)
class CredentialReadiness:
    service_wiring_ready: bool
    backend_ready: bool
    production_ready: bool
    error_code: str | None = None

    @property
    def ready(self) -> bool:
        return self.service_wiring_ready and self.backend_ready and self.production_ready


class CredentialLease(Generic[T]):
    """Non-exportable lease whose material is available only to a builder."""

    __slots__ = (
        "_tenant_id", "_handle", "_provider", "_revision", "_purpose",
        "_expires_at", "_material", "_clock", "_used",
    )

    def __init__(
        self, *, tenant_id: str, handle: str, provider: str, revision: str,
        purpose: str, expires_at: datetime, material: object,
        clock: Callable[[], datetime],
    ) -> None:
        self._tenant_id = tenant_id
        self._handle = handle
        self._provider = provider
        self._revision = revision
        self._purpose = purpose
        self._expires_at = expires_at
        self._material = material
        self._clock = clock
        self._used = False

    @property
    def handle(self) -> str:
        return self._handle

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def purpose(self) -> str:
        return self._purpose

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    def __repr__(self) -> str:
        return (
            "CredentialLease(handle=%r, tenant_id=%r, provider=%r, "
            "revision=%r, purpose=%r, expires_at=%r)"
            % (self._handle, self._tenant_id, self._provider,
               self._revision, self._purpose, self._expires_at)
        )

    def __getstate__(self) -> Mapping[str, object]:
        raise TypeError("CREDENTIAL_LEASE_NOT_SERIALIZABLE")

    async def use(
        self, *, scope: RuntimeScope, provider: str, revision: str,
        purpose: str, consumer: Callable[[T], T | Awaitable[T]],
    ) -> T:
        self._assert_usable(scope=scope, provider=provider, revision=revision, purpose=purpose)
        self._used = True
        try:
            result = consumer(self._material)  # trusted provider-builder boundary
            return await result if isawaitable(result) else result
        except Exception:
            raise CredentialBrokerError("CREDENTIAL_CONSUMER_FAILED") from None

    def _assert_usable(
        self, *, scope: RuntimeScope, provider: str, revision: str, purpose: str,
    ) -> None:
        if _scope_tenant_id(scope) != self._tenant_id:
            raise CredentialBrokerError("CREDENTIAL_TENANT_MISMATCH")
        if provider != self._provider:
            raise CredentialBrokerError("CREDENTIAL_PROVIDER_MISMATCH")
        if revision != self._revision:
            raise CredentialBrokerError("CREDENTIAL_REVISION_MISMATCH")
        if purpose != self._purpose:
            raise CredentialBrokerError("CREDENTIAL_PURPOSE_MISMATCH")
        if self._clock() >= self._expires_at:
            raise CredentialBrokerError("CREDENTIAL_LEASE_EXPIRED")


class CredentialBroker:
    """Resolve an opaque handle without exposing backend material."""

    def __init__(
        self, backend: CredentialBackend, audit: CredentialAuditSink,
        *, clock: Callable[[], datetime] | None = None,
        lease_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("CREDENTIAL_LEASE_TTL_INVALID")
        self._backend = backend
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease_ttl = lease_ttl

    def readiness(self) -> CredentialReadiness:
        if not getattr(self._backend, "operational", False):
            return CredentialReadiness(
                service_wiring_ready=True, backend_ready=False,
                production_ready=False, error_code="CREDENTIAL_BACKEND_NOT_READY",
            )
        if not getattr(self._backend, "production_ready", False):
            return CredentialReadiness(
                service_wiring_ready=True, backend_ready=True,
                production_ready=False, error_code="CREDENTIAL_BACKEND_NOT_READY",
            )
        return CredentialReadiness(
            service_wiring_ready=True, backend_ready=True, production_ready=True,
        )

    def require_production_ready(self) -> None:
        readiness = self.readiness()
        if not readiness.ready:
            raise CredentialBrokerError(readiness.error_code or "CREDENTIAL_BACKEND_NOT_READY")

    async def resolve(
        self, *, scope: RuntimeScope, credential_handle: str,
        provider: str, revision: str, purpose: str,
    ) -> CredentialLease:
        tenant_id = _scope_tenant_id(scope)
        handle = _required(credential_handle, "CREDENTIAL_HANDLE_REQUIRED")
        provider = _required(provider, "CREDENTIAL_PROVIDER_REQUIRED")
        revision = _required(revision, "CREDENTIAL_REVISION_REQUIRED")
        purpose = _required(purpose, "CREDENTIAL_PURPOSE_REQUIRED")
        if not getattr(self._backend, "operational", False):
            await self._record(tenant_id, handle, provider, revision, purpose, "backend_not_ready")
            raise CredentialBrokerError("CREDENTIAL_BACKEND_NOT_READY")
        try:
            record = await self._backend.resolve(
                tenant_id=tenant_id, handle=handle, provider=provider,
                revision=revision, purpose=purpose,
            )
        except CredentialBrokerError:
            await self._record(tenant_id, handle, provider, revision, purpose, "unavailable")
            raise CredentialBrokerError("CREDENTIAL_UNAVAILABLE") from None
        except Exception:
            await self._record(tenant_id, handle, provider, revision, purpose, "backend_error")
            raise CredentialBrokerError("CREDENTIAL_BACKEND_NOT_READY") from None
        try:
            _assert_record(record, tenant_id, handle, provider, revision, purpose, self._clock())
        except CredentialBrokerError:
            await self._record(tenant_id, handle, provider, revision, purpose, "binding_rejected")
            raise
        await self._record(tenant_id, handle, provider, revision, purpose, "issued")
        return CredentialLease(
            tenant_id=tenant_id, handle=handle, provider=provider,
            revision=revision, purpose=purpose,
            expires_at=min(record.expires_at, self._clock() + self._lease_ttl),
            material=record._material, clock=self._clock,
        )

    async def _record(self, tenant_id: str, handle: str, provider: str,
                      revision: str, purpose: str, outcome: str) -> None:
        try:
            await self._audit.record(CredentialAuditEvent(
                tenant_id=tenant_id, handle=handle, provider=provider,
                revision=revision, purpose=purpose, outcome=outcome,
                occurred_at=self._clock(),
            ))
        except Exception:
            raise CredentialBrokerError("CREDENTIAL_BACKEND_NOT_READY") from None


def _scope_tenant_id(scope: RuntimeScope) -> str:
    tenant_id = scope.org_id or scope.user_id
    return _required(tenant_id, "CREDENTIAL_TENANT_REQUIRED")


def _required(value: object, error: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CredentialBrokerError(error)
    return value.strip()


def _assert_record(
    record: BackendCredential, tenant_id: str, handle: str, provider: str,
    revision: str, purpose: str, now: datetime,
) -> None:
    if not isinstance(record, BackendCredential):
        raise CredentialBrokerError("CREDENTIAL_BACKEND_INVALID")
    if (record.tenant_id, record.handle, record.provider,
            record.revision, record.purpose) != (tenant_id, handle, provider, revision, purpose):
        raise CredentialBrokerError("CREDENTIAL_BINDING_MISMATCH")
    if record.expires_at <= now:
        raise CredentialBrokerError("CREDENTIAL_UNAVAILABLE")


class InMemoryCredentialBackend:
    """Explicit non-production backend for isolated tests only."""

    operational = True
    production_ready = False

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], BackendCredential] = {}

    def put(self, record: BackendCredential) -> None:
        self._records[(record.tenant_id, record.handle)] = record

    async def resolve(
        self, *, tenant_id: str, handle: str, provider: str,
        revision: str, purpose: str,
    ) -> BackendCredential:
        record = self._records.get((tenant_id, handle))
        if record is None:
            raise CredentialBrokerError("CREDENTIAL_UNAVAILABLE")
        return record


class InMemoryCredentialAuditSink:
    """Secret-free audit sink for tests; production persistence is separate."""

    def __init__(self) -> None:
        self.events: list[CredentialAuditEvent] = []

    async def record(self, event: CredentialAuditEvent) -> None:
        self.events.append(event)


__all__ = [
    "BackendCredential", "CredentialAuditEvent", "CredentialAuditSink",
    "CredentialBackend", "CredentialBroker", "CredentialBrokerError",
    "CredentialLease", "CredentialReadiness", "InMemoryCredentialAuditSink",
    "InMemoryCredentialBackend",
]
