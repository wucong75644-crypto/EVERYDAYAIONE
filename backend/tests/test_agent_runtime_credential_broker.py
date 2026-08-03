from __future__ import annotations

import pickle
from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.credential_broker import (
    BackendCredential,
    CredentialAuditEvent,
    CredentialBroker,
    CredentialBrokerError,
    InMemoryCredentialAuditSink,
    InMemoryCredentialBackend,
)
from services.agent.runtime.domain import RuntimeScope, ScopeKind


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
SECRET = "test-only-secret-material"


def _scope(*, user: str, org: str | None = "org-a") -> RuntimeScope:
    return RuntimeScope(ScopeKind.USER, user, user, org)


def _backend() -> tuple[InMemoryCredentialBackend, InMemoryCredentialAuditSink]:
    backend = InMemoryCredentialBackend()
    backend.put(BackendCredential(
        tenant_id="org-a", handle="credential:org-a:erp",
        provider="erp", revision="erp-v1", purpose="erp.submit",
        expires_at=NOW + timedelta(minutes=10), _material=SECRET,
    ))
    return backend, InMemoryCredentialAuditSink()


def _broker() -> tuple[CredentialBroker, InMemoryCredentialAuditSink]:
    backend, audit = _backend()
    return CredentialBroker(backend, audit, clock=lambda: NOW), audit


@pytest.mark.asyncio
async def test_lease_is_tenant_provider_revision_and_purpose_bound() -> None:
    broker, audit = _broker()
    lease = await broker.resolve(
        scope=_scope(user="user-a"), credential_handle="credential:org-a:erp",
        provider="erp", revision="erp-v1", purpose="erp.submit",
    )

    assert lease.tenant_id == "org-a"
    assert lease.provider == "erp"
    assert lease.revision == "erp-v1"
    assert lease.purpose == "erp.submit"
    assert lease.expires_at == NOW + timedelta(minutes=5)
    assert audit.events[-1].outcome == "issued"

    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_TENANT_MISMATCH"):
        await lease.use(
            scope=_scope(user="user-b", org="org-b"), provider="erp",
            revision="erp-v1", purpose="erp.submit", consumer=lambda value: value,
        )
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_PROVIDER_MISMATCH"):
        await lease.use(
            scope=_scope(user="user-a"), provider="media", revision="erp-v1",
            purpose="erp.submit", consumer=lambda value: value,
        )
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_REVISION_MISMATCH"):
        await lease.use(
            scope=_scope(user="user-a"), provider="erp", revision="erp-v2",
            purpose="erp.submit", consumer=lambda value: value,
        )
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_PURPOSE_MISMATCH"):
        await lease.use(
            scope=_scope(user="user-a"), provider="erp", revision="erp-v1",
            purpose="erp.read", consumer=lambda value: value,
        )


@pytest.mark.asyncio
async def test_missing_handle_and_cross_tenant_lookup_fail_closed() -> None:
    broker, _ = _broker()
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_UNAVAILABLE"):
        await broker.resolve(
            scope=_scope(user="user-a"), credential_handle="missing",
            provider="erp", revision="erp-v1", purpose="erp.submit",
        )
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_UNAVAILABLE"):
        await broker.resolve(
            scope=_scope(user="user-b", org="org-b"),
            credential_handle="credential:org-a:erp", provider="erp",
            revision="erp-v1", purpose="erp.submit",
        )


@pytest.mark.asyncio
async def test_backend_not_ready_and_expired_lease_fail_closed() -> None:
    backend, audit = _backend()
    backend.operational = False
    broker = CredentialBroker(backend, audit, clock=lambda: NOW)
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_BACKEND_NOT_READY"):
        await broker.resolve(
            scope=_scope(user="user-a"), credential_handle="credential:org-a:erp",
            provider="erp", revision="erp-v1", purpose="erp.submit",
        )

    backend.operational = True
    backend.put(BackendCredential(
        tenant_id="org-a", handle="expired", provider="erp", revision="erp-v1",
        purpose="erp.submit", expires_at=NOW, _material=SECRET,
    ))
    broker = CredentialBroker(backend, audit, clock=lambda: NOW)
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_UNAVAILABLE"):
        await broker.resolve(
            scope=_scope(user="user-a"), credential_handle="expired",
            provider="erp", revision="erp-v1", purpose="erp.submit",
        )


@pytest.mark.asyncio
async def test_secret_only_reaches_controlled_consumer_and_lease_is_not_serializable() -> None:
    broker, audit = _broker()
    lease = await broker.resolve(
        scope=_scope(user="user-a"), credential_handle="credential:org-a:erp",
        provider="erp", revision="erp-v1", purpose="erp.submit",
    )
    observed: list[str] = []
    result = await lease.use(
        scope=_scope(user="user-a"), provider="erp", revision="erp-v1",
        purpose="erp.submit", consumer=lambda material: observed.append(material) or "ok",
    )
    assert result == "ok"
    assert observed == [SECRET]
    assert SECRET not in repr(lease)
    assert SECRET not in repr(audit.events)
    with pytest.raises(TypeError, match="NOT_SERIALIZABLE"):
        pickle.dumps(lease)


def test_mock_backend_never_reports_production_readiness() -> None:
    backend, audit = _backend()
    readiness = CredentialBroker(backend, audit, clock=lambda: NOW).readiness()
    assert readiness.backend_ready is True
    assert readiness.production_ready is False
    assert readiness.ready is False
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_BACKEND_NOT_READY"):
        CredentialBroker(backend, audit, clock=lambda: NOW).require_production_ready()


def test_audit_contract_contains_only_secret_free_fields() -> None:
    event_fields = set(CredentialAuditEvent.__annotations__)
    assert event_fields == {
        "tenant_id", "handle", "provider", "revision", "purpose",
        "outcome", "occurred_at",
    }
    assert "_material" in BackendCredential.__annotations__
    assert "secret" not in event_fields
