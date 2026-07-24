"""Conversation Actor 数据库 Scope 装配测试。"""

from unittest.mock import MagicMock

import pytest

from core.db_scope import AsyncScopedDatabaseClient
from core.org_scoped_db import OrgScopedDB
from services.conversation_db_scope import (
    build_actor_task_databases,
    build_actor_worker_db,
)


USER_ID = "f566f6cc-3e7a-4383-befe-42c05fbfbff8"
ORG_ID = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"
TASK_ID = "291ece5a-6db9-4f54-8348-9ac6da153483"


def test_worker_scope_has_no_tenant_identity() -> None:
    base_db = MagicMock()

    worker_db = build_actor_worker_db(base_db)

    assert isinstance(worker_db, AsyncScopedDatabaseClient)
    assert worker_db._client is base_db
    assert worker_db.scope.settings == (
        "", "", "worker", "conversation-actor-worker",
    )


def test_task_scope_builds_control_and_application_clients() -> None:
    base_db = MagicMock()
    handler_db = MagicMock()

    databases = build_actor_task_databases(
        base_db,
        {"id": TASK_ID, "user_id": USER_ID, "org_id": ORG_ID},
        handler_db=handler_db,
    )

    assert isinstance(databases.control, AsyncScopedDatabaseClient)
    assert isinstance(databases.application, OrgScopedDB)
    assert databases.application._db is databases.control
    assert databases.application.org_id == ORG_ID
    assert isinstance(databases.handler, OrgScopedDB)
    assert databases.handler._db._client is handler_db
    assert databases.handler._db.scope == databases.control.scope
    assert databases.control.scope.settings == (
        USER_ID, ORG_ID, "worker", f"actor:{TASK_ID}",
    )


@pytest.mark.parametrize(
    "task",
    [
        {"id": TASK_ID, "user_id": None, "org_id": ORG_ID},
        {"id": None, "user_id": USER_ID, "org_id": ORG_ID},
        {"id": TASK_ID, "user_id": "invalid", "org_id": ORG_ID},
        {"id": TASK_ID, "user_id": USER_ID, "org_id": "invalid"},
    ],
)
def test_task_scope_rejects_missing_or_invalid_identity(task: dict) -> None:
    with pytest.raises((RuntimeError, ValueError)):
        build_actor_task_databases(MagicMock(), task)
