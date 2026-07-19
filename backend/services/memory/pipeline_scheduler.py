"""记忆管道调度器；新 revision 链路使用 Session Flush → Consolidation。"""

from __future__ import annotations

import uuid

from .config import get_memory_config
from loguru import logger

class PipelineScheduler:
    """只执行闭合 revision 的 Session Flush → Consolidation 调度器。"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()
        self._session_flush = None
        self._consolidator = None

    async def on_turn_committed(
        self,
        user_id: str,
        org_id: str,
        session_id: str,
        through_revision: int | None = None,
    ) -> None:
        """
        对话结束时调用。

        流程：
        1. 更新/创建管道状态
        2. conversation_count++
        3. 判断是否触发 L1
        """
        if through_revision is None:
            logger.warning(
                "Pipeline: memory flush rejected without closed revision | "
                "user_id={} | conversation_id={}",
                user_id,
                session_id,
            )
            return

        state = await self._get_or_create_state(user_id, org_id, session_id)
        # 更新计数
        state["conversation_count"] += 1
        await self._update_state(state)

        # 判断 L1 触发
        await self._maybe_trigger_l1(
            state,
            through_revision=through_revision,
        )

    async def _maybe_trigger_l1(
        self,
        state: dict,
        *,
        through_revision: int | None = None,
    ) -> None:
        """Warm-up 阈值判断"""
        cfg = self._cfg
        count = state["conversation_count"]
        threshold = state["warmup_threshold"]

        # Warm-up 模式：阈值从1开始指数增长
        if cfg.pipeline_enable_warmup and threshold > 0:
            if count >= threshold:
                await self._run_l1(
                    state,
                    through_revision=through_revision,
                )
                # 阈值翻倍，直到达到稳态
                new_threshold = min(threshold * 2, cfg.pipeline_every_n_conversations)
                if new_threshold >= cfg.pipeline_every_n_conversations:
                    new_threshold = 0  # 毕业，后续用固定阈值
                state["warmup_threshold"] = new_threshold
                state["conversation_count"] = 0
                await self._update_state(state)
                return

        # 稳态模式：每 N 轮触发
        if threshold == 0 and count >= cfg.pipeline_every_n_conversations:
            await self._run_l1(
                state,
                through_revision=through_revision,
            )
            state["conversation_count"] = 0
            await self._update_state(state)

    async def _run_l1(
        self,
        state: dict,
        *,
        through_revision: int | None = None,
    ) -> None:
        """执行通用 Session Flush；缺少闭合 revision 时拒绝执行。"""
        user_id = state["user_id"]
        org_id = state["org_id"]
        session_id = state["session_id"]

        if through_revision is None:
            logger.warning(
                "Pipeline: memory flush rejected without closed revision | "
                "user_id={} | conversation_id={}",
                user_id,
                session_id,
            )
            return

        from .session_flush import SessionFlushService

        if self._session_flush is None:
            self._session_flush = SessionFlushService(self._db)
        result = await self._session_flush.flush(
            user_id=user_id,
            org_id=org_id,
            conversation_id=session_id,
            through_revision=through_revision,
        )
        logger.info(
            "Pipeline: Session Flush completed | user_id={} | "
            "conversation_id={} | outcome={} | through_revision={}",
            user_id,
            session_id,
            result.outcome,
            result.through_revision,
        )
        if result.outcome != "committed":
            return

        from .consolidator import MemoryConsolidator

        if self._consolidator is None:
            self._consolidator = MemoryConsolidator(self._db)
        try:
            await self._consolidator.consolidate(
                user_id=user_id,
                org_id=org_id,
            )
        except Exception as exc:
            logger.error(
                "Pipeline: consolidation failed | user_id={} | "
                "conversation_id={} | error={}",
                user_id, session_id, exc,
            )

    # ============================
    # DB 状态管理
    # ============================

    async def _get_or_create_state(self, user_id: str, org_id: str, session_id: str) -> dict:
        """获取或创建管道状态"""
        try:
            row = await self._db.fetchrow(
                """SELECT * FROM memory_pipeline_state
                   WHERE org_id = $1 AND user_id = $2 AND session_id = $3""",
                uuid.UUID(org_id), uuid.UUID(user_id), uuid.UUID(session_id),
            )
            if row:
                return dict(row) | {
                    "user_id": user_id, "org_id": org_id, "session_id": session_id,
                }
        except Exception as e:
            logger.warning(f"Pipeline: failed to read state: {e}")

        # 新建
        state = {
            "user_id": user_id,
            "org_id": org_id,
            "session_id": session_id,
            "conversation_count": 0,
            "warmup_threshold": 1,
            "last_l1_at": None,
            "l1_cursor_timestamp": None,
            "l1_cursor_revision": 0,
        }

        try:
            await self._db.execute(
                """INSERT INTO memory_pipeline_state
                   (org_id, user_id, session_id, conversation_count, warmup_threshold)
                   VALUES ($1, $2, $3, 0, 1)
                   ON CONFLICT (org_id, user_id, session_id) DO NOTHING""",
                uuid.UUID(org_id), uuid.UUID(user_id), uuid.UUID(session_id),
            )
        except Exception as e:
            logger.warning(f"Pipeline: failed to create state: {e}")

        return state

    async def _update_state(self, state: dict) -> None:
        """持久化管道状态"""
        try:
            await self._db.execute(
                """UPDATE memory_pipeline_state SET
                       conversation_count = $4,
                       warmup_threshold = $5,
                       updated_at = NOW()
                   WHERE org_id = $1 AND user_id = $2 AND session_id = $3""",
                uuid.UUID(state["org_id"]),
                uuid.UUID(state["user_id"]),
                uuid.UUID(state["session_id"]),
                state.get("conversation_count", 0),
                state.get("warmup_threshold", 1),
            )
        except Exception as e:
            logger.warning(f"Pipeline: failed to update state: {e}")
