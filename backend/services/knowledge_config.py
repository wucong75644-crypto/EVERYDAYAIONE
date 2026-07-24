"""
知识库基础设施

PostgreSQL 直连（psycopg）管理、DashScope embedding 客户端、TTL 缓存。
"""

import asyncio
import hashlib
import time
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from core.config import settings
from core.db_scope import (
    AsyncScopedConnectionPool,
    DatabaseAccessKind,
    DatabaseScope,
    SET_DATABASE_SCOPE_SQL,
    database_scope_from_client,
)

# ===== 常量 =====

EMBEDDING_DIMS = 1024
EMBEDDING_MODEL = "text-embedding-v3"

# psycopg 连接超时
PG_CONNECT_TIMEOUT = 10

# 知识检索缓存: {cache_key: {"data": [...], "ts": float}}
_search_cache: Dict[str, Dict[str, Any]] = {}

# ===== PostgreSQL 直连管理 =====

_pg_pool = None
_worker_pg_pool = None
_kb_available: Optional[bool] = None  # None=未检查, True=可用, False=不可用
_worker_kb_available: Optional[bool] = None
_kb_lock = asyncio.Lock()
_worker_kb_lock = asyncio.Lock()


async def _get_pg_pool():
    """获取 psycopg AsyncConnectionPool（单例 + 延迟初始化）"""
    global _pg_pool, _kb_available

    if _kb_available is False:
        return None
    if _pg_pool is not None:
        return _pg_pool

    async with _kb_lock:
        if _kb_available is False:
            return None
        if _pg_pool is not None:
            return _pg_pool

        db_url = settings.effective_db_url
        if not db_url:
            logger.warning("DATABASE_URL/SUPABASE_DB_URL not configured, knowledge base disabled")
            _kb_available = False
            return None

        try:
            from psycopg_pool import AsyncConnectionPool

            _pg_pool = AsyncConnectionPool(
                conninfo=db_url,
                min_size=1,
                max_size=3,
                # 与 core/local_db.py 保持一致：强制 PG session TZ=Asia/Shanghai
                # 防止迁云数据库 / Docker / 主从异地复制时 timezone-sensitive 的
                # cast 行为出错。详见 commit 39b6f81。
                kwargs={"options": "-c timezone=Asia/Shanghai"},
                # check: 取连接前验活，死连接自动丢弃重建
                check=AsyncConnectionPool.check_connection,
                # 连接最多存活 10 分钟后主动回收，防止 PG 侧静默断开
                max_lifetime=600,
                open=False,
            )
            await _pg_pool.open()
            _kb_available = True
            logger.info("Knowledge base PostgreSQL pool initialized | tz=Asia/Shanghai")
            return _pg_pool
        except Exception as e:
            _kb_available = False
            logger.error(f"Knowledge base PostgreSQL init failed | error={e}")
            return None


async def _get_worker_pg_pool():
    """获取只使用 WORKER_DATABASE_URL 的后台知识库连接池。"""
    global _worker_pg_pool, _worker_kb_available

    if _worker_kb_available is False:
        return None
    if _worker_pg_pool is not None:
        return _worker_pg_pool

    async with _worker_kb_lock:
        if _worker_kb_available is False:
            return None
        if _worker_pg_pool is not None:
            return _worker_pg_pool

        db_url = settings.worker_database_url
        if not db_url:
            raise RuntimeError("WORKER_DATABASE_URL_REQUIRED")

        try:
            from psycopg_pool import AsyncConnectionPool

            _worker_pg_pool = AsyncConnectionPool(
                conninfo=db_url,
                min_size=1,
                max_size=3,
                kwargs={"options": "-c timezone=Asia/Shanghai"},
                check=AsyncConnectionPool.check_connection,
                max_lifetime=600,
                open=False,
            )
            await _worker_pg_pool.open()
            _worker_kb_available = True
            logger.info(
                "Worker knowledge PostgreSQL pool initialized | tz=Asia/Shanghai"
            )
            return _worker_pg_pool
        except Exception as exc:
            _worker_kb_available = False
            logger.error(
                "Worker knowledge PostgreSQL init failed | "
                f"error_type={type(exc).__name__}"
            )
            return None


async def close_pg_pools() -> None:
    """关闭 runtime/worker knowledge raw pools 并清除进程状态。"""
    global _pg_pool, _worker_pg_pool, _kb_available, _worker_kb_available

    for pool in (_pg_pool, _worker_pg_pool):
        if pool is not None:
            await pool.close()
    _pg_pool = None
    _worker_pg_pool = None
    _kb_available = None
    _worker_kb_available = None


async def get_pg_connection(
    db_source: Any = None,
    *,
    database_scope: DatabaseScope | None = None,
):
    """获取已注入可信数据库身份的 psycopg 异步连接上下文。"""
    resolved_scope = database_scope or database_scope_from_client(db_source)
    if resolved_scope is None:
        raise RuntimeError("DATABASE_SCOPE_REQUIRED")
    pool = (
        await _get_worker_pg_pool()
        if resolved_scope.access_kind == DatabaseAccessKind.WORKER
        else await _get_pg_pool()
    )
    if pool is None:
        return None
    return AsyncScopedConnectionPool(pool, resolved_scope).connection()


async def create_dedicated_connection(
    statement_timeout_s: int = 15,
    *,
    database_scope: DatabaseScope,
):
    """
    创建独立的 psycopg 异步连接（不走共享池）。

    用于后台批量任务（如 model_scorer 聚合），避免与在线业务争抢连接池。
    调用方需用 async with 管理生命周期。

    Args:
        statement_timeout_s: 单条 SQL 超时秒数，防止慢查询无限 hang
    """
    import psycopg

    db_url = (
        settings.worker_database_url
        if database_scope.access_kind == DatabaseAccessKind.WORKER
        else settings.effective_db_url
    )
    if not db_url:
        if database_scope.access_kind == DatabaseAccessKind.WORKER:
            raise RuntimeError("WORKER_DATABASE_URL_REQUIRED")
        return None

    connection = await psycopg.AsyncConnection.connect(
        conninfo=db_url,
        autocommit=False,
        options=f"-c timezone=Asia/Shanghai -c statement_timeout={statement_timeout_s * 1000}",
        connect_timeout=PG_CONNECT_TIMEOUT,
    )
    await connection.execute(SET_DATABASE_SCOPE_SQL, database_scope.settings)
    return connection


def is_kb_available() -> bool:
    """知识库是否可用"""
    return _kb_available is True and settings.kb_enabled


# ===== DashScope Embedding =====


async def compute_embedding(text: str) -> Optional[List[float]]:
    """调用 DashScope text-embedding-v3 计算文本向量"""
    if not settings.dashscope_api_key:
        return None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=600.0, write=10.0, pool=5.0),
        ) as client:
            resp = await client.post(
                f"{settings.dashscope_base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.dashscope_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": EMBEDDING_MODEL,
                    "input": text[:2000],  # 截断过长文本
                    "dimensions": EMBEDDING_DIMS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning(f"Embedding compute failed | error={type(e).__name__}: {e or 'no detail'}")
        return None


# ===== 内容哈希 =====


def compute_content_hash(category: str, title: str, content: str) -> str:
    """计算知识条目的内容哈希（用于去重）"""
    raw = f"{category}|{title}|{content}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ===== TTL 缓存 =====


def get_cached_search(cache_key: str) -> Optional[List[Dict[str, Any]]]:
    """从缓存获取搜索结果，过期返回 None"""
    entry = _search_cache.get(cache_key)
    if entry and (time.monotonic() - entry["ts"]) < settings.kb_cache_ttl:
        return entry["data"]
    return None


def set_cached_search(cache_key: str, data: List[Dict[str, Any]]) -> None:
    """写入搜索缓存"""
    _search_cache[cache_key] = {"data": data, "ts": time.monotonic()}


def invalidate_search_cache() -> None:
    """清空搜索缓存（知识更新后调用）"""
    _search_cache.clear()


# ===== 格式化工具 =====


def format_knowledge_node(row: Dict[str, Any]) -> Dict[str, Any]:
    """格式化知识节点为前端/注入用结构"""
    return {
        "id": str(row.get("id", "")),
        "category": row.get("category", ""),
        "subcategory": row.get("subcategory"),
        "node_type": row.get("node_type", ""),
        "title": row.get("title", ""),
        "content": row.get("content", ""),
        "confidence": row.get("confidence", 0.5),
        "hit_count": row.get("hit_count", 0),
        "source": row.get("source", "auto"),
        "metadata": row.get("metadata", {}),
        "similarity": row.get("similarity", 0.0),
    }
