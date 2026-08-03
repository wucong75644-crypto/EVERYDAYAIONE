"""Self-contained non-production Runtime backends.

These adapters are deliberately local and explicit. They are useful for
disposable integration profiles, but neither can ever report production
readiness or discover global settings.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from services.agent.runtime.credential_broker import (
    BackendCredential,
    CredentialBrokerError,
)


class LocalNonProductionCredentialBackend:
    """Explicit test-material backend; no settings or external secret reads."""

    operational = True
    production_ready = False
    non_production_ready = True

    def __init__(self, records: Iterable[BackendCredential] = ()) -> None:
        self._records: dict[tuple[str, str], BackendCredential] = {}
        for record in records:
            self.put_test_record(record)

    def put_test_record(self, record: BackendCredential) -> None:
        if not isinstance(record, BackendCredential):
            raise TypeError("LOCAL_CREDENTIAL_RECORD_REQUIRED")
        self._records[(record.tenant_id, record.handle)] = record

    def put_test_material(
        self, *, tenant_id: str, handle: str, provider: str, revision: str,
        purpose: str, material: object, expires_at: datetime | None = None,
    ) -> None:
        self.put_test_record(BackendCredential(
            tenant_id=tenant_id, handle=handle, provider=provider,
            revision=revision, purpose=purpose,
            expires_at=expires_at or datetime.now(timezone.utc) + timedelta(minutes=10),
            _material=material,
        ))

    async def resolve(
        self, *, tenant_id: str, handle: str, provider: str,
        revision: str, purpose: str,
    ) -> BackendCredential:
        record = self._records.get((tenant_id, handle))
        if record is None or (
            record.provider, record.revision, record.purpose
        ) != (provider, revision, purpose):
            raise CredentialBrokerError("CREDENTIAL_UNAVAILABLE")
        return record


class LocalNonProductionObjectStore:
    """Tenant-rooted content-addressed store for disposable profiles."""

    tenant_scoped = True
    production_ready = False
    non_production_ready = True

    def __init__(self, root: Path, *, tenant_id: str) -> None:
        if not tenant_id or tenant_id.strip() != tenant_id or "/" in tenant_id:
            raise ValueError("LOCAL_OBJECT_STORE_TENANT_INVALID")
        self._root = root.resolve() / tenant_id
        self._root.mkdir(parents=True, exist_ok=True)
        self.tenant_id = tenant_id

    async def put_verified(
        self, key: str, content: bytes, *, content_hash: str,
    ) -> dict[str, object]:
        target = self._safe_path(key)
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != content_hash:
            raise ValueError("LOCAL_OBJECT_CONTENT_HASH_MISMATCH")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != content_hash:
                raise RuntimeError("LOCAL_OBJECT_READBACK_FAILED")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "tenant_id": self.tenant_id, "key": key,
            "content_hash": content_hash, "verified": True,
        }

    async def get(self, key: str) -> bytes:
        target = self._safe_path(key)
        if not target.is_file():
            raise FileNotFoundError("LOCAL_OBJECT_NOT_FOUND")
        return target.read_bytes()

    def _safe_path(self, key: str) -> Path:
        if not isinstance(key, str) or not key.strip() or "\\" in key:
            raise PermissionError("LOCAL_OBJECT_KEY_INVALID")
        relative = Path(key)
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("LOCAL_OBJECT_KEY_INVALID")
        target = (self._root / relative).resolve()
        target.relative_to(self._root)
        return target


__all__ = ["LocalNonProductionCredentialBackend", "LocalNonProductionObjectStore"]
