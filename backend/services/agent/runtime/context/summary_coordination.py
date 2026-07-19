"""跨 Worker 对话摘要的 Redis single-flight 与失败抑制。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from loguru import logger


_LOCK_TTL_SECONDS = 60
_FAILURE_TTL_SECONDS = 300


@dataclass(frozen=True)
class SummaryCoordination:
    """一次跨 Turn 摘要协调结果。"""

    outcome: str
    lock_key: str
    lock_token: str | None = None
    suppression_key: str = ""

    @property
    def should_run(self) -> bool:
        """Redis 成功加锁或不可用降级时允许继续。"""
        return self.outcome in {"acquired", "degraded"}


def summary_prefix_fingerprint(
    conversation_id: str,
    summary_revision: int,
    through_revision: int,
) -> str:
    """生成不暴露会话 ID 的稳定摘要前缀指纹。"""
    encoded = json.dumps(
        [conversation_id, summary_revision, through_revision],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def acquire_summary_coordination(
    conversation_id: str,
    summary_revision: int,
    through_revision: int,
) -> SummaryCoordination:
    """尝试获取摘要锁；Redis 故障时降级到数据库 CAS。"""
    fingerprint = summary_prefix_fingerprint(
        conversation_id,
        summary_revision,
        through_revision,
    )
    lock_key = f"context-summary:{fingerprint}"
    suppression_key = f"context:summary:suppressed:{fingerprint}"
    try:
        from core.redis import RedisClient, get_redis

        redis = await get_redis()
        if redis is None:
            return SummaryCoordination("degraded", lock_key)
        if await redis.exists(suppression_key):
            return SummaryCoordination(
                "suppressed",
                lock_key,
                suppression_key=suppression_key,
            )
        token = await RedisClient.acquire_lock(
            lock_key,
            timeout=_LOCK_TTL_SECONDS,
        )
        if not token:
            return SummaryCoordination("in_flight", lock_key)
        return SummaryCoordination(
            "acquired",
            lock_key,
            lock_token=token,
            suppression_key=suppression_key,
        )
    except Exception as error:
        logger.warning(
            f"Context summary coordination degraded | error={error}"
        )
        return SummaryCoordination("degraded", lock_key)


async def finish_summary_coordination(
    coordination: SummaryCoordination,
    *,
    failed: bool,
) -> None:
    """记录模型失败并按 token 所有权释放分布式锁。"""
    if coordination.outcome != "acquired" or not coordination.lock_token:
        return
    try:
        if failed:
            from core.redis import get_redis

            redis = await get_redis()
            if redis is not None:
                await redis.set(
                    coordination.suppression_key,
                    "1",
                    ex=_FAILURE_TTL_SECONDS,
                )
    except Exception as error:
        logger.warning(
            f"Context summary suppression write failed | error={error}"
        )
    finally:
        try:
            from core.redis import RedisClient

            await RedisClient.release_lock(
                coordination.lock_key,
                coordination.lock_token,
            )
        except Exception as error:
            logger.warning(
                f"Context summary lock release failed | error={error}"
            )
