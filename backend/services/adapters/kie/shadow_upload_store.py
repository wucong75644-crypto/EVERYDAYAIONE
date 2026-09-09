"""KIE 海外旁路上传结果的短期缓存。"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any, Optional

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
    request: Optional[dict] = None,
) -> bool:
    """保存完整海外旁路上传结果，供一次图片获取失败重试使用。"""
    if not source_urls or len(source_urls) != len(staged_urls):
        return False

    return await _save(_cache_key(task_id), {
        "status": "ready", "source_urls": source_urls,
        "staged_urls": staged_urls, "request": request,
        "updated_at": time.time(),
    })


async def save_overseas_shadow_upload_status(
    task_id: str, status: str, source_urls: list[str],
    request: Optional[dict] = None,
) -> bool:
    """只有海外旁路写入状态；普通旁路不能覆盖它。"""
    if status not in {"pending", "failed"}:
        raise ValueError("Invalid shadow upload status")
    return await _save(_cache_key(task_id), {
        "status": status, "source_urls": source_urls,
        "request": request, "updated_at": time.time(),
    })


async def get_overseas_shadow_upload(task_id: str) -> Optional[dict]:
    """区分上传中、明确失败、完整成功；兼容部署前的 URL-only 缓存。"""
    data = await _load(_cache_key(task_id))
    if data is None:
        return None
    status = data.get("status", "ready")
    updated_at = data.get("updated_at")
    if not isinstance(status, str) or (updated_at is not None and (
        not isinstance(updated_at, (int, float)) or not math.isfinite(updated_at)
    )):
        return {"status": "failed", "reason": "invalid_cache"}
    if status in {"pending", "failed"}:
        return data
    source_urls, staged_urls = data.get("source_urls"), data.get("staged_urls")
    if (
        status != "ready" or not isinstance(source_urls, list)
        or not isinstance(staged_urls, list) or not source_urls
        or len(source_urls) != len(staged_urls)
        or not all(isinstance(url, str) and url for url in source_urls + staged_urls)
    ):
        return {"status": "failed", "reason": "invalid_cache"}
    return {**data, "status": "ready"}


async def _save(key: str, data: dict[str, Any]) -> bool:
    try:
        async with asyncio.timeout(3):
            redis = await get_redis()
            if not redis:
                return False
            await redis.set(key, json.dumps(data), ex=_CACHE_TTL_SECONDS)
            return True
    except Exception as exc:
        logger.warning("KIE_FALLBACK_CACHE_WRITE_FAILED | key={} | error_type={}", key, type(exc).__name__)
        return False


async def _load(key: str) -> Optional[dict]:
    try:
        async with asyncio.timeout(3):
            redis = await get_redis()
            if not redis:
                return None
            payload = await redis.get(key)
        if not payload:
            return None
        data = json.loads(payload)
        return data if isinstance(data, dict) else {"status": "failed", "reason": "invalid_cache"}
    except (ValueError, TypeError):
        return {"status": "failed", "reason": "invalid_cache"}
    except Exception as exc:
        logger.warning("KIE_FALLBACK_CACHE_READ_FAILED | key={} | error_type={}", key, type(exc).__name__)
        return None


async def get_overseas_shadow_upload_staged_urls(
    task_id: str,
) -> Optional[list[str]]:
    """返回按原提交顺序保存的海外临时空间 URL。"""
    data = await get_overseas_shadow_upload(task_id)
    return data["staged_urls"] if data and data["status"] == "ready" else None
