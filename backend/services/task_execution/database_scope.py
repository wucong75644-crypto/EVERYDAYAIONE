"""Keep real Actor pool transactions scoped to the already validated task."""
from core.db_scope import AsyncScopedDatabaseClient, DatabaseScope, DatabaseAccessKind, database_scope_from_client
from core.local_db import AsyncLocalDBClient
from .contracts import TaskError


def task_database(db, state):
    expected = DatabaseScope(str(state.actor_user_id), str(state.org_id) if state.org_id else None,
        DatabaseAccessKind.RUNTIME)
    current = database_scope_from_client(db)
    if current is not None:
        if (current.actor_user_id, current.org_id, current.access_kind) != (
                expected.actor_user_id, expected.org_id, expected.access_kind):
            raise TaskError('TASK_DATABASE_SCOPE_MISMATCH')
        return db
    if isinstance(db, AsyncLocalDBClient):
        return AsyncScopedDatabaseClient(db, expected)
    # Explicit store test/transport adapters own their transaction boundary.
    # Never mutate a shared client's scope or set connection-global identity.
    return db
