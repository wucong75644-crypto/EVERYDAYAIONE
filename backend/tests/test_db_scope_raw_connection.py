"""异步 raw SQL DatabaseScope 连接测试。"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.db_scope import (
    SET_DATABASE_SCOPE_SQL,
    AsyncScopedConnectionPool,
    DatabaseAccessKind,
    DatabaseScope,
    ScopedAsyncConnection,
)


def _scope(org_id: str | None = None) -> DatabaseScope:
    return DatabaseScope(
        actor_user_id=str(uuid4()),
        org_id=org_id,
        access_kind=DatabaseAccessKind.WORKER,
        request_id="raw-sql-1",
    )


def _pool():
    pool = MagicMock()
    connection = MagicMock()
    connection_context = AsyncMock()
    connection_context.__aenter__.return_value = connection
    pool.connection.return_value = connection_context
    transaction = AsyncMock()
    connection.transaction.return_value = transaction
    cursor = AsyncMock()
    cursor_context = AsyncMock()
    cursor_context.__aenter__.return_value = cursor
    connection.cursor.return_value = cursor_context
    return pool, connection, cursor, transaction


@pytest.mark.asyncio
async def test_raw_connection_sets_scope_inside_transaction() -> None:
    pool, connection, cursor, transaction = _pool()
    scope = _scope(str(uuid4()))

    async with AsyncScopedConnectionPool(pool, scope).connection() as scoped:
        assert isinstance(scoped, ScopedAsyncConnection)
        assert scoped._connection is connection

    cursor.execute.assert_awaited_once_with(
        SET_DATABASE_SCOPE_SQL,
        scope.settings,
    )
    transaction.__aenter__.assert_awaited_once()
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_raw_connection_exception_rolls_back_scope_transaction() -> None:
    pool, _, _, transaction = _pool()

    with pytest.raises(ValueError, match="query failed"):
        async with AsyncScopedConnectionPool(pool, _scope()).connection():
            raise ValueError("query failed")

    exit_args = transaction.__aexit__.await_args.args
    assert exit_args[0] is ValueError


@pytest.mark.asyncio
async def test_scoped_connection_rejects_transaction_escape() -> None:
    connection = ScopedAsyncConnection(MagicMock())

    with pytest.raises(RuntimeError, match="COMMIT"):
        await connection.commit()
    with pytest.raises(RuntimeError, match="ROLLBACK"):
        await connection.rollback()
    with pytest.raises(RuntimeError, match="AUTOCOMMIT"):
        await connection.set_autocommit(True)


def test_scoped_pool_does_not_expose_raw_connection_checkout() -> None:
    raw_pool = MagicMock()
    raw_pool.getconn = MagicMock()

    scoped_pool = AsyncScopedConnectionPool(raw_pool, _scope())

    with pytest.raises(AttributeError):
        getattr(scoped_pool, "getconn")


@pytest.mark.asyncio
async def test_knowledge_connection_requires_trusted_scope() -> None:
    from services.knowledge_config import get_pg_connection

    with pytest.raises(RuntimeError, match="DATABASE_SCOPE_REQUIRED"):
        await get_pg_connection()


@pytest.mark.asyncio
async def test_knowledge_connection_returns_scoped_pool_view() -> None:
    from services.knowledge_config import get_pg_connection

    raw_pool = MagicMock()
    scope = _scope(str(uuid4()))
    with patch(
        "services.knowledge_config._get_worker_pg_pool",
        new=AsyncMock(return_value=raw_pool),
    ):
        connection_context = await get_pg_connection(scope)

    assert connection_context is not raw_pool.connection.return_value


@pytest.mark.asyncio
async def test_runtime_knowledge_scope_uses_runtime_pool() -> None:
    from services.knowledge_config import get_pg_connection

    raw_pool = MagicMock()
    scope = DatabaseScope(
        actor_user_id=str(uuid4()),
        org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME,
        request_id="runtime-knowledge",
    )
    with (
        patch(
            "services.knowledge_config._get_pg_pool",
            new=AsyncMock(return_value=raw_pool),
        ) as runtime_pool,
        patch(
            "services.knowledge_config._get_worker_pg_pool",
            new=AsyncMock(),
        ) as worker_pool,
    ):
        await get_pg_connection(scope)

    runtime_pool.assert_awaited_once()
    worker_pool.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_pool_shutdown_closes_both_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import knowledge_config

    runtime_pool = MagicMock()
    runtime_pool.close = AsyncMock()
    worker_pool = MagicMock()
    worker_pool.close = AsyncMock()
    monkeypatch.setattr(knowledge_config, "_pg_pool", runtime_pool)
    monkeypatch.setattr(knowledge_config, "_worker_pg_pool", worker_pool)
    monkeypatch.setattr(knowledge_config, "_kb_available", True)
    monkeypatch.setattr(knowledge_config, "_worker_kb_available", True)

    await knowledge_config.close_pg_pools()

    runtime_pool.close.assert_awaited_once()
    worker_pool.close.assert_awaited_once()
    assert knowledge_config._pg_pool is None
    assert knowledge_config._worker_pg_pool is None


@pytest.mark.asyncio
async def test_two_pool_views_keep_independent_scope_values() -> None:
    pool_a, _, cursor_a, _ = _pool()
    pool_b, _, cursor_b, _ = _pool()
    scope_a = _scope(str(uuid4()))
    scope_b = _scope(str(uuid4()))

    async with AsyncScopedConnectionPool(pool_a, scope_a).connection():
        pass
    async with AsyncScopedConnectionPool(pool_b, scope_b).connection():
        pass

    assert cursor_a.execute.await_args.args[1] == scope_a.settings
    assert cursor_b.execute.await_args.args[1] == scope_b.settings
    assert scope_a.settings != scope_b.settings
