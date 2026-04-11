"""
AsyncLocalDBClient / AsyncQueryBuilder / AsyncRpcCaller 单元测试

测试异步 DB 基础设施层：SQL 构建复用、async execute、序列化、生命周期。
不连真实数据库，通过 mock AsyncConnectionPool 验证行为。
"""

import json
import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.local_db import (
    AsyncLocalDBClient,
    AsyncQueryBuilder,
    AsyncRpcCaller,
    QueryResponse,
)


# ── Fixtures ──────────────────────────────────────────


def _mock_async_pool():
    """创建 mock AsyncConnectionPool，模拟 async with pool.connection()

    psycopg3 async 的实际调用模式：
      pool.connection()  → sync，返回 async context manager
      conn.set_autocommit()  → async
      conn.cursor()  → sync，返回 async context manager
      cur.execute()  → async
      cur.fetchall()  → async
    """
    pool = MagicMock()
    conn = MagicMock()  # MagicMock：cursor() 是 sync 方法
    conn.set_autocommit = AsyncMock()  # 唯独 set_autocommit 是 async
    cur = AsyncMock()

    # async with pool.connection() as conn
    conn_ctx = AsyncMock()
    conn_ctx.__aenter__ = AsyncMock(return_value=conn)
    conn_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = conn_ctx

    # async with conn.cursor(row_factory=...) as cur
    cur_ctx = AsyncMock()
    cur_ctx.__aenter__ = AsyncMock(return_value=cur)
    cur_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.cursor.return_value = cur_ctx

    return pool, conn, cur


# ============================================================
# AsyncQueryBuilder — SELECT
# ============================================================


class TestAsyncQueryBuilderSelect:
    @pytest.mark.asyncio
    async def test_basic_select(self):
        """基础 SELECT * 查询"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",), ("name",)]
        cur.fetchall = AsyncMock(return_value=[
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ])

        result = await AsyncQueryBuilder(pool, "users").select("*").execute()

        assert len(result.data) == 2
        assert result.data[0]["name"] == "Alice"
        conn.set_autocommit.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_select_with_filters(self):
        """SELECT 带 eq/neq/gt 过滤"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])

        result = await (
            AsyncQueryBuilder(pool, "users")
            .select("id")
            .eq("status", "active")
            .neq("role", "admin")
            .gt("age", 18)
            .execute()
        )

        assert result.data == [{"id": 1}]
        # 验证 SQL 包含 WHERE 条件
        sql_arg = cur.execute.call_args[0][0]
        assert "WHERE" in sql_arg
        assert '"status"' in sql_arg

    @pytest.mark.asyncio
    async def test_select_count_exact(self):
        """SELECT with count="exact" 触发额外 COUNT 查询"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])
        cur.fetchone = AsyncMock(return_value={"count": 42})

        result = await (
            AsyncQueryBuilder(pool, "users")
            .select("id", count="exact")
            .execute()
        )

        assert result.count == 42
        assert cur.execute.await_count == 2  # SELECT + COUNT

    @pytest.mark.asyncio
    async def test_select_count_fetchone_none(self):
        """COUNT fetchone 返回 None 时兜底为 0"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[])
        cur.fetchone = AsyncMock(return_value=None)

        result = await (
            AsyncQueryBuilder(pool, "users")
            .select("id", count="exact")
            .execute()
        )

        assert result.count == 0

    @pytest.mark.asyncio
    async def test_select_single_found(self):
        """.single() 返回单行"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])

        result = await (
            AsyncQueryBuilder(pool, "users")
            .select("*").eq("id", 1).single()
            .execute()
        )

        assert result.data == {"id": 1}

    @pytest.mark.asyncio
    async def test_select_single_not_found(self):
        """.single() 无数据时抛 ValueError"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="Row not found"):
            await (
                AsyncQueryBuilder(pool, "users")
                .select("*").eq("id", 999).single()
                .execute()
            )

    @pytest.mark.asyncio
    async def test_select_maybe_single_found(self):
        """.maybe_single() 有数据返回行"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])

        result = await (
            AsyncQueryBuilder(pool, "users")
            .select("*").maybe_single()
            .execute()
        )

        assert result.data == {"id": 1}

    @pytest.mark.asyncio
    async def test_select_maybe_single_not_found(self):
        """.maybe_single() 无数据返回 None"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[])

        result = await (
            AsyncQueryBuilder(pool, "users")
            .select("*").maybe_single()
            .execute()
        )

        assert result.data is None

    @pytest.mark.asyncio
    async def test_select_no_description(self):
        """无 cursor.description 时返回空列表"""
        pool, conn, cur = _mock_async_pool()
        cur.description = None

        result = await AsyncQueryBuilder(pool, "users").select("*").execute()

        assert result.data == []


# ============================================================
# AsyncQueryBuilder — INSERT / UPSERT / UPDATE / DELETE
# ============================================================


class TestAsyncQueryBuilderMutations:
    @pytest.mark.asyncio
    async def test_insert(self):
        """INSERT 返回 RETURNING 行"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",), ("name",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1, "name": "Alice"}])

        result = await (
            AsyncQueryBuilder(pool, "users")
            .insert({"name": "Alice"})
            .execute()
        )

        assert result.data[0]["name"] == "Alice"
        sql = cur.execute.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "RETURNING" in sql

    @pytest.mark.asyncio
    async def test_upsert_with_conflict(self):
        """UPSERT ON CONFLICT DO UPDATE"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])

        result = await (
            AsyncQueryBuilder(pool, "users")
            .upsert({"id": 1, "name": "Bob"}, on_conflict="id")
            .execute()
        )

        sql = cur.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql

    @pytest.mark.asyncio
    async def test_update(self):
        """UPDATE 带 WHERE 过滤"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])

        await (
            AsyncQueryBuilder(pool, "users")
            .update({"name": "Charlie"})
            .eq("id", 1)
            .execute()
        )

        sql = cur.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "WHERE" in sql

    @pytest.mark.asyncio
    async def test_delete(self):
        """DELETE 带 WHERE"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])

        await (
            AsyncQueryBuilder(pool, "users")
            .delete().eq("id", 1)
            .execute()
        )

        sql = cur.execute.call_args[0][0]
        assert "DELETE FROM" in sql

    @pytest.mark.asyncio
    async def test_empty_insert_returns_empty(self):
        """空数据 INSERT 直接返回空"""
        pool, conn, cur = _mock_async_pool()

        result = await (
            AsyncQueryBuilder(pool, "users")
            .insert([])
            .execute()
        )

        assert result.data == []
        cur.execute.assert_not_awaited()


# ============================================================
# AsyncQueryBuilder — 序列化
# ============================================================


class TestAsyncQueryBuilderSerialization:
    @pytest.mark.asyncio
    async def test_uuid_serialized_to_string(self):
        """UUID 对象序列化为字符串"""
        pool, conn, cur = _mock_async_pool()
        test_uuid = uuid.uuid4()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": test_uuid}])

        result = await AsyncQueryBuilder(pool, "users").select("id").execute()

        assert result.data[0]["id"] == str(test_uuid)

    @pytest.mark.asyncio
    async def test_datetime_serialized_to_iso(self):
        """datetime 对象序列化为 ISO 字符串"""
        pool, conn, cur = _mock_async_pool()
        now = datetime(2026, 3, 27, 12, 0, 0)
        cur.description = [("created_at",)]
        cur.fetchall = AsyncMock(return_value=[{"created_at": now}])

        result = await AsyncQueryBuilder(pool, "users").select("*").execute()

        assert result.data[0]["created_at"] == "2026-03-27T12:00:00"

    @pytest.mark.asyncio
    async def test_decimal_serialized_to_float(self):
        """Decimal 对象序列化为 float"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("price",)]
        cur.fetchall = AsyncMock(return_value=[{"price": Decimal("19.99")}])

        result = await AsyncQueryBuilder(pool, "users").select("*").execute()

        assert result.data[0]["price"] == 19.99

    @pytest.mark.asyncio
    async def test_json_data_in_insert(self):
        """dict/list 字段在 INSERT 时自动 JSON 序列化"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",)]
        cur.fetchall = AsyncMock(return_value=[{"id": 1}])

        await (
            AsyncQueryBuilder(pool, "users")
            .insert({"id": 1, "meta": {"key": "value"}})
            .execute()
        )

        params = cur.execute.call_args[0][1]
        # meta 字段应该被 JSON 序列化
        assert '{"key": "value"}' in [str(p) for p in params]


# ============================================================
# AsyncRpcCaller
# ============================================================


class TestAsyncRpcCaller:
    @pytest.mark.asyncio
    async def test_rpc_with_params(self):
        """RPC 带参数调用"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("result",)]
        cur.fetchall = AsyncMock(return_value=[{"result": 42}])

        result = await AsyncRpcCaller(
            pool, "my_func", {"p_id": 1}
        ).execute()

        assert result.data == 42
        sql = cur.execute.call_args[0][0]
        assert '"my_func"' in sql
        assert "p_id := %s" in sql

    @pytest.mark.asyncio
    async def test_rpc_no_params(self):
        """RPC 无参数调用"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("result",)]
        cur.fetchall = AsyncMock(return_value=[{"result": "ok"}])

        result = await AsyncRpcCaller(pool, "ping", {}).execute()

        assert result.data == "ok"
        sql = cur.execute.call_args[0][0]
        assert '"ping"()' in sql

    @pytest.mark.asyncio
    async def test_rpc_jsonb_auto_parse(self):
        """RPC 返回 JSONB 字符串自动解析"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("result",)]
        cur.fetchall = AsyncMock(return_value=[
            {"result": '{"status": "ok", "count": 5}'}
        ])

        result = await AsyncRpcCaller(pool, "get_stats", {}).execute()

        assert isinstance(result.data, dict)
        assert result.data["status"] == "ok"
        assert result.data["count"] == 5

    @pytest.mark.asyncio
    async def test_rpc_jsonb_array_parse(self):
        """RPC 返回 JSON 数组字符串自动解析"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("result",)]
        cur.fetchall = AsyncMock(return_value=[
            {"result": '[1, 2, 3]'}
        ])

        result = await AsyncRpcCaller(pool, "get_ids", {}).execute()

        assert result.data == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_rpc_plain_string_not_parsed(self):
        """RPC 返回普通字符串不触发 JSON 解析"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("result",)]
        cur.fetchall = AsyncMock(return_value=[{"result": "hello world"}])

        result = await AsyncRpcCaller(pool, "greet", {}).execute()

        assert result.data == "hello world"

    @pytest.mark.asyncio
    async def test_rpc_no_description(self):
        """RPC 无结果集返回 None"""
        pool, conn, cur = _mock_async_pool()
        cur.description = None

        result = await AsyncRpcCaller(pool, "void_func", {}).execute()

        assert result.data is None

    @pytest.mark.asyncio
    async def test_rpc_multiple_rows(self):
        """RPC 返回多行"""
        pool, conn, cur = _mock_async_pool()
        cur.description = [("id",), ("name",)]
        cur.fetchall = AsyncMock(return_value=[
            {"id": 1, "name": "A"},
            {"id": 2, "name": "B"},
        ])

        result = await AsyncRpcCaller(pool, "list_items", {}).execute()

        assert len(result.data) == 2


# ============================================================
# AsyncLocalDBClient
# ============================================================


class TestAsyncLocalDBClient:
    def test_table_returns_async_query_builder(self):
        """table() 返回 AsyncQueryBuilder"""
        with patch("core.local_db.AsyncConnectionPool"):
            client = AsyncLocalDBClient("postgresql://localhost/test")
            builder = client.table("users")
            assert isinstance(builder, AsyncQueryBuilder)

    def test_rpc_returns_async_rpc_caller(self):
        """rpc() 返回 AsyncRpcCaller"""
        with patch("core.local_db.AsyncConnectionPool"):
            client = AsyncLocalDBClient("postgresql://localhost/test")
            caller = client.rpc("my_func", {"a": 1})
            assert isinstance(caller, AsyncRpcCaller)

    def test_pool_property_exposed(self):
        """pool 属性暴露连接池"""
        with patch("core.local_db.AsyncConnectionPool") as MockPool:
            client = AsyncLocalDBClient("postgresql://localhost/test")
            assert client.pool is client._pool

    @pytest.mark.asyncio
    async def test_open_calls_pool_open(self):
        """open() 调用连接池 open"""
        with patch("core.local_db.AsyncConnectionPool") as MockPool:
            mock_pool = AsyncMock()
            MockPool.return_value = mock_pool
            client = AsyncLocalDBClient("postgresql://localhost/test")
            await client.open()
            mock_pool.open.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_calls_pool_close(self):
        """close() 调用连接池 close"""
        with patch("core.local_db.AsyncConnectionPool") as MockPool:
            mock_pool = AsyncMock()
            MockPool.return_value = mock_pool
            client = AsyncLocalDBClient("postgresql://localhost/test")
            await client.close()
            mock_pool.close.assert_awaited_once()

    def test_pool_forces_session_timezone_asia_shanghai(self):
        """连接池必须显式强制 PG session TZ=Asia/Shanghai

        防止重构时不小心删掉 options 参数，导致整套时间架构回退到
        依赖系统 TZ 的隐式链状态（迁云数据库/Docker/主从异地复制时
        会出现 ±8h 偏移）。详见 commit 39b6f81。
        """
        with patch("core.local_db.AsyncConnectionPool") as MockPool:
            AsyncLocalDBClient("postgresql://localhost/test")
            call_kwargs = MockPool.call_args.kwargs
            psycopg_kwargs = call_kwargs.get("kwargs", {})
            assert psycopg_kwargs.get("options") == "-c timezone=Asia/Shanghai", (
                "AsyncLocalDBClient must force PG session TZ via "
                "options='-c timezone=Asia/Shanghai' to prevent implicit TZ "
                "dependency. See commit 39b6f81."
            )
