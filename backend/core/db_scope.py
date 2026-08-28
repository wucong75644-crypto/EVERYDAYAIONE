"""PostgreSQL RLS 使用的不可变事务级数据库身份。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any, AsyncIterator
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb



class DatabaseAccessKind(StrEnum):
    """允许进入普通数据库事务的服务类别。"""

    AUTHORIZATION = "authorization"
    PROJECTION = "projection"
    RUNTIME = "runtime"
    RUNTIME_ADMIN = "runtime_admin"
    SANDBOX_WORKER = "sandbox_worker"
    SYNC = "sync"
    WORKER = "worker"


@dataclass(frozen=True)
class PostgresArray:
    """显式标记 Scoped RPC 参数应按 PostgreSQL UUID 数组传递。"""

    values: list[UUID]

    def __init__(self, values: list[str | UUID]) -> None:
        object.__setattr__(self, "values", [UUID(str(value)) for value in values])


def _adapt_rpc_param(value: Any) -> Any:
    if isinstance(value, PostgresArray):
        return value.values
    return Jsonb(value) if isinstance(value, (dict, list)) else value


@dataclass(frozen=True)
class DatabaseScope:
    """随单次 Query/RPC 进入 PostgreSQL 事务的可信身份。"""

    actor_user_id: str | None
    org_id: str | None
    access_kind: DatabaseAccessKind
    request_id: str = ""

    def __post_init__(self) -> None:
        for field_name in ("actor_user_id", "org_id"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, str(UUID(value)))
        if len(self.request_id) > 128:
            raise ValueError("request_id exceeds 128 characters")

    @property
    def settings(self) -> tuple[str, str, str, str]:
        """返回 SET LOCAL 使用的固定顺序文本值。"""
        return (
            self.actor_user_id or "",
            self.org_id or "",
            self.access_kind.value,
            self.request_id,
        )


def database_scope_from_client(client: Any) -> DatabaseScope | None:
    """从显式 scoped client 或 OrgScopedDB 门面读取可信数据库身份。"""
    if isinstance(client, DatabaseScope):
        return client
    scope = getattr(client, "scope", None)
    if isinstance(scope, DatabaseScope):
        return scope
    wrapped = getattr(client, "_db", None)
    scope = getattr(wrapped, "scope", None)
    return scope if isinstance(scope, DatabaseScope) else None


SET_DATABASE_SCOPE_SQL = """
SELECT
    set_config('app.actor_user_id', %s, true),
    set_config('app.org_id', %s, true),
    set_config('app.access_kind', %s, true),
    set_config('app.request_id', %s, true)
"""


def _rpc_sql(name: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
    if not params:
        return f'SELECT public."{name}"()', []
    # PostgreSQL resolves an untyped parameter from its runtime value.  Small
    # Python integers (notably version 0/1) are therefore inferred as
    # ``smallint`` and fail against BIGINT RPC contracts.  Keep the RPC
    # surface named, while pinning the known numeric contract arguments.
    numeric_types = {
        "p_expected_action_version": "bigint",
        "p_expected_attempt_version": "bigint",
        "p_expected_version": "bigint",
        "p_fencing_token": "bigint",
        "p_executor_revision": "integer",
        "p_lease_seconds": "integer",
    }
    uuid_keys = {
        "p_action_id", "p_attempt_id", "p_dispatch_intent_id", "p_job_id",
        "p_claim_token", "p_receipt_id", "p_policy_receipt_id",
    }
    text_keys = {
        "p_external_idempotency_key", "p_request_hash", "p_executor_type",
        "p_runtime_revision", "p_workspace_scope_ref", "p_code_sha256",
        "p_terminal_status", "p_terminal_reason", "p_phase", "p_worker_id",
    }
    named_args = ", ".join(
        f"{key} := %s::{numeric_types[key]}"
        if key in numeric_types else (
            f"{key} := %s::uuid" if key in uuid_keys else
            f"{key} := %s::text" if key in text_keys else f"{key} := %s"
        )
        for key in params
    )
    values = [_adapt_rpc_param(value) for value in params.values()]
    return f'SELECT public."{name}"({named_args})', values


def _rpc_response(rows: list[dict[str, Any]]) -> Any:
    if len(rows) != 1 or len(rows[0]) != 1:
        return rows
    value = next(iter(rows[0].values()))
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped[0] in ("{", "["):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
    return value


class ScopedQueryBuilder:
    """在单个同步事务内注入 Scope 后执行既有 QueryBuilder。"""

    def __init__(self, builder: Any, scope: DatabaseScope):
        self._builder = builder
        self._scope = scope

    def __getattr__(self, name: str) -> Any:
        result = getattr(self._builder, name)
        if not callable(result):
            return result

        def chained(*args: Any, **kwargs: Any) -> Any:
            value = result(*args, **kwargs)
            return self if value is self._builder else value

        return chained

    @property
    def not_(self) -> _ScopedNotProxy:
        """保持取反过滤后的链式调用仍由 Scope 包装器执行。"""
        return _ScopedNotProxy(self, self._builder.not_)

    def execute(self) -> Any:
        from core.local_db import QueryResponse, RowNotFoundError, _serialize_row

        builders = {
            "select": self._builder._build_select,
            "insert": self._builder._build_insert,
            "upsert": self._builder._build_upsert,
            "update": self._builder._build_update,
            "delete": self._builder._build_delete,
        }
        sql, params = builders[self._builder._operation]()
        if not sql:
            return QueryResponse(data=[])
        total_count = None
        with self._builder._pool.connection() as connection:
            connection.autocommit = True
            with connection.transaction():
                with connection.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(SET_DATABASE_SCOPE_SQL, self._scope.settings)
                    cursor.execute(sql, params)
                    rows = cursor.fetchall() if cursor.description else []
                    if (
                        self._builder._count_mode == "exact"
                        and self._builder._operation == "select"
                    ):
                        count_sql, count_params = self._builder._build_count_query()
                        cursor.execute(count_sql, count_params)
                        total_count = cursor.fetchone()["count"]
        rows = [_serialize_row(row) for row in rows]
        if self._builder._single:
            if not rows:
                raise RowNotFoundError(self._builder._table)
            return QueryResponse(data=rows[0], count=total_count)
        if self._builder._maybe_single:
            return QueryResponse(
                data=rows[0] if rows else None,
                count=total_count,
            )
        return QueryResponse(data=rows, count=total_count)


class _ScopedNotProxy:
    """将底层 NOT 代理返回的 builder 重新包装为 scoped builder。"""

    def __init__(self, scoped_builder: ScopedQueryBuilder, proxy: Any):
        self._scoped_builder = scoped_builder
        self._proxy = proxy

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._proxy, name)

        def chained(*args: Any, **kwargs: Any) -> Any:
            value = method(*args, **kwargs)
            if value is self._scoped_builder._builder:
                return self._scoped_builder
            return value

        return chained


class ScopedRpcCaller:
    """在单个同步事务内注入 Scope 后调用 RPC。"""

    def __init__(self, caller: Any, scope: DatabaseScope):
        self._caller = caller
        self._scope = scope

    def execute(self) -> Any:
        from core.local_db import QueryResponse

        sql, params = _rpc_sql(self._caller._func_name, self._caller._params)
        with self._caller._pool.connection() as connection:
            connection.autocommit = True
            with connection.transaction():
                with connection.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(SET_DATABASE_SCOPE_SQL, self._scope.settings)
                    cursor.execute(sql, params)
                    rows = cursor.fetchall() if cursor.description else None
        return QueryResponse(
            data=_rpc_response(rows) if rows is not None else None
        )


class AsyncScopedQueryBuilder(ScopedQueryBuilder):
    """异步版事务级 Scope QueryBuilder。"""

    async def execute(self) -> Any:
        from core.local_db import QueryResponse, RowNotFoundError, _serialize_row

        builders = {
            "select": self._builder._build_select,
            "insert": self._builder._build_insert,
            "upsert": self._builder._build_upsert,
            "update": self._builder._build_update,
            "delete": self._builder._build_delete,
        }
        sql, params = builders[self._builder._operation]()
        if not sql:
            return QueryResponse(data=[])
        total_count = None
        async with self._builder._pool.connection() as connection:
            await connection.set_autocommit(True)
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        SET_DATABASE_SCOPE_SQL, self._scope.settings,
                    )
                    await cursor.execute(sql, params)
                    rows = await cursor.fetchall() if cursor.description else []
                    if (
                        self._builder._count_mode == "exact"
                        and self._builder._operation == "select"
                    ):
                        count_sql, count_params = self._builder._build_count_query()
                        await cursor.execute(count_sql, count_params)
                        row = await cursor.fetchone()
                        total_count = row["count"] if row else 0
        rows = [_serialize_row(row) for row in rows]
        if self._builder._single:
            if not rows:
                raise RowNotFoundError(self._builder._table)
            return QueryResponse(data=rows[0], count=total_count)
        if self._builder._maybe_single:
            return QueryResponse(
                data=rows[0] if rows else None,
                count=total_count,
            )
        return QueryResponse(data=rows, count=total_count)


class AsyncScopedRpcCaller(ScopedRpcCaller):
    """异步版事务级 Scope RPC。"""

    async def execute(self) -> Any:
        from core.local_db import QueryResponse

        sql, params = _rpc_sql(self._caller._func_name, self._caller._params)
        async with self._caller._pool.connection() as connection:
            await connection.set_autocommit(True)
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        SET_DATABASE_SCOPE_SQL, self._scope.settings,
                    )
                    await cursor.execute(sql, params)
                    rows = (
                        await cursor.fetchall()
                        if cursor.description else None
                    )
        return QueryResponse(
            data=_rpc_response(rows) if rows is not None else None
        )


class ScopedDatabaseClient:
    """共享基础 client 连接池的同步 Scope 门面。"""

    def __init__(self, client: Any, scope: DatabaseScope):
        self._client = (
            client._client
            if isinstance(client, ScopedDatabaseClient)
            else client
        )
        self.scope = scope

    @property
    def pool(self) -> Any:
        return self._client.pool

    def table(self, name: str) -> ScopedQueryBuilder:
        return ScopedQueryBuilder(self._client.table(name), self.scope)

    def rpc(
        self, name: str, params: dict[str, Any] | None = None,
    ) -> ScopedRpcCaller:
        return ScopedRpcCaller(self._client.rpc(name, params), self.scope)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class AsyncScopedDatabaseClient(ScopedDatabaseClient):
    """共享基础异步 client 连接池的 Scope 门面。"""

    def table(self, name: str) -> AsyncScopedQueryBuilder:
        return AsyncScopedQueryBuilder(self._client.table(name), self.scope)

    def rpc(
        self, name: str, params: dict[str, Any] | None = None,
    ) -> AsyncScopedRpcCaller:
        return AsyncScopedRpcCaller(self._client.rpc(name, params), self.scope)


class ScopedAsyncConnection:
    """禁止在 Scope 事务中途显式结束事务的异步连接门面。"""

    def __init__(self, connection: Any):
        self._connection = connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    async def commit(self) -> None:
        raise RuntimeError("SCOPED_CONNECTION_EXPLICIT_COMMIT_FORBIDDEN")

    async def rollback(self) -> None:
        raise RuntimeError("SCOPED_CONNECTION_EXPLICIT_ROLLBACK_FORBIDDEN")

    async def set_autocommit(self, _value: bool) -> None:
        raise RuntimeError("SCOPED_CONNECTION_AUTOCOMMIT_CHANGE_FORBIDDEN")


class AsyncScopedConnectionPool:
    """为任意异步 psycopg pool 提供事务级 DatabaseScope 连接。"""

    def __init__(self, pool: Any, scope: DatabaseScope):
        self._pool = pool
        self.scope = scope

    @asynccontextmanager
    async def connection(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[ScopedAsyncConnection]:
        async with self._pool.connection(*args, **kwargs) as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        SET_DATABASE_SCOPE_SQL,
                        self.scope.settings,
                    )
                yield ScopedAsyncConnection(connection)
