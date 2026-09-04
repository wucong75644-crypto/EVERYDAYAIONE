"""一次性 WebSocket 握手 ticket。

长期 access token 不应出现在 WebSocket URL 中。ticket 只在 Redis 中保存很短时间，
并且在首次消费时原子删除，即使被记录到 URL 日志中也不能作为长期凭证重放。
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Optional

from core.redis import get_redis

WS_TICKET_PREFIX = "auth:ws-ticket:"
WS_TICKET_TTL_SECONDS = 60


async def issue_ws_ticket(user_id: str, org_id: Optional[str] = None) -> str:
    """为已认证用户签发一次性 WebSocket ticket。"""
    redis = await get_redis()
    if redis is None:
        raise RuntimeError("Redis 不可用，无法创建 WebSocket ticket")

    ticket = secrets.token_urlsafe(32)
    value = json.dumps(
        {"user_id": str(user_id), "org_id": org_id},
        separators=(",", ":"),
    )
    stored = await redis.set(
        f"{WS_TICKET_PREFIX}{ticket}",
        value,
        ex=WS_TICKET_TTL_SECONDS,
        nx=True,
    )
    if not stored:
        raise RuntimeError("WebSocket ticket 创建失败")
    return ticket


async def consume_ws_ticket(ticket: str) -> Optional[dict[str, Any]]:
    """原子消费 WebSocket ticket，返回用户与组织上下文。"""
    if not ticket or len(ticket) > 256:
        return None

    redis = await get_redis()
    if redis is None:
        raise RuntimeError("Redis 不可用，无法验证 WebSocket ticket")

    value = await redis.getdel(f"{WS_TICKET_PREFIX}{ticket}")
    if not value:
        return None

    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None

    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    if not user_id:
        return None
    return {
        "user_id": str(user_id),
        "org_id": payload.get("org_id"),
    }
