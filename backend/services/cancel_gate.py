"""用户取消机制（Phase 1）

设计参考 docs/document/TECH_用户中断与恢复机制.md §13.2 / §四.5

整合两类机制：
1. asyncio.Event 信号：供 Agent 循环内轮询取消（chat_handler.py）
2. Cancelled gate 集合：供 WS 推送入口闸门检查 drop（根治"工具鬼显"）

多租户：gate 用 (org_id, task_id) 复合 key 隔离，跨 org 互不影响。
TTL：默认 30 分钟自动清理，避免长时间占内存。
"""

import asyncio
import time
from typing import Dict, Tuple

from loguru import logger


CANCELLED_GATE_TTL = 1800


class CancelManager:
    """统一管理 cancel 信号（asyncio.Event）+ WS 闸门（gate 集合）。"""

    def __init__(self):
        self._signals: Dict[str, asyncio.Event] = {}
        self._gates: Dict[Tuple[str, str], float] = {}
        self._gates_lock = asyncio.Lock()

    def register_listener(self, task_id: str) -> None:
        """注册取消监听（工具循环开始时调用）"""
        self._signals[task_id] = asyncio.Event()

    def is_signalled(self, task_id: str) -> bool:
        """非阻塞检查 asyncio.Event 是否触发"""
        event = self._signals.get(task_id)
        return bool(event and event.is_set())

    def unregister_listener(self, task_id: str) -> None:
        """清理监听"""
        self._signals.pop(task_id, None)

    def cancel(self, task_id: str, org_id: str | None = None) -> bool:
        """触发取消：set Event + mark gate（双轨）。

        Returns:
            True = 找到运行中的任务并 set 了 Event
        """
        # 闸门必须在同步入口立即生效。若把 mark_gate 仅作为后台 task，
        # 取消请求返回到旧 runtime 推送之间会存在一个事件循环窗口，
        # 旧 chunk 可能先被本地或 Redis 发布出去。
        self._set_gate(task_id, org_id)

        event = self._signals.get(task_id)
        if event:
            event.set()
            logger.info(f"Cancel signal sent | task_id={task_id} | org={org_id}")
            return True
        logger.warning(
            f"Cancel signal miss (task not running) | "
            f"task_id={task_id} | org={org_id}"
        )
        return False

    def _set_gate(self, task_id: str, org_id: str | None = None) -> None:
        """在当前事件循环中立即写入闸门。

        `_gates` 只由同一 Worker 的事件循环访问；异步批量清理仍通过
        `_gates_lock` 保护。取消入口不能等待异步锁，否则会重新引入竞态。
        """
        key = (org_id or "", task_id)
        self._gates[key] = time.time() + CANCELLED_GATE_TTL
        logger.info(
            f"Cancelled gate set | task={task_id} | org={org_id} | "
            f"expires_in={CANCELLED_GATE_TTL}s"
        )

    async def mark_gate(
        self, task_id: str, org_id: str | None = None,
    ) -> None:
        """标记任务已取消，TTL 内 WS 推送一律 drop。"""
        async with self._gates_lock:
            self._set_gate(task_id, org_id)

    def is_in_gate(
        self, task_id: str, org_id: str | None = None,
    ) -> bool:
        """非阻塞检查闸门集合。已过期视为不在集合。

        匹配逻辑：
        - 精确匹配 (org_id, task_id) 复合 key (O(1))
        - 向后兼容：org_id=None 时，扫描所有 org 看 task_id 是否匹配 (O(n))

        性能：n=cancelled 任务数，TTL 30 分钟 + 单 org 1h<50 次告警限流，
        实际 n ≤ 50。每次推送的 O(50) lookup <0.1ms 可接受。
        v2 优化：用 dict[task_id, list[org_id]] 改 O(1)，或调用方传 org_id 走精确匹配
        """
        now = time.time()
        key = (org_id or "", task_id)

        expire_at = self._gates.get(key)
        if expire_at is not None and expire_at > now:
            return True

        if org_id is None:
            for (gate_org, gate_task), exp in self._gates.items():
                if gate_task == task_id and exp > now:
                    return True

        return False

    async def cleanup_gates(self) -> int:
        """清理过期闸门项（由定期任务调用）。"""
        now = time.time()
        async with self._gates_lock:
            expired = [k for k, exp in self._gates.items() if exp <= now]
            for k in expired:
                del self._gates[k]
        if expired:
            logger.debug(f"Cancelled gates cleaned | count={len(expired)}")
        return len(expired)

    async def clear_gate(
        self, task_id: str, org_id: str | None = None,
    ) -> None:
        """为同一 task 的合法 RESUME 打开新的 WS 投递窗口。"""
        key = (org_id or "", task_id)
        async with self._gates_lock:
            self._gates.pop(key, None)
