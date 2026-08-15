"""Conversation Actor 的 Worker 与任务级数据库身份装配。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
)
from core.org_scoped_db import OrgScopedDB


@dataclass(frozen=True)
class ActorTaskDatabases:
    """同一任务的控制面 DB 与应用层租户过滤 DB。"""

    control: Any
    application: Any
    handler: Any


def build_actor_worker_db(db: Any) -> AsyncScopedDatabaseClient:
    """构造只用于跨租户扫描、claim 和任务身份读取的 Worker DB。"""
    return AsyncScopedDatabaseClient(
        db,
        DatabaseScope(
            actor_user_id=None,
            org_id=None,
            access_kind=DatabaseAccessKind.WORKER,
            request_id="conversation-actor-worker",
        ),
    )


def build_actor_task_databases(
    db: Any,
    task: Mapping[str, Any],
    *,
    handler_db: Any | None = None,
) -> ActorTaskDatabases:
    """为同一任务分别构造 Worker 控制面与 Runtime 业务执行面。"""
    task_id = task.get("id")
    user_id = task.get("user_id")
    if not task_id or not user_id:
        raise RuntimeError("ACTOR_TASK_DATABASE_SCOPE_MISSING")
    org_id = task.get("org_id")
    control_scope = DatabaseScope(
        actor_user_id=str(user_id),
        org_id=str(org_id) if org_id else None,
        access_kind=DatabaseAccessKind.WORKER,
        request_id=f"actor:{task_id}"[:128],
    )
    execution_scope = DatabaseScope(
        actor_user_id=str(user_id),
        org_id=str(org_id) if org_id else None,
        access_kind=DatabaseAccessKind.RUNTIME,
        request_id=f"agent:{task_id}"[:128],
    )
    control = AsyncScopedDatabaseClient(db, control_scope)
    handler_control = ScopedDatabaseClient(handler_db or db, execution_scope)
    return ActorTaskDatabases(
        control=control,
        application=OrgScopedDB(control, control_scope.org_id),
        handler=OrgScopedDB(handler_control, execution_scope.org_id),
    )
