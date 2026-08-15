"""Memory raw SQL 的 DatabaseScope 传播测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.db_scope import (
    AsyncScopedConnectionPool,
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
    database_scope_from_client,
)
from core.org_scoped_db import OrgScopedDB
from services.memory.memory_service_v2 import (
    MemoryServiceV2,
    _PsycopgAdapter,
    _get_memory_db,
    get_scheduler,
)


USER_A = "f566f6cc-3e7a-4383-befe-42c05fbfbff8"
USER_B = "72fbe19e-c790-4e75-9087-cbf78bb243e2"
ORG_A = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"
ORG_B = "5fb02c39-7558-467b-a749-a416f354e107"


def _client(user_id: str, org_id: str) -> OrgScopedDB:
    scope = DatabaseScope(
        user_id,
        org_id,
        DatabaseAccessKind.WORKER,
        f"memory:{user_id}",
    )
    return OrgScopedDB(
        ScopedDatabaseClient(object(), scope),
        org_id,
    )


def test_scope_resolver_reads_direct_and_org_wrapped_clients() -> None:
    client = _client(USER_A, ORG_A)

    assert database_scope_from_client(client) == client._db.scope
    assert database_scope_from_client(client._db) == client._db.scope
    assert database_scope_from_client(MagicMock()) is None


@pytest.mark.asyncio
async def test_memory_service_wraps_global_pool_with_calling_scope() -> None:
    raw_pool = MagicMock()
    client = _client(USER_A, ORG_A)
    service = MemoryServiceV2(db_pool=client)

    with patch(
        "services.memory.memory_service_v2._get_memory_db",
        new=AsyncMock(return_value=raw_pool),
    ):
        adapter = await service._ensure_db()

    assert isinstance(adapter, _PsycopgAdapter)
    assert isinstance(adapter._pool, AsyncScopedConnectionPool)
    assert adapter._pool._pool is raw_pool
    assert adapter._pool.scope == client._db.scope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access_kind", "expected_pool"),
    [
        (DatabaseAccessKind.RUNTIME, "runtime-pool"),
        (DatabaseAccessKind.WORKER, "worker-pool"),
    ],
)
async def test_memory_pool_matches_calling_database_role(
    access_kind: DatabaseAccessKind,
    expected_pool: str,
) -> None:
    with (
        patch(
            "services.knowledge_config._get_pg_pool",
            new=AsyncMock(return_value="runtime-pool"),
        ),
        patch(
            "services.knowledge_config._get_worker_pg_pool",
            new=AsyncMock(return_value="worker-pool"),
        ),
    ):
        assert await _get_memory_db(access_kind) == expected_pool


@pytest.mark.asyncio
async def test_scheduler_is_not_shared_between_tenants() -> None:
    raw_pool = MagicMock()
    client_a = _client(USER_A, ORG_A)
    client_b = _client(USER_B, ORG_B)

    with patch(
        "services.memory.memory_service_v2._get_memory_db",
        new=AsyncMock(return_value=raw_pool),
    ):
        scheduler_a = await get_scheduler(client_a)
        scheduler_b = await get_scheduler(client_b)

    assert scheduler_a is not scheduler_b
    assert scheduler_a._db._pool.scope == client_a._db.scope
    assert scheduler_b._db._pool.scope == client_b._db.scope


@pytest.mark.asyncio
async def test_memory_service_rejects_missing_scope() -> None:
    service = MemoryServiceV2(db_pool=object())

    with pytest.raises(RuntimeError, match="SCOPE_REQUIRED"):
        await service._ensure_db()


@pytest.mark.asyncio
async def test_adapter_write_does_not_explicitly_commit() -> None:
    pool = MagicMock()
    connection = MagicMock()
    connection.commit = AsyncMock()
    connection_context = AsyncMock()
    connection_context.__aenter__.return_value = connection
    pool.connection.return_value = connection_context
    cursor = AsyncMock()
    cursor_context = AsyncMock()
    cursor_context.__aenter__.return_value = cursor
    connection.cursor.return_value = cursor_context

    await _PsycopgAdapter(pool).execute("SELECT $1", "value")

    cursor.execute.assert_awaited_once_with("SELECT %s", ("value",))
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_fetch_maps_rows_and_reorders_placeholders() -> None:
    pool = MagicMock()
    connection = MagicMock()
    connection_context = AsyncMock()
    connection_context.__aenter__.return_value = connection
    pool.connection.return_value = connection_context
    cursor = AsyncMock()
    cursor.description = [("first",), ("second",)]
    cursor.fetchall.return_value = [("A", "B")]
    cursor_context = AsyncMock()
    cursor_context.__aenter__.return_value = cursor
    connection.cursor.return_value = cursor_context

    rows = await _PsycopgAdapter(pool).fetch(
        "SELECT $2, $1",
        "first-value",
        "second-value",
    )

    assert rows == [{"first": "A", "second": "B"}]
    cursor.execute.assert_awaited_once_with(
        "SELECT %s, %s",
        ("second-value", "first-value"),
    )


@pytest.mark.asyncio
async def test_adapter_fetchrow_returns_first_or_none() -> None:
    adapter = _PsycopgAdapter(MagicMock())
    adapter.fetch = AsyncMock(side_effect=[[{"id": 1}, {"id": 2}], []])

    assert await adapter.fetchrow("SELECT 1") == {"id": 1}
    assert await adapter.fetchrow("SELECT 1") is None
