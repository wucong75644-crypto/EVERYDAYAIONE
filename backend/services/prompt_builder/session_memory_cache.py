"""Session 级 Curated Memory 缓存。

设计:
  问题: 当前每次 PromptBuilder.build() 都调 MemoryServiceV2.build_memory_context()
        → 每条新 user 消息都执行 Curated Memory Search。

  方案:
        - 新会话开头一次性查询 Curated Memory
        - 整会话固定, 不再查
        - 学到的新事实异步抽取存 DB, 等下次新会话生效

  实现: Redis cache (key=conv_id), 跟 conversation_cache 同 TTL (30min)
       cache miss → 调 MemoryServiceV2 → 写回 cache
       cache hit → 直接返回
"""

from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from loguru import logger

_CACHE_TTL = 1800  # 30 minute (跟 conversation_cache 一致)
_MAX_VALUE_BYTES = 50 * 1024
_KEY_PREFIX = "pb:session_mem"


def _make_key(conv_id: str, org_id: Optional[str]) -> str:
    org = org_id or "global"
    return f"{_KEY_PREFIX}:{org}:{conv_id}"


async def get_session_memory(
    conv_id: str,
    org_id: Optional[str] = None,
) -> Optional[Tuple[Optional[str], str]]:
    """读取会话级 Curated Memory 缓存。

    Returns:
        (prepend, persona) 元组, 或 None (cache miss).
        prepend 可能是 None (无 L1 召回结果).
        persona 是空串 (无 L3 画像).
    """
    try:
        from core.redis import get_redis
        r = await get_redis()
        if r is None:
            return None
        raw = await r.get(_make_key(conv_id, org_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return (data.get("prepend"), data.get("persona", ""))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SessionMemoryCache get failed | conv={conv_id} | {e}")
        return None


async def set_session_memory(
    conv_id: str,
    prepend: Optional[str],
    persona: str,
    org_id: Optional[str] = None,
    ttl: int = _CACHE_TTL,
) -> bool:
    """写入会话级 Curated Memory 缓存。"""
    try:
        from core.redis import get_redis
        r = await get_redis()
        if r is None:
            return False
        data = {"prepend": prepend, "persona": persona}
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw.encode("utf-8")) > _MAX_VALUE_BYTES:
            logger.warning(
                f"SessionMemoryCache value too large, skip | "
                f"conv={conv_id} | size={len(raw)}B"
            )
            return False
        await r.set(_make_key(conv_id, org_id), raw, ex=ttl)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SessionMemoryCache set failed | conv={conv_id} | {e}")
        return False


async def delete_session_memory(
    conv_id: str,
    org_id: Optional[str] = None,
) -> None:
    """删除会话级 Curated Memory 缓存。"""
    try:
        from core.redis import get_redis
        r = await get_redis()
        if r is None:
            return
        await r.delete(_make_key(conv_id, org_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SessionMemoryCache delete failed | conv={conv_id} | {e}")
