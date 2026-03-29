"""
Redis Pub/Sub Mixin for WebSocket Manager

提供跨进程消息投递能力，支持 uvicorn --workers N 多进程部署。
Redis 不可用时自动降级为本地投递（单进程模式）。
"""

import asyncio
import json
from typing import Any, Dict, Optional

from loguru import logger


# Redis Pub/Sub Channel
WS_CHANNEL = "ws:broadcast"


class RedisPubSubMixin:
    """
    Redis Pub/Sub Mixin

    为 WebSocketManager 提供跨进程消息投递。
    依赖主类提供: send_to_connection, _task_subscribers, _connections, _conn_index
    """

    # 类型声明（由主类提供的属性）
    _worker_id: str
    _pubsub: Any
    _listener_task: Optional[asyncio.Task]
    _redis_available: bool

    def _init_redis_state(self) -> None:
        """初始化 Redis 相关状态（由主类 __init__ 调用）"""
        self._pubsub = None
        self._listener_task = None
        self._redis_available = False

    async def start_redis_listener(self) -> None:
        """启动 Redis Pub/Sub 监听（在 lifespan startup 中调用）"""
        try:
            from core.config import settings
            from redis.asyncio import Redis

            self._pubsub_redis = Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=300.0,
                socket_connect_timeout=5.0,
                socket_keepalive=True,
            )
            self._pubsub = self._pubsub_redis.pubsub()
            await self._pubsub.subscribe(WS_CHANNEL)
            self._redis_available = True
            self._listener_task = asyncio.create_task(self._redis_listen_loop())
            logger.info(
                f"Redis Pub/Sub started | worker={self._worker_id} | "
                f"channel={WS_CHANNEL}"
            )
        except Exception as e:
            logger.warning(
                f"Redis Pub/Sub unavailable, using local-only mode | error={e}"
            )
            self._redis_available = False

    async def stop_redis_listener(self) -> None:
        """停止 Redis Pub/Sub 监听（在 lifespan shutdown 中调用）"""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(WS_CHANNEL)
                await self._pubsub.aclose()
            except Exception:
                pass
            self._pubsub = None

        if hasattr(self, "_pubsub_redis") and self._pubsub_redis:
            try:
                await self._pubsub_redis.aclose()
            except Exception:
                pass

        self._redis_available = False
        logger.info(f"Redis Pub/Sub stopped | worker={self._worker_id}")

    async def _redis_listen_loop(self) -> None:
        """Redis 监听循环：接收其他 Worker 发来的消息（含自动重连 + 心跳保活）"""
        retry_delay = 2
        max_delay = 30
        consecutive_failures = 0
        ping_interval = 60  # 每 60 秒发一次 ping，防止 Upstash 空闲断连

        while True:
            try:
                got_message = False
                last_ping = asyncio.get_event_loop().time()

                async for raw_msg in self._pubsub.listen():
                    got_message = True
                    consecutive_failures = 0

                    # 定期 ping 保活
                    now = asyncio.get_event_loop().time()
                    if now - last_ping > ping_interval:
                        try:
                            await self._pubsub.ping()
                        except Exception:
                            pass
                        last_ping = now

                    if raw_msg["type"] not in ("message",):
                        continue

                    try:
                        data = json.loads(raw_msg["data"])
                        if data.get("source") == self._worker_id:
                            continue
                        await self._deliver_from_redis(data)
                    except json.JSONDecodeError:
                        logger.warning("Redis Pub/Sub received invalid JSON")
                    except Exception as e:
                        logger.warning(f"Redis message handling error | error={e}")

                if not got_message:
                    raise ConnectionError(
                        "PubSub listen() returned empty, likely not subscribed"
                    )

            except asyncio.CancelledError:
                return
            except Exception as e:
                consecutive_failures += 1
                delay = min(retry_delay * (2 ** (consecutive_failures - 1)), max_delay)
                logger.warning(
                    f"Redis listener disconnected, reconnecting in {delay}s | "
                    f"worker={self._worker_id} | attempt={consecutive_failures} | "
                    f"error={e}"
                )
                self._redis_available = False

                await asyncio.sleep(delay)

                try:
                    await self._reconnect_pubsub()
                    logger.info(
                        f"Redis Pub/Sub reconnected | worker={self._worker_id} | "
                        f"after {consecutive_failures} attempts"
                    )
                except asyncio.CancelledError:
                    return
                except Exception as reconn_err:
                    logger.warning(
                        f"Redis reconnect failed | worker={self._worker_id} | "
                        f"error={reconn_err}"
                    )

    async def _reconnect_pubsub(self) -> None:
        """重建 Redis Pub/Sub 连接"""
        if self._pubsub:
            try:
                await self._pubsub.close()
            except Exception:
                pass

        if hasattr(self, "_pubsub_redis") and self._pubsub_redis:
            try:
                await self._pubsub_redis.close()
            except Exception:
                pass

        from core.config import settings
        from redis.asyncio import Redis

        self._pubsub_redis = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=300.0,
            socket_connect_timeout=5.0,
            socket_keepalive=True,
        )
        self._pubsub = self._pubsub_redis.pubsub()
        await self._pubsub.subscribe(WS_CHANNEL)
        self._redis_available = True

    async def _deliver_from_redis(self, data: Dict[str, Any]) -> None:
        """根据 Redis 消息的 target 信息，投递到本地连接"""
        target_type = data.get("target_type")
        target_id = data.get("target_id")
        message = data.get("message")

        if not message:
            return

        if target_type == "task":
            subscribers = self._task_subscribers.get(target_id, set())
            for conn_id in list(subscribers):
                await self.send_to_connection(conn_id, message)

        elif target_type == "user":
            connections = self._connections.get(target_id, {})
            for conn_id in list(connections.keys()):
                await self.send_to_connection(conn_id, message)

        elif target_type == "broadcast":
            broadcast_org_id = data.get("org_id")
            for conn_id, conn in list(self._conn_index.items()):
                if broadcast_org_id is not None and conn.org_id != broadcast_org_id:
                    continue
                await self.send_to_connection(conn_id, message)

    async def _publish(
        self,
        target_type: str,
        target_id: str,
        message: Dict[str, Any],
        org_id: str | None = None,
    ) -> None:
        """发布消息到 Redis Channel，供其他 Worker 接收"""
        if not self._redis_available:
            return

        try:
            from core.redis import RedisClient
            client = await RedisClient.get_client()
            data: Dict[str, Any] = {
                "source": self._worker_id,
                "target_type": target_type,
                "target_id": target_id,
                "message": message,
            }
            if org_id is not None:
                data["org_id"] = org_id
            payload = json.dumps(data, ensure_ascii=False)
            await client.publish(WS_CHANNEL, payload)
        except Exception as e:
            logger.warning(f"Redis publish failed | error={e}")
