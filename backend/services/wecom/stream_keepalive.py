"""
企微 Stream 保活机制

企微长连接协议要求在处理期间定期发送 stream 更新以保持 req_id 活跃。
不发送保活会导致 req_id 失效，后续回复被静默丢弃。

保活间隔 2 秒，与真实处理阶段大致匹配：
  0s  正在理解你的问题...   ← 初始占位（_handle_text 直接发送）
  2s  正在回忆相关记忆...   ← Mem0 初始化中
  4s  正在思考回复...       ← Brain 调用中
  6s+ 正在生成回复...       ← 生成阶段
"""

import asyncio
from typing import Any, Callable, Coroutine

from loguru import logger

KEEPALIVE_INTERVAL = 2    # 保活间隔（秒），留余量防止事件循环延迟
KEEPALIVE_TIMEOUT = 120   # 安全超时上限（秒）

# 处理阶段进度文案（每次保活自动推进到下一阶段）
PROGRESS_STAGES = [
    "正在回忆相关记忆...",   # 2s  — Mem0 初始化
    "正在思考回复...",       # 4s  — Brain 调用
    "正在生成回复...",       # 6s+ — 生成阶段（循环使用）
]


class StreamKeepAlive:
    """Stream 保活：每 3 秒发送进度更新，防止企微 req_id 失效"""

    def __init__(
        self,
        reply_ctx: Any,
        push_fn: Callable[..., Coroutine],
    ) -> None:
        self._reply_ctx = reply_ctx
        self._push_fn = push_fn
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._stage_index = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _next_status(self) -> str:
        if self._stage_index < len(PROGRESS_STAGES):
            text = PROGRESS_STAGES[self._stage_index]
            self._stage_index += 1
            return text
        return PROGRESS_STAGES[-1]

    async def _loop(self) -> None:
        elapsed = 0
        try:
            while not self._stopped and elapsed < KEEPALIVE_TIMEOUT:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                elapsed += KEEPALIVE_INTERVAL
                if self._stopped:
                    break
                stream_id = self._reply_ctx.active_stream_id
                if not stream_id:
                    break
                status = self._next_status()
                await self._push_fn(
                    self._reply_ctx, stream_id, status, finish=False,
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Stream keepalive error: {e}")
