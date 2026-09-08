"""单进程、单事件循环内的模型请求准入；不参与 Actor 任务调度。"""

from __future__ import annotations

import asyncio


class ModelConcurrencyLease:
    """槽位可以由 stream finally 或服务关闭释放，但只归还一次。"""

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._semaphore.release()


class ModelConcurrencyGate:
    def __init__(self, max_concurrency: int | None = None) -> None:
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("model_gateway_max_concurrency must be positive")
        self._limit = max_concurrency
        self._semaphore: asyncio.Semaphore | None = None

    async def acquire(self) -> ModelConcurrencyLease:
        if self._semaphore is None:
            limit = self._limit
            if limit is None:
                from core.config import get_settings

                limit = get_settings().model_gateway_max_concurrency
            self._semaphore = asyncio.Semaphore(limit)
        # asyncio.Semaphore 的取消处理归还尚未交付的槽位；成功返回后的
        # 所有权属于 lease，调用者必须处理 acquire 与 cancel 同时完成的情况。
        await self._semaphore.acquire()
        return ModelConcurrencyLease(self._semaphore)
