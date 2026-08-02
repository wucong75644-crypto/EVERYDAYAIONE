"""Isolated Workspace/OSS/Scheduler side-effect contracts."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


class ObjectStore(Protocol):
    async def put_verified(self, key: str, content: bytes, *, content_hash: str) -> Mapping[str, object]: ...
    async def get(self, key: str) -> bytes: ...


class SchedulerStore(Protocol):
    async def cas(self, task_id: str, expected_version: int, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True, kw_only=True)
class WorkspaceResourceService:
    root: Path
    staging: Path
    objects: ObjectStore

    async def delete(self, resource_id: str, relative_path: str, oss_key: str) -> Mapping[str, object]:
        source = self._safe(relative_path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError("WORKSPACE_RESOURCE_NOT_FOUND")
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        backup = await self.objects.put_verified(oss_key, content, content_hash=digest)
        if backup.get("content_hash") != digest or backup.get("verified") is not True:
            raise RuntimeError("OSS_RETENTION_NOT_VERIFIED")
        tombstone = self.staging / "deleted" / resource_id
        tombstone.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, tombstone)
        return {"resource_id": resource_id, "content_hash": digest, "oss_key": oss_key, "state": "completed"}

    async def restore(self, resource_id: str, relative_path: str, oss_key: str) -> Mapping[str, object]:
        destination = self._safe(relative_path)
        tombstone = self.staging / "deleted" / resource_id
        content = await self.objects.get(oss_key) if not tombstone.exists() else tombstone.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(f".{destination.name}.{resource_id}.restore")
        temp.write_bytes(content)
        os.replace(temp, destination)
        if tombstone.exists():
            tombstone.unlink()
        return {"resource_id": resource_id, "content_hash": digest, "state": "completed"}

    def _safe(self, relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute() or ".." in raw.parts or "\\" in relative_path:
            raise PermissionError("WORKSPACE_RESOURCE_PATH_INVALID")
        target = (self.root / raw).resolve()
        target.relative_to(self.root.resolve())
        if any(part in {".env", ".git", ".ssh", "staging"} for part in raw.parts):
            raise PermissionError("WORKSPACE_RESOURCE_PATH_BLOCKED")
        return target


@dataclass(frozen=True, kw_only=True)
class ScheduledTaskService:
    store: SchedulerStore

    async def mutate(self, task_id: str, expected_version: int, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        if operation not in {"create", "update", "delete", "pause", "resume", "list"}:
            raise ValueError("SCHEDULED_OPERATION_INVALID")
        return await self.store.cas(task_id, expected_version, operation, payload)


__all__ = ["ObjectStore", "ScheduledTaskService", "SchedulerStore", "WorkspaceResourceService"]
