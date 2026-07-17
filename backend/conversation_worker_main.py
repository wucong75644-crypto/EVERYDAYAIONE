"""Conversation Actor 独立 Worker 进程入口。"""

from __future__ import annotations

import asyncio
import os
import signal

from loguru import logger

from core.config import get_settings
from core.logging_config import setup_logging


async def _run() -> None:
    settings = get_settings()
    if not settings.conversation_actor_worker_enabled:
        raise RuntimeError(
            "CONVERSATION_ACTOR_WORKER_ENABLED=false，拒绝启动 Actor Worker"
        )

    from core.database import close_async_db, get_async_db
    from core.redis import RedisClient
    from services.conversation_runtime import (
        ConversationActorRuntime,
        create_kernel_manager,
    )
    from services.websocket_manager import ws_manager

    db = await get_async_db()
    runtime = ConversationActorRuntime(db, ws_manager, create_kernel_manager())
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info(f"Conversation Actor Worker starting | pid={os.getpid()}")
    try:
        await runtime.start()
        await shutdown.wait()
    finally:
        await runtime.stop()
        await close_async_db()
        await RedisClient.close()
        logger.info("Conversation Actor Worker stopped")


def main() -> None:
    setup_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
