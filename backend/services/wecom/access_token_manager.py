"""
企业微信自建应用 access_token 管理（per-org 版）

- 每个企业独立的 corp_id + agent_secret → 独立的 token
- Redis 缓存：legacy 按 org；Runtime 按 org + opaque credential revision
- 失败重试 3 次
- API 文档：https://developer.work.weixin.qq.com/document/path/91039
"""

import re
from typing import Optional

import httpx
from loguru import logger

from core.redis import get_redis

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
REFRESH_MARGIN = 300  # 提前 5 分钟刷新
_OPAQUE_CREDENTIAL_REVISION = re.compile(r"^wecom-app:[0-9a-f]{64}$")


def _redis_key(
    org_id: str,
    *,
    credential_revision: str | None = None,
) -> str:
    legacy_key = f"wecom:access_token:{org_id}"
    if credential_revision is None:
        return legacy_key
    if (
        not isinstance(credential_revision, str)
        or _OPAQUE_CREDENTIAL_REVISION.fullmatch(credential_revision) is None
    ):
        raise ValueError("WECOM_TOKEN_CREDENTIAL_REVISION_INVALID")
    return f"{legacy_key}:credential:{credential_revision}"


async def get_access_token(
    org_id: str,
    corp_id: str,
    agent_secret: str,
    *,
    credential_revision: str | None = None,
) -> Optional[str]:
    """
    获取指定企业的自建应用 access_token（优先从 Redis 缓存读取）。

    Args:
        org_id: 企业 ID（用作 Redis 缓存 key）
        corp_id: 企微企业 ID（corpid）
        agent_secret: 自建应用 Secret
        credential_revision: Runtime 专用非敏感 opaque 配置修订；省略时保持 legacy key

    Returns:
        access_token 字符串，失败返回 None
    """
    # 1. 尝试从 Redis 读取
    try:
        key = _redis_key(
            org_id,
            credential_revision=credential_revision,
        )
    except ValueError:
        return None
    redis = await get_redis()
    if redis:
        try:
            cached = await redis.get(key)
            if cached:
                return cached
        except Exception as e:
            logger.warning(f"Wecom token: Redis read failed | org_id={org_id} | error={e}")

    # 2. 缓存未命中，从 API 获取
    return await _fetch_and_cache_token(
        org_id,
        corp_id,
        agent_secret,
        credential_revision=credential_revision,
    )


async def _fetch_and_cache_token(
    org_id: str,
    corp_id: str,
    agent_secret: str,
    retries: int = 3,
    *,
    credential_revision: str | None = None,
) -> Optional[str]:
    """从企微 API 获取 token 并缓存到 Redis"""
    try:
        key = _redis_key(
            org_id,
            credential_revision=credential_revision,
        )
    except ValueError:
        return None
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
