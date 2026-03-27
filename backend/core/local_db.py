"""
本地 PostgreSQL 兼容层 — 模拟 Supabase SDK 的链式查询 API

替换 Supabase SDK，使业务代码无需改动即可切换到本地 PostgreSQL。
支持的操作：table/select/insert/upsert/update/delete/eq/neq/in_/lt/gt/
            gte/lte/or_/is_/not_/order/limit/range/single/maybe_single/execute/rpc
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from loguru import logger


# ============================================================
# 响应对象（兼容 Supabase SDK 的 response.data 格式）
# ============================================================

@dataclass
class QueryResponse:
    """模拟 Supabase SDK 的 APIResponse"""
    data: Any = None
    count: Optional[int] = None


def _serialize_row(row: dict) -> dict:
    """将 PostgreSQL 原生类型（UUID、datetime、Decimal 等）转为 JSON 可序列化类型

    Supabase SDK 返回的都是字符串/数字，本地 PostgreSQL 返回原生 Python 类型。
    此函数在查询结果返回前统一转换，确保上层业务代码无需关心数据库差异。
    """
    import uuid
    from datetime import datetime, date, time
    from decimal import Decimal

    result = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, date):
            result[k] = v.isoformat()
        elif isinstance(v, time):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result


# ============================================================
# 查询构造器（链式 API）
# ============================================================

class QueryBuilder:
    """
    模拟 Supabase 的链式查询构造器。

    用法与 Supabase SDK 完全一致：
        db.table("users").select("*").eq("id", user_id).single().execute()
    """

    def __init__(self, pool: ConnectionPool, table_name: str):
        self._pool = pool
        self._table = table_name
        self._operation: str = "select"  # select/insert/upsert/update/delete
        self._columns: str = "*"
        self._count_mode: Optional[str] = None  # "exact" / "planned" / "estimated"
        self._filters: list[tuple[str, str, Any]] = []
        self._or_filters: list[str] = []  # 原始 OR 条件
        self._order_clauses: list[tuple[str, bool]] = []
        self._limit_val: Optional[int] = None
        self._offset_val: Optional[int] = None
        self._single: bool = False
        self._maybe_single: bool = False
        self._data: Any = None
        self._on_conflict: Optional[str] = None

    # ---- 操作类型 ----

    def select(self, columns: str = "*", *, count: Optional[str] = None) -> QueryBuilder:
        self._operation = "select"
        self._columns = columns
        self._count_mode = count
        return self

    def insert(self, data: dict | list[dict]) -> QueryBuilder:
        self._operation = "insert"
        self._data = data if isinstance(data, list) else [data]
        return self

    def upsert(
        self, data: dict | list[dict], *, on_conflict: str = ""
    ) -> QueryBuilder:
        self._operation = "upsert"
        self._data = data if isinstance(data, list) else [data]
        self._on_conflict = on_conflict
        return self

    def update(self, data: dict) -> QueryBuilder:
        self._operation = "update"
        self._data = data
        return self

    def delete(self) -> QueryBuilder:
        self._operation = "delete"
        return self

    # ---- 过滤器 ----

    def eq(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "=", value))
        return self

    def neq(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "!=", value))
        return self

    def gt(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, ">", value))
        return self

    def gte(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, ">=", value))
        return self

    def lt(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "<", value))
        return self

    def lte(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "<=", value))
        return self

    def in_(self, column: str, values: list) -> QueryBuilder:
        if values:
            self._filters.append((column, "IN", tuple(values)))
        else:
            self._filters.append(("1", "=", 0))
        return self

    def is_(self, column: str, value: str) -> QueryBuilder:
        """NULL 检查：.is_("col", "null") → WHERE col IS NULL"""
        if value == "null" or value is None:
            self._filters.append((column, "IS NULL", None))
        else:
            self._filters.append((column, "IS NOT NULL", None))
        return self

    def like(self, column: str, pattern: str) -> QueryBuilder:
        self._filters.append((column, "LIKE", pattern))
        return self

    def ilike(self, column: str, pattern: str) -> QueryBuilder:
        self._filters.append((column, "ILIKE", pattern))
        return self

    def or_(self, filter_str: str) -> QueryBuilder:
        """
        Supabase OR 过滤器。
        用法: .or_("outer_id.eq.ABC,sku_outer_id.eq.ABC")
        生成: WHERE (outer_id = 'ABC' OR sku_outer_id = 'ABC')
        """
        self._or_filters.append(filter_str)
        return self

    @property
    def not_(self) -> _NotProxy:
        """取反代理: .not_.is_("col", "null") → WHERE col IS NOT NULL"""
        return _NotProxy(self)

    # ---- 排序 / 分页 ----

    def order(self, column: str, *, desc: bool = False) -> QueryBuilder:
        self._order_clauses.append((column, desc))
        return self

    def limit(self, count: int) -> QueryBuilder:
        self._limit_val = count
        return self

    def range(self, start: int, end: int) -> QueryBuilder:
        self._offset_val = start
        self._limit_val = max(end - start + 1, 0)
        return self

    def single(self) -> QueryBuilder:
        self._single = True
        self._limit_val = 1
        return self

    def maybe_single(self) -> QueryBuilder:
        self._maybe_single = True
        self._limit_val = 1
        return self

    # ---- 构建 SQL ----

    @staticmethod
    def _quote_col(col: str) -> str:
        """安全引用列名，跳过表达式和已引用的列"""
        # 已引用
        if col.startswith('"'):
            return col
        # JSONB 路径（如 generation_params->>type）
        if "->" in col or "::" in col:
            return col
        # 简单标识符
        if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
            return f'"{col}"'
        # 其他表达式（函数调用、AS 等）原样返回
        return col

    def _build_where(self, params: list) -> str:
        clauses = []

        for col, op, val in self._filters:
            if op in ("IS NULL", "IS NOT NULL"):
                clauses.append(f'{self._quote_col(col)} {op}')
            elif op == "IN":
                placeholders = ", ".join(["%s"] * len(val))
                clauses.append(f'{self._quote_col(col)} IN ({placeholders})')
                params.extend(val)
            else:
                clauses.append(f'{self._quote_col(col)} {op} %s')
                params.append(val)

        # OR 过滤器（解析 Supabase 格式）
        for or_str in self._or_filters:
            or_clause = self._parse_or_filter(or_str, params)
            if or_clause:
                clauses.append(f"({or_clause})")

        if not clauses:
            return ""
        return " WHERE " + " AND ".join(clauses)

    @staticmethod
    def _parse_or_filter(filter_str: str, params: list) -> str:
        """
        解析 Supabase OR 过滤格式。
        "outer_id.eq.ABC,sku_outer_id.eq.ABC"
        → "outer_id = %s OR sku_outer_id = %s"  (params: ['ABC', 'ABC'])
        """
        op_map = {
            "eq": "=", "neq": "!=", "gt": ">", "gte": ">=",
            "lt": "<", "lte": "<=", "like": "LIKE", "ilike": "ILIKE",
            "is": "IS",
        }
        parts = []
        for segment in filter_str.split(","):
            segment = segment.strip()
            # 格式: column.operator.value
            match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\.(eq|neq|gt|gte|lt|lte|like|ilike|is)\.(.+)$', segment)
            if not match:
                continue
            col, op_key, val = match.groups()
            sql_op = op_map.get(op_key, "=")
            if sql_op == "IS":
                if val == "null":
                    parts.append(f'"{col}" IS NULL')
                else:
                    parts.append(f'"{col}" IS NOT NULL')
            else:
                parts.append(f'"{col}" {sql_op} %s')
                params.append(val)
        return " OR ".join(parts)

    def _build_order(self) -> str:
        if not self._order_clauses:
            return ""
        parts = []
        for col, desc in self._order_clauses:
            parts.append(f'{self._quote_col(col)} {"DESC" if desc else "ASC"}')
        return " ORDER BY " + ", ".join(parts)

    def _build_limit_offset(self) -> str:
        sql = ""
        if self._limit_val is not None:
            sql += f" LIMIT {self._limit_val}"
        if self._offset_val is not None:
            sql += f" OFFSET {self._offset_val}"
        return sql

    def _build_select(self) -> tuple[str, list]:
        params: list = []
        if self._columns == "*":
            cols = "*"
        else:
            col_list = [c.strip() for c in self._columns.split(",")]
            cols = ", ".join(self._quote_col(c) for c in col_list)
        sql = f'SELECT {cols} FROM "{self._table}"'
        sql += self._build_where(params)
        sql += self._build_order()
        sql += self._build_limit_offset()
        return sql, params

    def _build_insert(self) -> tuple[str, list]:
        rows = self._data
        if not rows:
            return "", []
        columns = list(rows[0].keys())
        col_sql = ", ".join(f'"{c}"' for c in columns)
        row_placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
        all_placeholders = ", ".join([row_placeholders] * len(rows))
        params: list = []
        for row in rows:
            for c in columns:
                val = row.get(c)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                params.append(val)
        sql = f'INSERT INTO "{self._table}" ({col_sql}) VALUES {all_placeholders} RETURNING *'
        return sql, params

    def _build_upsert(self) -> tuple[str, list]:
        rows = self._data
        if not rows:
            return "", []
        columns = list(rows[0].keys())
        col_sql = ", ".join(f'"{c}"' for c in columns)
        row_placeholders = "(" + ", ".join(["%s"] * len(columns)) + ")"
        all_placeholders = ", ".join([row_placeholders] * len(rows))
        params: list = []
        for row in rows:
            for c in columns:
                val = row.get(c)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                params.append(val)

        sql = f'INSERT INTO "{self._table}" ({col_sql}) VALUES {all_placeholders}'

        if self._on_conflict:
            conflict_cols = ", ".join(
                f'"{c.strip()}"' for c in self._on_conflict.split(",")
            )
            sql += f" ON CONFLICT ({conflict_cols})"
        else:
            sql += " ON CONFLICT"

        conflict_col_set = set(
            c.strip() for c in (self._on_conflict or "").split(",") if c.strip()
        )
        update_cols = [c for c in columns if c not in conflict_col_set]
        if update_cols:
            set_clauses = ", ".join(
                f'"{c}" = EXCLUDED."{c}"' for c in update_cols
            )
            sql += f" DO UPDATE SET {set_clauses}"
        else:
            sql += " DO NOTHING"

        sql += " RETURNING *"
        return sql, params

    def _build_update(self) -> tuple[str, list]:
        params: list = []
        set_clauses = []
        for col, val in self._data.items():
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            set_clauses.append(f'"{col}" = %s')
            params.append(val)
        sql = f'UPDATE "{self._table}" SET {", ".join(set_clauses)}'
        sql += self._build_where(params)
        sql += " RETURNING *"
        return sql, params

    def _build_delete(self) -> tuple[str, list]:
        params: list = []
        sql = f'DELETE FROM "{self._table}"'
        sql += self._build_where(params)
        sql += " RETURNING *"
        return sql, params

    # ---- 执行 ----

    def execute(self) -> QueryResponse:
        builders = {
            "select": self._build_select,
            "insert": self._build_insert,
            "upsert": self._build_upsert,
            "update": self._build_update,
            "delete": self._build_delete,
        }
        sql, params = builders[self._operation]()
        if not sql:
            return QueryResponse(data=[])

        total_count = None

        with self._pool.connection() as conn:
            conn.autocommit = True
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                if cur.description:
                    rows = cur.fetchall()
                else:
                    rows = []

                # count 模式：额外查询总数
                if self._count_mode == "exact" and self._operation == "select":
                    count_sql, count_params = self._build_count_query()
                    cur.execute(count_sql, count_params)
                    total_count = cur.fetchone()["count"]

        # PostgreSQL 返回原生类型（UUID、datetime 等），
        # 统一转为字符串以兼容 Supabase SDK 的行为
        rows = [_serialize_row(r) for r in rows]

        # 单行模式
        if self._single:
            if not rows:
                raise ValueError(
                    f"Row not found in table '{self._table}'"
                )
            return QueryResponse(data=rows[0], count=total_count)

        if self._maybe_single:
            return QueryResponse(data=rows[0] if rows else None, count=total_count)

        return QueryResponse(data=rows, count=total_count)

    def _build_count_query(self) -> tuple[str, list]:
        """构建 COUNT(*) 查询（用于 select(count="exact")）"""
        params: list = []
        sql = f'SELECT COUNT(*) as count FROM "{self._table}"'
        sql += self._build_where(params)
        return sql, params


# ============================================================
# NOT 代理（支持 .not_.is_() 链式调用）
# ============================================================

class _NotProxy:
    """代理类，将 .not_.is_("col", "null") 转为 IS NOT NULL"""

    def __init__(self, builder: QueryBuilder):
        self._builder = builder

    def is_(self, column: str, value: str) -> QueryBuilder:
        if value == "null" or value is None:
            self._builder._filters.append((column, "IS NOT NULL", None))
        else:
            self._builder._filters.append((column, "IS NULL", None))
        return self._builder

    def eq(self, column: str, value: Any) -> QueryBuilder:
        self._builder._filters.append((column, "!=", value))
        return self._builder

    def in_(self, column: str, values: list) -> QueryBuilder:
        if values:
            self._builder._filters.append((column, "NOT IN", tuple(values)))
        else:
            pass  # NOT IN empty list → always true → no filter
        return self._builder


# ============================================================
# RPC 调用器
# ============================================================

class RpcCaller:
    """模拟 Supabase 的 db.rpc("func_name", params).execute()"""

    def __init__(self, pool: ConnectionPool, func_name: str, params: dict):
        self._pool = pool
        self._func_name = func_name
        self._params = params

    def execute(self) -> QueryResponse:
        if self._params:
            named_args = ", ".join(
                f"{k} := %s" for k in self._params
            )
            sql = f'SELECT "{self._func_name}"({named_args})'
            params = list(self._params.values())
        else:
            sql = f'SELECT "{self._func_name}"()'
            params = []

        with self._pool.connection() as conn:
            conn.autocommit = True
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                if cur.description:
                    rows = cur.fetchall()
                    if len(rows) == 1 and len(rows[0]) == 1:
                        val = list(rows[0].values())[0]
                        # JSONB 自动解析（字典或数组）
                        if isinstance(val, str):
                            stripped = val.strip()
                            if stripped and stripped[0] in ('{', '['):
                                try:
                                    val = json.loads(val)
                                except json.JSONDecodeError:
                                    pass
                        return QueryResponse(data=val)
                    return QueryResponse(data=rows)
                return QueryResponse(data=None)


# ============================================================
# 主客户端类
# ============================================================

class LocalDBClient:
    """
    本地 PostgreSQL 客户端，兼容 Supabase SDK 的链式查询接口。

    用法：
        client = LocalDBClient("postgresql://user:pass@localhost:5432/db")
        result = client.table("users").select("*").eq("id", uid).execute()
        print(result.data)
    """

    def __init__(self, database_url: str, *, min_size: int = 2, max_size: int = 10):
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
        )
        logger.info(f"LocalDB 连接池已创建 | min={min_size} max={max_size}")

    def table(self, table_name: str) -> QueryBuilder:
        return QueryBuilder(self._pool, table_name)

    def rpc(self, func_name: str, params: Optional[dict] = None) -> RpcCaller:
        return RpcCaller(self._pool, func_name, params or {})

    def close(self) -> None:
        self._pool.close()
        logger.info("LocalDB 连接池已关闭")

    @property
    def pool(self) -> ConnectionPool:
        """暴露连接池，供需要原生 SQL 的场景使用"""
        return self._pool


# ============================================================
# 异步查询构造器（复用 QueryBuilder 的 SQL 构建，仅重写 execute）
# ============================================================

class AsyncQueryBuilder(QueryBuilder):
    """异步版 QueryBuilder — SQL 构建逻辑 100% 复用父类，execute() 改为 async"""

    async def execute(self) -> QueryResponse:
        builders = {
            "select": self._build_select,
            "insert": self._build_insert,
            "upsert": self._build_upsert,
            "update": self._build_update,
            "delete": self._build_delete,
        }
        sql, params = builders[self._operation]()
        if not sql:
            return QueryResponse(data=[])

        total_count = None

        async with self._pool.connection() as conn:
            await conn.set_autocommit(True)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, params)
                if cur.description:
                    rows = await cur.fetchall()
                else:
                    rows = []

                if self._count_mode == "exact" and self._operation == "select":
                    count_sql, count_params = self._build_count_query()
                    await cur.execute(count_sql, count_params)
                    row = await cur.fetchone()
                    total_count = row["count"] if row else 0

        rows = [_serialize_row(r) for r in rows]

        if self._single:
            if not rows:
                raise ValueError(
                    f"Row not found in table '{self._table}'"
                )
            return QueryResponse(data=rows[0], count=total_count)

        if self._maybe_single:
            return QueryResponse(data=rows[0] if rows else None, count=total_count)

        return QueryResponse(data=rows, count=total_count)


# ============================================================
# 异步 RPC 调用器
# ============================================================

class AsyncRpcCaller(RpcCaller):
    """异步版 RpcCaller — execute() 改为 async"""

    async def execute(self) -> QueryResponse:
        if self._params:
            named_args = ", ".join(
                f"{k} := %s" for k in self._params
            )
            sql = f'SELECT "{self._func_name}"({named_args})'
            params = list(self._params.values())
        else:
            sql = f'SELECT "{self._func_name}"()'
            params = []

        async with self._pool.connection() as conn:
            await conn.set_autocommit(True)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, params)
                if cur.description:
                    rows = await cur.fetchall()
                    if len(rows) == 1 and len(rows[0]) == 1:
                        val = list(rows[0].values())[0]
                        if isinstance(val, str):
                            stripped = val.strip()
                            if stripped and stripped[0] in ('{', '['):
                                try:
                                    val = json.loads(val)
                                except json.JSONDecodeError:
                                    pass
                        return QueryResponse(data=val)
                    return QueryResponse(data=rows)
                return QueryResponse(data=None)


# ============================================================
# 异步主客户端类
# ============================================================

class AsyncLocalDBClient:
    """
    异步版本地 PostgreSQL 客户端，链式查询接口与 LocalDBClient 一致。

    用法：
        client = AsyncLocalDBClient("postgresql://user:pass@localhost:5432/db")
        await client.open()
        result = await client.table("users").select("*").eq("id", uid).execute()
        await client.close()
    """

    def __init__(self, database_url: str, *, min_size: int = 2, max_size: int = 10):
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        self._min_size = min_size
        self._max_size = max_size

    async def open(self) -> None:
        """打开连接池（必须在 async 上下文中调用）"""
        await self._pool.open()
        logger.info(
            f"AsyncLocalDB 连接池已打开 | min={self._min_size} max={self._max_size}"
        )

    async def close(self) -> None:
        """关闭连接池"""
        await self._pool.close()
        logger.info("AsyncLocalDB 连接池已关闭")

    def table(self, table_name: str) -> AsyncQueryBuilder:
        return AsyncQueryBuilder(self._pool, table_name)

    def rpc(self, func_name: str, params: Optional[dict] = None) -> AsyncRpcCaller:
        return AsyncRpcCaller(self._pool, func_name, params or {})

    @property
    def pool(self) -> AsyncConnectionPool:
        """暴露连接池，供需要原生 SQL 的场景使用"""
        return self._pool
