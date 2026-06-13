"""
记忆系统 V2 统一 Facade

对外接口保持和旧 memory_service.py 兼容，内部转发到新模块。
支持 v1(Mem0)/v2(四层架构) 灰度切换。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .config import get_memory_config
from .l1_extractor import L1Extractor
from .l1_dedup import L1DedupService
from .retrieval_pipeline import RetrievalPipeline, ScoredMemory
from .pipeline_scheduler import PipelineScheduler
from .context_compressor import ContextCompressor

logger = logging.getLogger(__name__)

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
    def _convert_placeholders(sql: str) -> str:
        """将 $1, $2, ... 转为 %s"""
        import re
        return re.sub(r'\$\d+', '%s', sql)

    async def fetch(self, sql, *args):
        sql = self._convert_placeholders(sql)
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
        sql = self._convert_placeholders(sql)
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args if args else None)
            await conn.commit()


class MemoryServiceV2:
    """
    记忆系统 V2 统一入口

    对外方法与旧 MemoryService 兼容：
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
    # 检索（替代旧的 Mem0 搜索+千问精排）
    # ============================

    async def get_relevant_memories(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        org_id: str = "",
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

        # V2 阶段 4.1: 按 atom_id 排序保证字节稳定
        # 防止 RRF 分值微变导致顺序漂移, 漂移会破坏 prompt cache
        # 召回相关性已经在 search 阶段决定, 注入顺序不影响 LLM 行为
        scored_sorted = sorted(scored, key=lambda m: m.atom_id)
        return [
            {
                "id": m.atom_id,
                "memory": m.content,
                "metadata": {
                    "type": m.type,
                    "priority": m.priority,
                    "scene_name": m.scene_name,
                    "score": m.score,
                },
                "created_at": "",
                "updated_at": "",
            }
            for m in scored_sorted
        ]

    # ============================
    # 记忆注入（双部分架构）
    # ============================

    async def build_memory_context(
        self,
        user_id: str,
        org_id: str,
        query: str,
    ) -> tuple[str, str]:
        """
        构建记忆上下文（双部分注入）

        Returns:
            (prepend_context, append_system_context)
            - prepend_context: 动态 L1 记忆，注入 user prompt 前面
            - append_system_context: 稳定 L3 persona，注入 system prompt 末尾
        """
        await self._ensure_db()

        # 动态部分：L1 相关记忆
        scored = await self._retrieval.search(
            query=query, user_id=user_id, org_id=org_id,
        )
        prepend = self._retrieval.format_for_injection(scored)

        # 稳定部分：L3 persona
        append = await self._get_persona_context(user_id, org_id)

        return prepend, append

    async def _get_persona_context(self, user_id: str, org_id: str) -> str:
        """获取 persona 注入文本"""
        try:
            row = await self._db.fetchrow(
                "SELECT content FROM memory_personas WHERE org_id = $1 AND user_id = $2",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            if row and row["content"]:
                return f"<user-persona>\n{row['content']}\n</user-persona>"
        except Exception:
            pass
        return ""

    # ============================
    # 对话提取（替代旧的 Mem0 extract）
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

        scheduler = get_scheduler(db_pool=self._db)
        await scheduler.on_turn_committed(
            user_id=user_id,
            org_id=org_id,
            session_id=conversation_id,
            messages=messages,
        )

        return []  # 异步提取，不立即返回结果

    # ============================
    # CRUD（直接操作 memory_atoms）
    # ============================

    async def add_memory(
        self,
        user_id: str,
        content: str,
        source: str = "manual",
        org_id: str = "",
    ) -> list[dict]:
        """手动添加记忆"""
        from .l1_extractor import _insert_atom, MemoryAtom

        atom = MemoryAtom(
            content=content,
            type="persona",  # 手动添加默认为 persona 类型
            priority=70,
        )
        atom_id = await _insert_atom(self._db, atom, user_id, org_id, "")
        if atom_id:
            return [{"id": atom_id, "memory": content, "metadata": {"source": source}}]
        return []

    async def get_all_memories(
        self,
        user_id: str,
        org_id: str = "",
    ) -> list[dict]:
        """获取用户所有记忆"""
        try:
            rows = await self._db.fetch(
                """SELECT id::text, content, type, priority, scene_name,
                          created_at::text, updated_at::text
                   FROM memory_atoms
                   WHERE org_id = $1 AND user_id = $2 AND NOT is_deleted
                   ORDER BY updated_at DESC""",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return [
                {
                    "id": r["id"],
                    "memory": r["content"],
                    "metadata": {
                        "type": r["type"],
                        "priority": r["priority"],
                        "scene_name": r["scene_name"],
                    },
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"MemoryV2: get_all failed: {e}")
            return []

    async def delete_memory(
        self,
        memory_id: str,
        user_id: str,
        org_id: str = "",
    ) -> None:
        """删除单条记忆"""
        try:
            await self._db.execute(
                "UPDATE memory_atoms SET is_deleted = TRUE, updated_at = NOW() WHERE id = $1 AND user_id = $2",
                uuid.UUID(memory_id), uuid.UUID(user_id),
            )
        except Exception as e:
            logger.error(f"MemoryV2: delete failed: {e}")

    async def delete_all_memories(
        self,
        user_id: str,
        org_id: str = "",
    ) -> None:
        """删除用户所有记忆"""
        try:
            await self._db.execute(
                "UPDATE memory_atoms SET is_deleted = TRUE, updated_at = NOW() WHERE org_id = $1 AND user_id = $2",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
        except Exception as e:
            logger.error(f"MemoryV2: delete_all failed: {e}")

    async def get_memory_count(
        self,
        user_id: str,
        org_id: str = "",
    ) -> int:
        """获取记忆总数"""
        try:
            row = await self._db.fetchrow(
                "SELECT COUNT(*) as cnt FROM memory_atoms WHERE org_id = $1 AND user_id = $2 AND NOT is_deleted",
                uuid.UUID(org_id), uuid.UUID(user_id),
            )
            return row["cnt"] if row else 0
        except Exception:
            return 0

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
