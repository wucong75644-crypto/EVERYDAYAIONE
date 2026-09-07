"""KIE 海外旁路上传结果的短期缓存。"""

from __future__ import annotations

import json
from typing import Optional

from loguru import logger

from core.redis import get_redis


_CACHE_KEY_PREFIX = "kie:shadow-overseas-upload:"
_CACHE_TTL_SECONDS = 60 * 60


def _cache_key(task_id: str) -> str:
    return f"{_CACHE_KEY_PREFIX}{task_id}"


async def save_overseas_shadow_upload_urls(
    task_id: str,
    source_urls: list[str],
    staged_urls: list[str],
) -> bool:
    """保存完整海外旁路上传结果，供一次图片获取失败重试使用。"""
    if not source_urls or len(source_urls) != len(staged_urls):
        return False

    try:
        redis = await get_redis()
        if not redis:
            return False
        payload = json.dumps({"source_urls": source_urls, "staged_urls": staged_urls})
        await redis.set(_cache_key(task_id), payload, ex=_CACHE_TTL_SECONDS)
        return True
    except Exception as exc:
        logger.warning(
            "KIE_SHADOW_UPLOAD_CACHE_FAILURE | task_id={} | error_type={}",
            task_id,
            type(exc).__name__,
        )
        return False


async def get_overseas_shadow_upload_urls(
    task_id: str,
) -> Optional[dict[str, str]]:
    """返回原 CDN URL 到海外临时空间 URL 的完整映射。"""
    try:
        redis = await get_redis()
        if not redis:
            return None
        payload = await redis.get(_cache_key(task_id))
        if not payload:
            return None
        data = json.loads(payload)
        source_urls = data.get("source_urls")
        staged_urls = data.get("staged_urls")
        if (
            not isinstance(source_urls, list)
            or not isinstance(staged_urls, list)
            or not source_urls
            or len(source_urls) != len(staged_urls)
            or not all(isinstance(url, str) and url for url in source_urls + staged_urls)
        ):
            return None
        return dict(zip(source_urls, staged_urls))
    except Exception as exc:
        logger.warning(
            "KIE_SHADOW_UPLOAD_CACHE_READ_FAILURE | task_id={} | error_type={}",
            task_id,
            type(exc).__name__,
        )
        return None
