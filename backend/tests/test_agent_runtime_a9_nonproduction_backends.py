from datetime import datetime, timedelta, timezone

import pytest

from services.agent.runtime.credential_broker import (
    CredentialBroker,
    CredentialBrokerError,
    InMemoryCredentialAuditSink,
)
from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.nonproduction_backends import (
    LocalNonProductionCredentialBackend,
    LocalNonProductionObjectStore,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _scope(org_id: str = "org-a") -> RuntimeScope:
    return RuntimeScope(ScopeKind.USER, "user-a", "user-a", org_id)


def _broker() -> tuple[CredentialBroker, InMemoryCredentialAuditSink]:
    backend = LocalNonProductionCredentialBackend()
    backend.put_test_material(
        tenant_id="org-a", handle="test:org-a:erp", provider="erp",
        revision="erp-test-v1", purpose="erp.submit", material="test-only",
        expires_at=NOW + timedelta(minutes=10),
    )
    audit = InMemoryCredentialAuditSink()
    return CredentialBroker(backend, audit, clock=lambda: NOW), audit


@pytest.mark.asyncio
async def test_local_credential_backend_is_tenant_bound_and_never_production_ready():
    broker, audit = _broker()
    lease = await broker.resolve(
        scope=_scope(), credential_handle="test:org-a:erp", provider="erp",
        revision="erp-test-v1", purpose="erp.submit",
    )
    assert broker.readiness().ready is False
    assert broker.readiness().production_ready is False
    assert lease.handle == "test:org-a:erp"
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_TENANT_MISMATCH"):
        await lease.use(
            scope=_scope("org-b"), provider="erp", revision="erp-test-v1",
            purpose="erp.submit", consumer=lambda value: value,
        )
    assert "test-only" not in repr(lease)
    assert "test-only" not in repr(audit.events)


@pytest.mark.asyncio
async def test_local_credential_binding_mismatch_and_expiry_fail_closed():
    broker, _ = _broker()
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_UNAVAILABLE"):
        await broker.resolve(
            scope=_scope(), credential_handle="test:org-a:erp", provider="erp",
            revision="wrong", purpose="erp.submit",
        )
    expired = LocalNonProductionCredentialBackend()
    expired.put_test_material(
        tenant_id="org-a", handle="expired", provider="erp", revision="v1",
        purpose="submit", material="test-only", expires_at=NOW,
    )
    with pytest.raises(CredentialBrokerError, match="CREDENTIAL_UNAVAILABLE"):
        await CredentialBroker(expired, InMemoryCredentialAuditSink(), clock=lambda: NOW).resolve(
            scope=_scope(), credential_handle="expired", provider="erp",
            revision="v1", purpose="submit",
        )


@pytest.mark.asyncio
async def test_local_object_store_verifies_hash_and_blocks_escape(tmp_path):
    store = LocalNonProductionObjectStore(tmp_path, tenant_id="org-a")
    assert store.production_ready is False
    content = b"non-production artifact"
    import hashlib
    digest = hashlib.sha256(content).hexdigest()
    result = await store.put_verified("artifacts/a", content, content_hash=digest)
    assert result == {"tenant_id": "org-a", "key": "artifacts/a", "content_hash": digest, "verified": True}
    assert await store.get("artifacts/a") == content
    with pytest.raises(ValueError, match="CONTENT_HASH_MISMATCH"):
        await store.put_verified("artifacts/b", content, content_hash="0" * 64)
    with pytest.raises(PermissionError, match="KEY_INVALID"):
        await store.get("../org-b/secret")
