"""
管道调度器：L1→L2→L3 触发链

移植自腾讯 TencentDB-Agent-Memory pipeline-manager.ts

核心机制：
- Warm-up：新会话首条消息立即触发 L1，阈值指数增长（1→2→4→...→N）
- L2 Timer：downward-only 语义，L1完成后延迟触发，有最小间隔
- L3 全局互斥：同一用户同时只运行一个 L3
- 冷会话跳过：>24h 无活动的会话停止 L2 轮询
- DB 持久化：进程重启后恢复状态
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone, timedelta

from .config import get_memory_config
from loguru import logger




class PipelineScheduler:
    """L1→L2→L3 管道调度器"""

    def __init__(self, db_pool=None):
        self._db = db_pool
        self._cfg = get_memory_config()
        self._l2_timers: dict[str, asyncio.Task] = {}
        self._l3_lock = asyncio.Lock()
        self._l3_pending = False

    # ============================
    # 对外入口：对话结束回调
    # ============================

    async def on_turn_committed(
        self,
        user_id: str,
        org_id: str,
        session_id: str,
        messages: list[dict],
    ) -> None:
        """
        对话结束时调用。

        流程：
        1. 更新/创建管道状态
        2. conversation_count++
        3. 判断是否触发 L1
        """
        state = await self._get_or_create_state(user_id, org_id, session_id)

        # 更新计数
        state["conversation_count"] += 1
        await self._update_state(state)

        # 判断 L1 触发
        await self._maybe_trigger_l1(state, messages)

    # ============================
    # L1 触发判断
    # ============================

    async def _maybe_trigger_l1(self, state: dict, messages: list[dict]) -> None:
        """Warm-up 阈值判断"""
        cfg = self._cfg
        count = state["conversation_count"]
        threshold = state["warmup_threshold"]

        # Warm-up 模式：阈值从1开始指数增长
        if cfg.pipeline_enable_warmup and threshold > 0:
            if count >= threshold:
                await self._run_l1(state, messages)
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
            await self._run_l1(state, messages)
            state["conversation_count"] = 0
            await self._update_state(state)

    @staticmethod
    def _should_extract(messages: list[dict]) -> bool:
        """V2 阶段 6.4: 抽取前置门禁

        判断这批对话是否值得调 LLM 抽取记忆.

        业界共识 (ChatGPT/mem0 都用前置 classifier):
          - 短消息/寒暄 → 跳过 (60-70% 对话其实无新事实)
          - 业务关键词/数字 → 抽取
          - 减少 LLM 调用成本 + 提升抽取质量 (避免抽取"嗯/好/谢谢"这种空轮)

        返回 True 才走 LLM 抽取.
        """
        import re

        # 业务关键词 (跟 EVERYDAYAIONE 业务相关的)
        BUSINESS_KEYWORDS = (
            "公司", "我们", "我的", "退款率", "退款", "销售", "利润", "毛利",
            "SKU", "订单", "客单价", "红线", "成本", "采购", "库存", "供应商",
            "平台", "京东", "淘宝", "拼多多", "抖音", "快手", "小红书",
            "偏好", "习惯", "通常", "总是", "经常", "重要", "关注",
            "目标", "计划", "策略", "标准", "规则", "要求",
        )

        # 数字/百分比正则
        NUMBER_RE = re.compile(r'\d+%|\d{2,}')

        # 至少有一条 user 消息满足条件
        for m in messages:
            if m.get("role") != "user":
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                # 多模态: 提取 text 部分
                content = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            text = str(content).strip()

            # 太短 → skip
            if len(text) < 10:
                continue
            # 业务关键词触发
            if any(kw in text for kw in BUSINESS_KEYWORDS):
                return True
            # 数字/百分比触发
            if NUMBER_RE.search(text):
                return True
            # 长消息 (>50 字) 兜底, 可能含价值
            if len(text) > 50:
                return True

        return False

    async def _run_l1(self, state: dict, messages: list[dict]) -> None:
        """执行 L1 提取（fire-and-forget）

        V2 阶段 6.4: 前置门禁
        """
        user_id = state["user_id"]
        org_id = state["org_id"]
        session_id = state["session_id"]

        # 抽取前置门禁: 判断这批对话是否值得调 LLM
        if not self._should_extract(messages):
            logger.info(
                f"Pipeline L1 SKIP (no business value) | "
                f"user={user_id[:8]}... session={session_id[:8]}... | "
                f"msgs={len(messages)}"
            )
            return

        logger.info(f"Pipeline: triggering L1 for user={user_id[:8]}... session={session_id[:8]}...")

        try:
            from .l1_extractor import L1Extractor
            extractor = L1Extractor(db_pool=self._db)
            result = await extractor.extract(
                messages=messages,
                session_id=session_id,
                user_id=user_id,
                org_id=org_id,
                previous_scene_name=state.get("last_scene_name", ""),
            )

            if result.success and result.stored_count > 0:
                # 更新状态
                state["last_l1_at"] = datetime.now(timezone.utc).isoformat()
                state["last_scene_name"] = result.last_scene_name or state.get("last_scene_name", "")
                state["atoms_since_last_persona"] = (
                    state.get("atoms_since_last_persona", 0) + result.stored_count
                )
                await self._update_state(state)

                # 调度 L2
                await self._schedule_l2(state)

                logger.info(f"Pipeline: L1 done, stored {result.stored_count} atoms")

        except Exception as e:
            logger.error(f"Pipeline: L1 failed: {e}")

    # ============================
    # L2 调度（downward-only timer）
    # ============================

    async def _schedule_l2(self, state: dict) -> None:
        """
        L1 完成后调度 L2。

        Timer 语义：downward-only
        - fire_time = max(now + delay, last_l2 + min_interval)
        - 只下移不后延
        """
        cfg = self._cfg
        now = datetime.now(timezone.utc)

        # 计算 fire_time
        fire_after_delay = now + timedelta(seconds=cfg.pipeline_l2_delay_after_l1)

        last_l2 = state.get("last_l2_at")
        if last_l2:
            if isinstance(last_l2, str):
                last_l2 = datetime.fromisoformat(last_l2.replace("Z", "+00:00"))
            fire_after_min_interval = last_l2 + timedelta(seconds=cfg.pipeline_l2_min_interval)
            fire_time = max(fire_after_delay, fire_after_min_interval)
        else:
            fire_time = fire_after_delay

        # 保存 fire_time
        state["l2_fire_time"] = fire_time.isoformat()
        await self._update_state(state)

        # 设置 timer
        delay_seconds = max(0, (fire_time - now).total_seconds())
        timer_key = f"{state['user_id']}:{state['session_id']}"

        # 取消旧 timer（downward-only：新 fire_time 更早才替换）
        old_timer = self._l2_timers.get(timer_key)
        if old_timer and not old_timer.done():
            # 只下移不后延：如果新 fire_time 更晚，保持旧 timer
            old_fire = state.get("_old_l2_fire_time")
            if old_fire and fire_time > datetime.fromisoformat(old_fire):
                return
            old_timer.cancel()

        state["_old_l2_fire_time"] = fire_time.isoformat()
        self._l2_timers[timer_key] = asyncio.create_task(
            self._l2_timer_fire(state, delay_seconds)
        )

        logger.debug(f"Pipeline: L2 scheduled in {delay_seconds:.0f}s")

    async def _l2_timer_fire(self, state: dict, delay: float) -> None:
        """L2 timer 到期"""
        try:
            await asyncio.sleep(delay)

            # 冷会话检查
            if self._is_cold_session(state):
                logger.debug(f"Pipeline: cold session, skipping L2")
                return

            await self._run_l2(state)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Pipeline: L2 timer error: {e}")

    async def _run_l2(self, state: dict) -> None:
        """执行 L2 场景提取"""
        user_id = state["user_id"]
        org_id = state["org_id"]

        logger.info(f"Pipeline: triggering L2 for user={user_id[:8]}...")

        try:
            from .l2_scene_manager import L2SceneManager
            manager = L2SceneManager(db_pool=self._db)
            result = await manager.extract_scenes(
                user_id=user_id,
                org_id=org_id,
            )

            state["last_l2_at"] = datetime.now(timezone.utc).isoformat()
            await self._update_state(state)

            if result.get("success"):
                logger.info(f"Pipeline: L2 done, processed {result.get('scenes_updated', 0)} scenes")
                # 触发 L3 判断
                await self._maybe_trigger_l3(state)

        except Exception as e:
            logger.error(f"Pipeline: L2 failed: {e}")

    # ============================
    # L3 触发判断（全局互斥）
    # ============================

    async def _maybe_trigger_l3(self, state: dict) -> None:
        """L2 完成后判断是否触发 L3"""
        cfg = self._cfg
        atoms_since = state.get("atoms_since_last_persona", 0)

        # 触发条件
        should_trigger = False
        reason = ""

        # P1: 主动请求
        if state.get("request_persona_update"):
            should_trigger = True
            reason = f"主动请求: {state.get('persona_update_reason', 'Agent请求')}"
            state["request_persona_update"] = False
            state["persona_update_reason"] = None

        # P2: 冷启动（无 persona 且有 atoms）
        elif not state.get("last_persona_at") and atoms_since > 0:
            should_trigger = True
            reason = "首次冷启动"

        # P3: 达到阈值
        elif atoms_since >= cfg.l3_trigger_every_n:
            should_trigger = True
            reason = f"达到阈值: {atoms_since} >= {cfg.l3_trigger_every_n}"

        if not should_trigger:
            return

        # 全局互斥
        if self._l3_lock.locked():
            self._l3_pending = True
            logger.debug("Pipeline: L3 already running, marked pending")
            return

        async with self._l3_lock:
            await self._run_l3(state, reason)
            # 完成后检查 pending
            if self._l3_pending:
                self._l3_pending = False
                await self._run_l3(state, "pending from previous L2")

    async def _run_l3(self, state: dict, reason: str) -> None:
        """执行 L3 画像生成"""
        user_id = state["user_id"]
        org_id = state["org_id"]

        logger.info(f"Pipeline: triggering L3 for user={user_id[:8]}... reason={reason}")

        try:
            from .l3_persona_generator import L3PersonaGenerator
            generator = L3PersonaGenerator(db_pool=self._db)
            success = await generator.generate(
                user_id=user_id,
                org_id=org_id,
                trigger_reason=reason,
            )

            if success:
                state["last_persona_at"] = datetime.now(timezone.utc).isoformat()
                state["atoms_since_last_persona"] = 0
                await self._update_state(state)
                logger.info("Pipeline: L3 persona generated")

        except Exception as e:
            logger.error(f"Pipeline: L3 failed: {e}")

    # ============================
    # 工具函数
    # ============================

    def _is_cold_session(self, state: dict) -> bool:
        """冷会话判定：>24h 无 L1 活动"""
        last_l1 = state.get("last_l1_at")
        if not last_l1:
            return False
        if isinstance(last_l1, str):
            last_l1 = datetime.fromisoformat(last_l1.replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - last_l1).total_seconds() / 3600
        return hours > self._cfg.pipeline_session_active_hours

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
            "last_scene_name": None,
            "last_l2_at": None,
            "l2_fire_time": None,
            "atoms_since_last_persona": 0,
            "last_persona_at": None,
            "request_persona_update": False,
            "persona_update_reason": None,
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
                       last_l1_at = $6,
                       last_scene_name = $7,
                       last_l2_at = $8,
                       l2_fire_time = $9,
                       atoms_since_last_persona = $10,
                       last_persona_at = $11,
                       request_persona_update = $12,
                       persona_update_reason = $13,
                       updated_at = NOW()
                   WHERE org_id = $1 AND user_id = $2 AND session_id = $3""",
                uuid.UUID(state["org_id"]),
                uuid.UUID(state["user_id"]),
                uuid.UUID(state["session_id"]),
                state.get("conversation_count", 0),
                state.get("warmup_threshold", 1),
                state.get("last_l1_at"),
                state.get("last_scene_name"),
                state.get("last_l2_at"),
                state.get("l2_fire_time"),
                state.get("atoms_since_last_persona", 0),
                state.get("last_persona_at"),
                state.get("request_persona_update", False),
                state.get("persona_update_reason"),
            )
        except Exception as e:
            logger.warning(f"Pipeline: failed to update state: {e}")
