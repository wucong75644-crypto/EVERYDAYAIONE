"""会话级 messages 内存缓存(Redis)。

V3.3: 让 messages 数组跨轮在 Redis 持续累积,避免每轮从 DB 重建丢失上下文。
对齐 OpenAI Assistants thread 模式 — 服务器持有 messages 状态。

链路:
  常规路径(99%):
    第 1 轮 tool_loop 结束 → 压缩后 messages 写 Redis
    第 2 轮 _build_llm_messages → Redis hit → 直接用(无 DB 重建,无 budget 砍)
    第 2 轮 tool_loop 结束 → 更新 Redis

  冷启动路径(1%):
    Redis miss(过期 / 服务器重启 / 用户隔天回来)
    → DB 重建 messages(完整,history_loader 不再 budget break)
    → 调统一压缩入口
    → 回填 Redis

DB 仍是归档 SSOT(完整 messages),Redis 是热数据(已压缩)。
故障降级: Redis 故障 → cache miss → 走 DB 重建路径。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from core.redis import get_redis

# 30 分钟空闲过期 — 跟用户连续对话节奏匹配
_CACHE_TTL = 1800

# 单 conversation cache value 上限 250KB
# 超出表示压缩没有效果,写入前由上层压缩处理
_MAX_VALUE_BYTES = 250 * 1024

# Redis key 格式: conv:msgs:{org_id}:{conv_id}
_KEY_PREFIX = "conv:msgs"


def _make_key(conv_id: str, org_id: Optional[str]) -> str:
    """构建 Redis key,含 org_id 隔离多租户。"""
    org = org_id or "global"
    return f"{_KEY_PREFIX}:{org}:{conv_id}"


async def get_messages(
    conv_id: str,
    org_id: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """从 Redis 取 messages。

    Returns:
        messages list 或 None(miss / 故障 / 解析失败均返回 None,触发降级)
    """
    if not conv_id:
        return None
    try:
        r = await get_redis()
        if r is None:
            return None
        raw = await r.get(_make_key(conv_id, org_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            logger.warning(f"ConversationCache: invalid type | conv={conv_id}")
            return None
        return data
    except Exception as e:  # noqa: BLE001
        # 任何异常都降级到 cache miss,不阻塞主流程
        logger.warning(f"ConversationCache get failed | conv={conv_id} | {e}")
        return None


async def set_messages(
    conv_id: str,
    messages: List[Dict[str, Any]],
    org_id: Optional[str] = None,
    ttl: int = _CACHE_TTL,
) -> bool:
    """写 messages 到 Redis。

    超过 _MAX_VALUE_BYTES 不写(让上层压缩后再尝试)。
    任何故障都吞掉异常,不阻塞主流程。

    Returns:
        True 写成功,False 跳过或故障
    """
    if not conv_id or not messages:
        return False
    try:
        r = await get_redis()
        if r is None:
            return False
        serialized = json.dumps(messages, ensure_ascii=False, default=str)
        size = len(serialized.encode("utf-8"))
        if size > _MAX_VALUE_BYTES:
            logger.warning(
                f"ConversationCache skip | conv={conv_id} | "
                f"size={size} > max={_MAX_VALUE_BYTES} (压缩未生效?)"
            )
            return False
        await r.setex(_make_key(conv_id, org_id), ttl, serialized)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ConversationCache set failed | conv={conv_id} | {e}")
        return False


async def delete_messages(
    conv_id: str,
    org_id: Optional[str] = None,
) -> None:
    """删 cache(用户主动清空对话 / 测试清理用)。"""
    if not conv_id:
        return
    try:
        r = await get_redis()
        if r is None:
            return
        await r.delete(_make_key(conv_id, org_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ConversationCache delete failed | conv={conv_id} | {e}")
