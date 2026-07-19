"""带版本的闭合会话历史缓存。

数据库是上下文事实来源。Redis 只保存某个 conversation revision 已闭合的历史，
不得保存当前任务的 user message、工具调用或工具结果。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from core.redis import get_redis

_CACHE_TTL = 1800
_MAX_VALUE_BYTES = 250 * 1024
_KEY_PREFIX = "conv:msgs:v5"
_SCHEMA_VERSION = 5


def _make_key(conv_id: str, org_id: Optional[str]) -> str:
    """构建带租户隔离的 Redis key。"""
    return f"{_KEY_PREFIX}:{org_id or 'global'}:{conv_id}"


async def _delete_key(redis: Any, key: str, conv_id: str) -> None:
    """删除无法安全复用的缓存值；删除失败不阻塞数据库回源。"""
    try:
        await redis.delete(key)
    except Exception as error:  # noqa: BLE001
        logger.warning(
            f"context_cache_delete_failed | conversation_id={conv_id} | error={error}"
        )


async def get_closed_messages(
    conv_id: str,
    requested_revision: int,
    through_message_id: Optional[str],
    org_id: Optional[str] = None,
    *,
    summary_revision: int = 0,
    task_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """仅在历史上下界和闭合消息边界精确匹配时返回缓存历史。"""
    if (
        not conv_id
        or requested_revision < 0
        or summary_revision < 0
        or summary_revision > requested_revision
    ):
        return None
    try:
        redis = await get_redis()
        if redis is None:
            return None
        key = _make_key(conv_id, org_id)
        raw = await redis.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "context_cache_malformed_invalidated | "
                f"conversation_id={conv_id} | task_id={task_id} | turn_id={turn_id}"
            )
            await _delete_key(redis, key, conv_id)
            return None
        if isinstance(data, list):
            logger.info(
                f"context_cache_legacy_invalidated | conversation_id={conv_id}"
            )
            await _delete_key(redis, key, conv_id)
            return None
        if not _is_valid_envelope(data):
            logger.warning(
                f"context_cache_invalidated | conversation_id={conv_id}"
            )
            await _delete_key(redis, key, conv_id)
            return None
        if (
            data["revision"] != requested_revision
            or data.get("through_message_id") != through_message_id
            or data["summary_revision"] != summary_revision
        ):
            logger.debug(
                "context_cache_revision_miss | "
                f"conversation_id={conv_id} | requested_revision={requested_revision} | "
                f"requested_summary_revision={summary_revision} | "
                f"cached_revision={data['revision']} | "
                f"cached_summary_revision={data['summary_revision']} | task_id={task_id} | "
                f"turn_id={turn_id}"
            )
            return None
        logger.debug(
            "context_cache_hit | "
            f"conversation_id={conv_id} | revision={requested_revision} | "
            f"task_id={task_id} | turn_id={turn_id}"
        )
        return data["closed_messages"]
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "context_cache_get_failed | "
            f"conversation_id={conv_id} | requested_revision={requested_revision} | "
            f"task_id={task_id} | turn_id={turn_id} | error={error}"
        )
        return None


def _is_valid_envelope(data: Any) -> bool:
    """校验当前历史投影信封的最小结构。"""
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        return False
    revision = data.get("revision")
    summary_revision = data.get("summary_revision")
    through_message_id = data.get("through_message_id")
    messages = data.get("closed_messages")
    return (
        isinstance(revision, int)
        and revision >= 0
        and isinstance(summary_revision, int)
        and 0 <= summary_revision <= revision
        and (
            (revision == 0 and through_message_id is None)
            or (
                revision > 0
                and isinstance(through_message_id, str)
                and bool(through_message_id)
            )
        )
        and isinstance(messages, list)
        and all(isinstance(message, dict) for message in messages)
    )


async def set_closed_messages(
    conv_id: str,
    revision: int,
    through_message_id: Optional[str],
    messages: List[Dict[str, Any]],
    org_id: Optional[str] = None,
    ttl: int = _CACHE_TTL,
    *,
    summary_revision: int = 0,
    task_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> bool:
    """写入 v4 闭合历史信封；摘要 revision 是历史查询的排他下界。"""
    if (
        not conv_id
        or revision < 0
        or summary_revision < 0
        or summary_revision > revision
    ):
        return False
    envelope = {
        "schema_version": _SCHEMA_VERSION,
        "revision": revision,
        "summary_revision": summary_revision,
        "through_message_id": through_message_id,
        "closed_messages": messages,
    }
    try:
        redis = await get_redis()
        if redis is None:
            return False
        serialized = json.dumps(envelope, ensure_ascii=False, default=str)
        size = len(serialized.encode("utf-8"))
        if size > _MAX_VALUE_BYTES:
            logger.warning(
                "context_cache_write_skipped | "
                f"conversation_id={conv_id} | revision={revision} | "
                f"task_id={task_id} | turn_id={turn_id} | "
                f"size={size} | max_size={_MAX_VALUE_BYTES}"
            )
            return False
        await redis.setex(_make_key(conv_id, org_id), ttl, serialized)
        return True
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "context_cache_set_failed | "
            f"conversation_id={conv_id} | revision={revision} | "
            f"task_id={task_id} | turn_id={turn_id} | error={error}"
        )
        return False


async def delete_messages(
    conv_id: str,
    org_id: Optional[str] = None,
) -> None:
    """删除会话闭合历史缓存。"""
    if not conv_id:
        return
    try:
        redis = await get_redis()
        if redis is not None:
            await redis.delete(_make_key(conv_id, org_id))
    except Exception as error:  # noqa: BLE001
        logger.warning(
            f"context_cache_delete_failed | conversation_id={conv_id} | error={error}"
        )
