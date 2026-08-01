"""Real, non-producing capability primitives for AR-17.2 read adapters."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.scope import ScopeKind
from services.agent.runtime.executors.contracts import ActionSnapshot
from services.agent.runtime.executors.capabilities import CapabilityBinding


@dataclass(frozen=True, kw_only=True)
class RuntimeReadResources:
    database: Any
    user_id: str
    org_id: str | None
    conversation_id: str | None = None
    base_revision: int | None = None
    workspace_root: Path | None = None
    artifact_store: Any | None = None
    capability_ttl_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("RUNTIME_READ_USER_REQUIRED")
        if self.base_revision is not None and self.base_revision < 0:
            raise ValueError("RUNTIME_READ_REVISION_INVALID")
        if self.capability_ttl_seconds < 1:
            raise ValueError("RUNTIME_READ_CAPABILITY_TTL_INVALID")
        if self.workspace_root is not None:
            object.__setattr__(
                self, "workspace_root", Path(self.workspace_root).resolve()
            )


class RealReadCapability:
    """Base for capabilities that issue an exact Action/Attempt binding."""

    def __init__(self, resources: RuntimeReadResources) -> None:
        self.resources = resources

    def bind(self, snapshot: ActionSnapshot) -> "BoundRealReadCapability":
        self._validate_snapshot(snapshot)
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self.resources.capability_ttl_seconds,
        )
        binding = CapabilityBinding(
            action_id=snapshot.action_id,
            attempt_id=snapshot.attempt_id,
            expires_at=expires_at,
        )
        return BoundRealReadCapability(self, binding)

    async def _read_bound(
        self, snapshot: ActionSnapshot, request: Mapping[str, object],
    ) -> Mapping[str, object]:
        raise NotImplementedError

    def _validate_snapshot(self, snapshot: ActionSnapshot) -> None:
        scope = snapshot.scope
        if scope.kind is ScopeKind.USER:
            if scope.user_id != self.resources.user_id:
                raise PermissionError("READ_USER_SCOPE_MISMATCH")
            if self.resources.org_id is not None:
                raise PermissionError("READ_PERSONAL_ORG_SCOPE_MISMATCH")
        elif scope.kind is ScopeKind.CHANNEL:
            if scope.org_id != self.resources.org_id or not scope.org_id:
                raise PermissionError("READ_ORG_SCOPE_MISMATCH")
        else:
            raise PermissionError("READ_SCOPE_KIND_NOT_ALLOWED")
        db_scope = database_scope_from_client(self.resources.database)
        if db_scope is None or db_scope.access_kind is not DatabaseAccessKind.RUNTIME:
            raise PermissionError("RUNTIME_DATABASE_SCOPE_REQUIRED")
        if db_scope.actor_user_id != self.resources.user_id:
            raise PermissionError("READ_DATABASE_ACTOR_MISMATCH")
        if db_scope.org_id != self.resources.org_id:
            raise PermissionError("READ_DATABASE_ORG_MISMATCH")


class BoundRealReadCapability:
    def __init__(self, owner: RealReadCapability, binding: CapabilityBinding) -> None:
        self._owner = owner
        self._binding = binding

    async def read(
        self, snapshot: ActionSnapshot, request: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._binding.assert_live(snapshot.action_id, snapshot.attempt_id)
        if snapshot.action_id != self._binding.action_id:
            raise PermissionError("READ_ACTION_BINDING_MISMATCH")
        if snapshot.attempt_id != self._binding.attempt_id:
            raise PermissionError("READ_ATTEMPT_BINDING_MISMATCH")
        return await self._owner._read_bound(snapshot, request)


async def execute_query(builder: Any) -> Any:
    result = builder.execute()
    return await result if inspect.isawaitable(result) else result


async def table_rows(
    database: Any, table: str, columns: str, *, limit: int = 100,
) -> list[dict[str, Any]]:
    result = await execute_query(database.table(table).select(columns).limit(limit))
    data = getattr(result, "data", None)
    return data if isinstance(data, list) else []


def bounded_limit(value: object, *, default: int = 20, maximum: int = 100) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("READ_LIMIT_INVALID")
    if value < 1 or value > maximum:
        raise ValueError("READ_LIMIT_OUT_OF_RANGE")
    return value


def required_text(request: Mapping[str, object], name: str, *, max_len: int = 200) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise ValueError(f"READ_{name.upper()}_INVALID")
    return value.strip()


def optional_text(request: Mapping[str, object], name: str, *, max_len: int = 200) -> str | None:
    value = request.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_len:
        raise ValueError(f"READ_{name.upper()}_INVALID")
    return value.strip() or None


def public_rows(rows: list[Mapping[str, object]], allowed: tuple[str, ...]) -> list[dict[str, object]]:
    return [{key: row.get(key) for key in allowed if key in row} for row in rows]
