"""
记忆系统 V2 统一 Facade

通用 Curated Memory 的 Search/Get、Prompt 注入与调度 Facade。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .config import get_memory_config
from .retrieval_pipeline import RetrievalPipeline, ScoredMemory
from .pipeline_scheduler import PipelineScheduler
from .context_compressor import ContextCompressor
from loguru import logger



# 全局调度器单例
_scheduler: PipelineScheduler | None = None


async def get_scheduler(db_pool=None) -> PipelineScheduler:
    """获取全局管道调度器（自动适配 DB 连接）"""
    global _scheduler
    if _scheduler is None:
        pool = await _get_memory_db()
        adapted = _PsycopgAdapter(pool) if pool else db_pool
        _scheduler = PipelineScheduler(db_pool=adapted)
    return _scheduler


async def _get_memory_db():
    """获取记忆系统的 psycopg 异步连接池（复用 knowledge_config 的池）"""
    from services.knowledge_config import _get_pg_pool
    return await _get_pg_pool()


class _PsycopgAdapter:
    """适配 psycopg AsyncConnectionPool 为 memory 模块使用的接口

    memory 模块 SQL 使用 asyncpg 风格的 $1, $2... 占位符。
    psycopg 使用 %s 占位符。这里自动转换。
    """

    def __init__(self, pool):
        self._pool = pool

    @staticmethod
    def _convert_placeholders(sql: str, args: tuple) -> tuple[str, tuple]:
        """asyncpg $N → psycopg %s，按 $N 在 SQL 中出现顺序重排 args。

        asyncpg 按编号绑定，$N 可乱序/复用（如 SET 用 $4..$13、WHERE 用 $1..$3）。
        psycopg %s 按出现位置绑定，若不重排 args 会让 SET/WHERE 字段错位
        （历史 bug：'operator does not exist: uuid = boolean'）。
        """
        import re
        order: list[int] = []

        def replace(m):
            order.append(int(m.group(1)) - 1)
            return '%s'

        new_sql = re.sub(r'\$(\d+)', replace, sql)
        new_args = tuple(args[i] for i in order)
        return new_sql, new_args

    async def fetch(self, sql, *args):
        sql, args = self._convert_placeholders(sql, args)
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args if args else None)
                cols = [desc[0] for desc in cur.description] if cur.description else []
                rows = await cur.fetchall()
                return [dict(zip(cols, row)) for row in rows]

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def execute(self, sql, *args):
        sql, args = self._convert_placeholders(sql, args)
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args if args else None)
            await conn.commit()


class MemoryServiceV2:
    """
    记忆系统 V2 统一入口

    通用记忆运行时入口：
    - get_relevant_memories() → 检索
    - extract_memories_from_conversation() → 提取
    - add_memory() / delete_memory() → CRUD
    - get_all_memories() → 列表
    """

    def __init__(self, db_pool=None):
        self._raw_pool = db_pool  # 可能是 Supabase client（忽略）
        self._db = None  # 延迟初始化
        self._cfg = get_memory_config()
        self._retrieval = None
        self._compressor = ContextCompressor()

    async def _ensure_db(self):
        """确保 DB 适配器已初始化"""
        if self._db is None:
            pool = await _get_memory_db()
            if pool:
                self._db = _PsycopgAdapter(pool)
                self._retrieval = RetrievalPipeline(db_pool=self._db)
            else:
                raise RuntimeError("Memory V2: database pool not available")
        return self._db

    # ============================
    # Curated Memory 检索
    # ============================

    async def get_relevant_memories(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        org_id: str | None = None,
    ) -> list[dict]:
        """
        检索相关记忆（兼容旧接口）

        旧接口返回 [{id, memory, metadata, created_at, updated_at}]

        """
        if not query or not self._cfg.enabled:
            return []

        await self._ensure_db()
        scored = await self._retrieval.search(
            query=query,
            user_id=user_id,
            org_id=org_id,
            max_results=limit,
        )

        return [
            {
                "id": m.atom_id,
                "memory": m.content,
                "metadata": {
                    "kind": m.kind,
                    "priority": m.priority,
                    "score": m.score,
                },
                "created_at": "",
                "updated_at": "",
            }
            for m in scored
        ]

    async def get_memory(
        self,
        user_id: str,
        memory_id: str,
        org_id: str | None = None,
    ) -> dict[str, Any] | None:
        """严格读取一条当前可用且属于该用户和组织的 Curated Memory。"""
        if not memory_id or not user_id or not self._cfg.enabled:
            return None
        await self._ensure_db()
        memory = await self._retrieval.get(
            atom_id=memory_id,
            user_id=user_id,
            org_id=org_id,
        )
        if memory is None:
            return None
        return {
            "id": memory.atom_id,
            "memory": memory.content,
            "metadata": {
                "kind": memory.kind,
                "priority": memory.priority,
                "score": memory.score,
                "valid_from": memory.valid_from,
                "valid_until": memory.valid_until,
                "source_message_ids": list(memory.source_message_ids),
            },
        }

    # ============================
    # 记忆注入（双部分架构）
    # ============================

    async def build_memory_context(
        self,
        user_id: str,
        org_id: str | None,
        query: str,
    ) -> tuple[str, str]:
        """
        构建 Curated Memory 上下文。

        Returns:
            (curated_memory_context, legacy_persona_context)
            legacy_persona_context 固定为空，保留元组形状兼容调用方。
        """
        await self._ensure_db()

        # 动态部分：L1 相关记忆
        scored = await self._retrieval.search(
            query=query, user_id=user_id, org_id=org_id, max_results=3,
        )

        prepend = self._retrieval.format_for_injection(scored)

        shadow_claims = await self.get_session_memory_shadow(user_id)
        shadow_overlap = sum(
            claim in (prepend or "") for claim in shadow_claims
        )
        logger.info(
            "Session Memory shadow compared | user_id={} | "
            "session_count={} | legacy_exact_overlap={}",
            user_id,
            len(shadow_claims),
            shadow_overlap,
        )
        return prepend, ""

    async def get_session_memory_shadow(
        self,
        user_id: str,
        limit: int = 25,
    ) -> list[str]:
        """只读最近 Session Memory 候选；调用方不得将其注入模型。"""
        await self._ensure_db()
        try:
            rows = await self._db.fetch(
                """SELECT content
                   FROM memory_session_logs
                   WHERE user_id = $1::uuid AND status = 'ready'
                   ORDER BY created_at DESC
                   LIMIT $2""",
                user_id,
                limit,
            )
        except Exception as exc:
            logger.warning(
                "Session Memory shadow read failed | user_id={} | "
                "error_type={}",
                user_id,
                type(exc).__name__,
            )
            return []
        claims: list[str] = []
        for row in rows:
            payload = row.get("content")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            items = payload.get("items", []) if isinstance(payload, dict) else []
            claims.extend(
                str(item["claim"])
                for item in items
                if isinstance(item, dict) and item.get("claim")
            )
        return claims[:50]

    # ============================
    # 对话提取调度
    # ============================

    async def extract_memories_from_conversation(
        self,
        user_id: str,
        messages: list[dict],
        conversation_id: str = "",
        org_id: str = "",
    ) -> list[dict]:
        """
        从对话中提取记忆（通过管道调度器触发）

        兼容旧接口：返回提取的记忆列表
        """
        if not messages or not self._cfg.enabled:
            return []

        scheduler = await get_scheduler(db_pool=self._db)
        await scheduler.on_turn_committed(
            user_id=user_id,
            org_id=org_id,
            session_id=conversation_id,
        )

        return []  # 异步提取，不立即返回结果

    # ============================
    # 上下文压缩
    # ============================

    async def compress_context(
        self,
        messages: list[dict],
        context_window: int | None = None,
    ) -> list[dict]:
        """对消息列表执行上下文压缩"""
        return await self._compressor.compress_if_needed(messages, context_window)

    # ============================
    # 画像和场景查询（新接口）
    # ============================

    async def get_persona(self, user_id: str, org_id: str) -> dict | None:
        """获取用户画像"""
        try:
            row = await self._db.fetchrow(
                """SELECT content, archetype, version, trigger_reason,
                          total_atoms_processed, total_scenes,
                          created_at::text, updated_at::text
                   FROM memory_personas
                   WHERE org_id = $1 AND user_id = $2""",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return dict(row) if row else None
        except Exception:
            return None

    async def get_scenes(self, user_id: str, org_id: str) -> list[dict]:
        """获取用户的活跃场景列表"""
        try:
            rows = await self._db.fetch(
                """SELECT id::text, title, summary, heat,
                          created_at::text, updated_at::text
                   FROM memory_scenes
                   WHERE org_id = $1 AND user_id = $2 AND status = 'active'
                   ORDER BY heat DESC""",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return [dict(r) for r in rows]
        except Exception:
            return []
