"""
Redis Streams 任务消息总线

解决多 Worker 部署下 WebSocket 消息跨进程投递 + 断线重连补发问题。

架构：
- 生产者（chat_handler 等）调用 publish() 写入 Redis Stream
- 消费者（ws.py）调用 consume() 从 Stream 读取并推送给 WS 客户端
- 断线重连时，客户端带上 last_stream_id，从断点继续读取
- 任务完成后设置 Stream 过期时间，自动清理

Redis 不可用时降级为直接 Pub/Sub 投递（现有逻辑）。
"""

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from loguru import logger


# ============================================================
# 配置
# ============================================================

STREAM_KEY_PREFIX = "stream:task:"
STREAM_MAXLEN = 1000       # 每个 Stream 最大条目数（防止 OOM）
STREAM_TTL_SECONDS = 600   # 任务完成后 Stream 保留时间（10 分钟）
XREAD_BLOCK_MS = 5000      # XREAD 阻塞等待时间（5 秒）

# 标记流结束的消息类型
_TERMINAL_TYPES = frozenset({"message_done", "message_error"})


# ============================================================
# 生产者 API
# ============================================================


async def publish(
    task_id: str,
    user_id: str,
    message: Dict[str, Any],
) -> Optional[str]:
    """
    写入一条消息到 Redis Stream。

    Args:
        task_id: 任务 ID（Stream key 的一部分）
        user_id: 用户 ID（写入 entry，用于消费时鉴权）
        message: 完整的 WS 消息 JSON（与直接推送格式一致）

    Returns:
        Stream entry ID（如 "1712438000000-0"），失败时返回 None

    降级：Redis 不可用时 fallback 到 ws_manager.send_to_task_or_user
    """
    try:
        from core.redis import RedisClient
        client = await RedisClient.get_client()

        stream_key = f"{STREAM_KEY_PREFIX}{task_id}"
        data = json.dumps(message, ensure_ascii=False)

        entry_id = await client.xadd(
            stream_key,
            {"user_id": user_id, "data": data},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )

        return entry_id

    except Exception as e:
        logger.warning(
            f"Stream publish failed, fallback to PubSub | "
            f"task={task_id} | error={e}"
        )
        # 降级：走原来的 Pub/Sub 路径
        await _fallback_pubsub(task_id, user_id, message)
        return None


async def set_stream_expire(task_id: str, ttl_seconds: int = STREAM_TTL_SECONDS) -> None:
    """
    任务完成后设置 Stream 过期时间。

    Args:
        task_id: 任务 ID
        ttl_seconds: 过期时间（秒），默认 600
    """
    try:
        from core.redis import RedisClient
        client = await RedisClient.get_client()

        stream_key = f"{STREAM_KEY_PREFIX}{task_id}"
        await client.expire(stream_key, ttl_seconds)

        logger.debug(f"Stream expire set | task={task_id} | ttl={ttl_seconds}s")
    except Exception as e:
        logger.warning(f"Stream expire failed | task={task_id} | error={e}")


# ============================================================
# 消费者 API
# ============================================================


async def consume(
    task_id: str,
    user_id: str,
    last_stream_id: str = "0",
) -> AsyncGenerator[Tuple[str, Dict[str, Any]], None]:
    """
    从 Redis Stream 读取消息的异步生成器。

    流程：
    1. XRANGE 补发 last_stream_id 之后的历史消息
    2. XREAD BLOCK 实时监听新消息
    3. 遇到 message_done / message_error 时自动结束

    Args:
        task_id: 任务 ID
        user_id: 用户 ID（鉴权：只返回 user_id 匹配的消息）
        last_stream_id: 上次收到的 Stream entry ID（"0" 表示从头读取）

    Yields:
        (stream_id, message_dict) 元组
    """
    from core.redis import RedisClient

    stream_key = f"{STREAM_KEY_PREFIX}{task_id}"
    cursor = last_stream_id

    try:
        client = await RedisClient.get_client()

        # Phase 1: 补发历史消息（XRANGE：从 cursor 之后开始）
        # XRANGE 的 min 需要用 "(" 前缀表示排他（exclusive）
        range_min = f"({cursor}" if cursor != "0" else "-"
        history = await client.xrange(stream_key, min=range_min, max="+")

        for entry_id, fields in history:
            msg = _parse_entry(entry_id, fields, user_id)
            if msg is None:
                continue
            stream_id, message = msg
            cursor = stream_id
            yield stream_id, message
            if message.get("type") in _TERMINAL_TYPES:
                return

        # Phase 2: 实时监听（XREAD BLOCK）
        while True:
            entries = await client.xread(
                {stream_key: cursor},
                block=XREAD_BLOCK_MS,
                count=50,
            )

            if not entries:
                # 超时无新消息，检查 Stream 是否还存在
                exists = await client.exists(stream_key)
                if not exists:
                    # Stream 已过期（任务完成>10分钟），退出
                    logger.debug(
                        f"Stream expired, consumer exiting | task={task_id}"
                    )
                    return
                continue

            for _stream_name, messages in entries:
                for entry_id, fields in messages:
                    msg = _parse_entry(entry_id, fields, user_id)
                    if msg is None:
                        continue
                    stream_id, message = msg
                    cursor = stream_id
                    yield stream_id, message
                    if message.get("type") in _TERMINAL_TYPES:
                        return

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            f"Stream consume error | task={task_id} | cursor={cursor} | error={e}"
        )


# ============================================================
# 内部辅助函数
# ============================================================


def _parse_entry(
    entry_id: str,
    fields: Dict[str, str],
    expected_user_id: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    解析 Stream entry，校验 user_id 并反序列化消息。

    Returns:
        (stream_id, message_dict) 或 None（user_id 不匹配或解析失败）
    """
    # 鉴权：user_id 必须匹配
    if fields.get("user_id") != expected_user_id:
        return None

    data = fields.get("data")
    if not data:
        return None

    try:
        message = json.loads(data)
        # 附加 stream_id 供前端追踪断点
        message["stream_id"] = entry_id
        return entry_id, message
    except json.JSONDecodeError:
        logger.warning(f"Stream entry JSON decode failed | id={entry_id}")
        return None


async def _fallback_pubsub(
    task_id: str,
    user_id: str,
    message: Dict[str, Any],
) -> None:
    """Redis 不可用时降级到 ws_manager Pub/Sub 投递"""
    try:
        from services.websocket_manager import ws_manager
        await ws_manager.send_to_task_or_user(task_id, user_id, message)
    except Exception as e:
        logger.error(
            f"Fallback PubSub also failed | task={task_id} | error={e}"
        )
