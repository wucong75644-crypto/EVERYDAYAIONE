"""消息生成幂等记录的 TTL 清理循环。"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


_CLEANUP_INTERVAL_SECONDS = 3600


async def message_idempotency_cleanup_loop(db: Any) -> None:
    """每小时清理过期记录；失败不影响 API 主链路。"""
    logger.info("Message idempotency cleanup loop started | retention=24h")
    while True:
        try:
            result = await db.rpc(
                "cleanup_expired_message_generation_requests",
                {},
            ).execute()
            deleted = int(result.data or 0)
            if deleted:
                logger.info(
                    f"Message idempotency cleanup done | deleted={deleted}"
                )
        except asyncio.CancelledError:
            logger.info("Message idempotency cleanup loop stopped")
            return
        except Exception as error:
            logger.warning(
                "Message idempotency cleanup failed | "
                f"error={type(error).__name__}"
            )
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("Message idempotency cleanup loop stopped")
            return
