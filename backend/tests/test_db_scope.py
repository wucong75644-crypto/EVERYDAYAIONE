"""DatabaseScope 的事务注入与连接池隔离测试。"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from core.db_scope import (
    AsyncScopedDatabaseClient,
    AsyncScopedQueryBuilder,
    AsyncScopedRpcCaller,
    SET_DATABASE_SCOPE_SQL,
    DatabaseAccessKind,
    DatabaseScope,
    ScopedDatabaseClient,
    ScopedQueryBuilder,
    ScopedRpcCaller,
)
from core.local_db import (
    AsyncLocalDBClient,
    AsyncQueryBuilder,
    AsyncRpcCaller,
    LocalDBClient,
    QueryBuilder,
    RpcCaller,
)


def _scope() -> DatabaseScope:
    return DatabaseScope(
        actor_user_id=str(uuid4()),
        org_id=str(uuid4()),
        access_kind=DatabaseAccessKind.RUNTIME,
        request_id="request-1",
    )


def _sync_db():
    pool = MagicMock()
    connection = MagicMock()
    cursor = MagicMock()
    connection_ctx = MagicMock()
    connection_ctx.__enter__.return_value = connection
    pool.connection.return_value = connection_ctx
    cursor_ctx = MagicMock()
    cursor_ctx.__enter__.return_value = cursor
    connection.cursor.return_value = cursor_ctx
    transaction_ctx = MagicMock()
    connection.transaction.return_value = transaction_ctx
    return pool, connection, cursor, transaction_ctx


def _async_db():
    pool = MagicMock()
    connection = MagicMock()
    connection.set_autocommit = AsyncMock()
    cursor = AsyncMock()
    connection_ctx = AsyncMock()
    connection_ctx.__aenter__.return_value = connection
    pool.connection.return_value = connection_ctx
    cursor_ctx = AsyncMock()
    cursor_ctx.__aenter__.return_value = cursor
    connection.cursor.return_value = cursor_ctx
    transaction_ctx = AsyncMock()
    connection.transaction.return_value = transaction_ctx
    return pool, connection, cursor, transaction_ctx


def test_scope_normalizes_uuids_and_builds_fixed_settings() -> None:
    actor_id, org_id = uuid4(), uuid4()
    scope = DatabaseScope(
        actor_user_id=str(actor_id).upper(),
        org_id=str(org_id).upper(),
        access_kind=DatabaseAccessKind.WORKER,
    )

    assert scope.settings == (str(actor_id), str(org_id), "worker", "")


@pytest.mark.parametrize("field", ["actor_user_id", "org_id"])
def test_scope_rejects_invalid_uuid(field: str) -> None:
    values = {
        "actor_user_id": str(uuid4()),
        "org_id": str(uuid4()),
        "access_kind": DatabaseAccessKind.RUNTIME,
    }
    values[field] = "invalid"

    with pytest.raises(ValueError):
        DatabaseScope(**values)


def test_scope_rejects_unbounded_request_id() -> None:
    with pytest.raises(ValueError, match="request_id"):
        DatabaseScope(None, None, DatabaseAccessKind.WORKER, "x" * 129)


def test_sync_query_sets_scope_before_business_sql_in_transaction() -> None:
    pool, connection, cursor, transaction = _sync_db()
    scope = _scope()
    cursor.description = [("id",)]
    cursor.fetchall.return_value = [{"id": 1}]

    result = ScopedQueryBuilder(
        QueryBuilder(pool, "items"), scope,
    ).select("*").execute()

    assert result.data == [{"id": 1}]
    assert cursor.execute.call_args_list[0].args == (
        SET_DATABASE_SCOPE_SQL,
        scope.settings,
    )
    assert 'SELECT * FROM "items"' in cursor.execute.call_args_list[1].args[0]
    connection.transaction.assert_called_once_with()
    transaction.__enter__.assert_called_once()
    transaction.__exit__.assert_called_once()


def test_sync_rpc_sets_scope_and_returns_json() -> None:
    pool, _, cursor, _ = _sync_db()
    scope = _scope()
    cursor.description = [("payload",)]
    cursor.fetchall.return_value = [{"payload": '{"ok": true}'}]

    result = ScopedRpcCaller(
        RpcCaller(pool, "run_task", {"p_id": "1"}), scope,
    ).execute()

    assert result.data == {"ok": True}
    assert cursor.execute.call_args_list[0].args[1] == scope.settings
    assert '"run_task"' in cursor.execute.call_args_list[1].args[0]


def test_sync_rpc_adapts_mapping_and_list_params_as_jsonb() -> None:
    pool, _, cursor, _ = _sync_db()
    cursor.description = None

    ScopedRpcCaller(
        RpcCaller(
            pool,
            "register_asset",
            {"p_metadata": {"source": "web"}, "p_items": ["one"]},
        ),
        _scope(),
    ).execute()

    params = cursor.execute.call_args_list[1].args[1]
    assert all(isinstance(value, Jsonb) for value in params)
    assert params[0].obj == {"source": "web"}
    assert params[1].obj == ["one"]


def test_not_filter_keeps_scope_wrapper_until_execute() -> None:
    pool, _, cursor, _ = _sync_db()
    scope = _scope()
    cursor.description = [("id",)]
    cursor.fetchall.return_value = []

    result = (
        ScopedQueryBuilder(QueryBuilder(pool, "items"), scope)
        .select("*")
        .not_.eq("status", "deleted")
        .execute()
    )

    assert result.data == []
    assert cursor.execute.call_args_list[0].args[1] == scope.settings
    assert '"status" != %s' in cursor.execute.call_args_list[1].args[0]


def test_sync_query_error_exits_scope_transaction() -> None:
    pool, _, cursor, transaction = _sync_db()
    cursor.execute.side_effect = [None, RuntimeError("query failed")]

    with pytest.raises(RuntimeError, match="query failed"):
        ScopedQueryBuilder(
            QueryBuilder(pool, "items"), _scope(),
        ).select("*").execute()

    exit_args = transaction.__exit__.call_args.args
    assert exit_args[0] is RuntimeError


def test_unscoped_sync_query_keeps_legacy_autocommit_path() -> None:
    pool, connection, cursor, _ = _sync_db()
    cursor.description = [("id",)]
    cursor.fetchall.return_value = []

    QueryBuilder(pool, "items").select("*").execute()

    connection.transaction.assert_not_called()
    assert cursor.execute.call_count == 1
    assert connection.autocommit is True


@pytest.mark.asyncio
async def test_async_query_sets_scope_before_business_sql_in_transaction() -> None:
    pool, connection, cursor, transaction = _async_db()
    scope = _scope()
    cursor.description = [("id",)]
    cursor.fetchall.return_value = [{"id": 1}]

    result = await AsyncScopedQueryBuilder(
        AsyncQueryBuilder(pool, "items"), scope,
    ).select("*").execute()

    assert result.data == [{"id": 1}]
    assert cursor.execute.await_args_list[0].args == (
        SET_DATABASE_SCOPE_SQL,
        scope.settings,
    )
    assert 'SELECT * FROM "items"' in cursor.execute.await_args_list[1].args[0]
    connection.transaction.assert_called_once_with()
    transaction.__aenter__.assert_awaited_once()
    transaction.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_rpc_sets_scope_in_transaction() -> None:
    pool, _, cursor, _ = _async_db()
    scope = _scope()
    cursor.description = [("result",)]
    cursor.fetchall.return_value = [{"result": {"ok": True}}]

    result = await AsyncScopedRpcCaller(
        AsyncRpcCaller(pool, "run_task", {}), scope,
    ).execute()

    assert result.data == {"ok": True}
    assert cursor.execute.await_args_list[0].args[1] == scope.settings


@pytest.mark.asyncio
async def test_async_rpc_adapts_mapping_param_as_jsonb() -> None:
    pool, _, cursor, _ = _async_db()
    cursor.description = None

    await AsyncScopedRpcCaller(
        AsyncRpcCaller(pool, "register_asset", {"p_metadata": {"ok": True}}),
        _scope(),
    ).execute()

    value = cursor.execute.await_args_list[1].args[1][0]
    assert isinstance(value, Jsonb)
    assert value.obj == {"ok": True}


@pytest.mark.asyncio
async def test_async_query_error_exits_scope_transaction() -> None:
    pool, _, cursor, transaction = _async_db()
    cursor.execute.side_effect = [None, RuntimeError("query failed")]

    with pytest.raises(RuntimeError, match="query failed"):
        await AsyncScopedQueryBuilder(
            AsyncQueryBuilder(pool, "items"), _scope(),
        ).select("*").execute()

    exit_args = transaction.__aexit__.await_args.args
    assert exit_args[0] is RuntimeError


def test_scoped_clients_share_pool_without_mutating_base_client() -> None:
    pool = MagicMock()
    sync_client = object.__new__(LocalDBClient)
    sync_client._pool = pool
    async_client = object.__new__(AsyncLocalDBClient)
    async_client._pool = pool
    async_client._min_size = 1
    async_client._max_size = 2
    scope = _scope()

    scoped_sync = ScopedDatabaseClient(sync_client, scope)
    scoped_async = AsyncScopedDatabaseClient(async_client, scope)

    assert scoped_sync.pool is pool
    assert scoped_async.pool is pool
    assert scoped_sync.table("items")._scope is scope
    assert scoped_async.rpc("run")._scope is scope


def test_nested_sync_scope_reuses_base_client_and_replaces_scope() -> None:
    base = MagicMock()
    first = ScopedDatabaseClient(base, _scope())
    replacement = _scope()

    nested = ScopedDatabaseClient(first, replacement)

    assert nested._client is base
    assert nested.scope is replacement
