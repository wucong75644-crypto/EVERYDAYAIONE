"""
企业微信自建应用 access_token 管理

- 通过 corpid + secret 获取 token
- Redis 缓存（提前 5 分钟刷新）
- 失败重试 3 次
- API 文档：https://developer.work.weixin.qq.com/document/path/91039
"""

import time
from typing import Optional

import httpx
from loguru import logger

from core.config import get_settings
from core.redis import get_redis

REDIS_KEY = "wecom:access_token"
TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
REFRESH_MARGIN = 300  # 提前 5 分钟刷新


async def get_access_token() -> Optional[str]:
    """
    获取企微自建应用 access_token（优先从 Redis 缓存读取）。

    Returns:
        access_token 字符串，失败返回 None
    """
    # 1. 尝试从 Redis 读取
    redis = await get_redis()
    if redis:
        try:
            cached = await redis.get(REDIS_KEY)
            if cached:
                return cached
        except Exception as e:
            logger.warning(f"Wecom token: Redis read failed | error={e}")

    # 2. 缓存未命中，从 API 获取
    return await _fetch_and_cache_token()


async def _fetch_and_cache_token(retries: int = 3) -> Optional[str]:
    """从企微 API 获取 token 并缓存到 Redis"""
    settings = get_settings()
    corp_id = settings.wecom_corp_id
    secret = settings.wecom_agent_secret

    if not corp_id or not secret:
        logger.error("Wecom token: corp_id or agent_secret not configured")
        return None

    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    TOKEN_URL,
                    params={"corpid": corp_id, "corpsecret": secret},
                )
                data = resp.json()

            errcode = data.get("errcode", -1)
            if errcode != 0:
                errmsg = data.get("errmsg", "unknown")
                logger.warning(
                    f"Wecom token: API error | attempt={attempt} | "
                    f"errcode={errcode} | errmsg={errmsg}"
                )
                continue

            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)

            # 缓存到 Redis（提前 5 分钟过期）
            ttl = max(expires_in - REFRESH_MARGIN, 60)
            redis = await get_redis()
            if redis:
                try:
                    await redis.set(REDIS_KEY, token, ex=ttl)
                except Exception as e:
                    logger.warning(f"Wecom token: Redis write failed | error={e}")

            logger.info(
                f"Wecom token: refreshed | expires_in={expires_in}s | "
                f"cache_ttl={ttl}s"
            )
            return token

        except Exception as e:
            logger.warning(
                f"Wecom token: fetch failed | attempt={attempt} | error={e}"
            )

    logger.error("Wecom token: all retries exhausted")
    return None
