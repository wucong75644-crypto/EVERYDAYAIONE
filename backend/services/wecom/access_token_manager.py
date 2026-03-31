"""
企业微信自建应用 access_token 管理（per-org 版）

- 每个企业独立的 corp_id + agent_secret → 独立的 token
- Redis 缓存：wecom:access_token:{org_id}（提前 5 分钟刷新）
- 失败重试 3 次
- API 文档：https://developer.work.weixin.qq.com/document/path/91039
"""

from typing import Optional

import httpx
from loguru import logger

from core.redis import get_redis

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
REFRESH_MARGIN = 300  # 提前 5 分钟刷新


def _redis_key(org_id: str) -> str:
    return f"wecom:access_token:{org_id}"


async def get_access_token(
    org_id: str,
    corp_id: str,
    agent_secret: str,
) -> Optional[str]:
    """
    获取指定企业的自建应用 access_token（优先从 Redis 缓存读取）。

    Args:
        org_id: 企业 ID（用作 Redis 缓存 key）
        corp_id: 企微企业 ID（corpid）
        agent_secret: 自建应用 Secret

    Returns:
        access_token 字符串，失败返回 None
    """
    # 1. 尝试从 Redis 读取
    key = _redis_key(org_id)
    redis = await get_redis()
    if redis:
        try:
            cached = await redis.get(key)
            if cached:
                return cached
        except Exception as e:
            logger.warning(f"Wecom token: Redis read failed | org_id={org_id} | error={e}")

    # 2. 缓存未命中，从 API 获取
    return await _fetch_and_cache_token(org_id, corp_id, agent_secret)


async def _fetch_and_cache_token(
    org_id: str,
    corp_id: str,
    agent_secret: str,
    retries: int = 3,
) -> Optional[str]:
    """从企微 API 获取 token 并缓存到 Redis"""
    if not corp_id or not agent_secret:
        logger.error(f"Wecom token: corp_id or agent_secret not configured | org_id={org_id}")
        return None

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    TOKEN_URL,
                    params={"corpid": corp_id, "corpsecret": agent_secret},
                )
                data = resp.json()

            errcode = data.get("errcode", -1)
            if errcode != 0:
                errmsg = data.get("errmsg", "unknown")
                logger.warning(
                    f"Wecom token: API error | org_id={org_id} | attempt={attempt} | "
                    f"errcode={errcode} | errmsg={errmsg}"
                )
                continue

            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)

            # 缓存到 Redis（提前 5 分钟过期）
            key = _redis_key(org_id)
            ttl = max(expires_in - REFRESH_MARGIN, 60)
            redis = await get_redis()
            if redis:
                try:
                    await redis.set(key, token, ex=ttl)
                except Exception as e:
                    logger.warning(f"Wecom token: Redis write failed | org_id={org_id} | error={e}")

            logger.info(
                f"Wecom token: refreshed | org_id={org_id} | "
                f"expires_in={expires_in}s | cache_ttl={ttl}s"
            )
            return token

        except Exception as e:
            logger.warning(
                f"Wecom token: fetch failed | org_id={org_id} | attempt={attempt} | error={e}"
            )

    logger.error(f"Wecom token: all retries exhausted | org_id={org_id}")
    return None
