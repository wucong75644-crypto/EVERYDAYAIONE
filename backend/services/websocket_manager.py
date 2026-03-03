"""
WebSocket 连接管理器（分布式版）

支持多 Worker 进程间通过 Redis Pub/Sub 投递 WebSocket 消息。

架构：
- 每个 Worker 维护本地连接池和任务订阅
- 发送消息时：本地投递 + Redis Publish
- 每个 Worker 监听 Redis Channel，收到后投递给本地连接
- 通过 worker_id 过滤自身消息，避免重复投递

参考实现:
- https://github.com/DontPanicO/fastapi-distributed-websocket
"""

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket
from loguru import logger


# === 配置常量 ===

HEARTBEAT_INTERVAL = 30
HEARTBEAT_TIMEOUT = 60
MAX_CONNECTIONS_PER_USER = 5
CONNECTION_CLEANUP_INTERVAL = 300

# Redis Pub/Sub Channel
WS_CHANNEL = "ws:broadcast"


@dataclass
class Connection:
    """单个 WebSocket 连接"""
    websocket: WebSocket
    user_id: str
    conn_id: str
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    subscribed_tasks: Set[str] = field(default_factory=set)


class WebSocketManager:
    """
    WebSocket 连接管理器（分布式版）

    支持 uvicorn --workers N 多进程部署。
    通过 Redis Pub/Sub 实现跨进程消息投递。
    Redis 不可用时自动降级为本地投递（单进程模式）。
    """

    def __init__(self):
        # === 本地连接管理（与原版相同） ===
        self._connections: Dict[str, Dict[str, Connection]] = {}
        self._task_subscribers: Dict[str, Set[str]] = {}
        self._conn_index: Dict[str, Connection] = {}
        self._lock = asyncio.Lock()

        # === Redis Pub/Sub（新增） ===
        self._worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._pubsub = None
        self._listener_task: Optional[asyncio.Task] = None
        self._redis_available = False

    # ================================================================
    # Redis Pub/Sub 生命周期
    # ================================================================

    async def start_redis_listener(self):
        """启动 Redis Pub/Sub 监听（在 lifespan startup 中调用）"""
        try:
            from core.config import settings
            from redis.asyncio import Redis

            # Pub/Sub 需要独立连接，不复用主连接
            # socket_timeout 需要设置较长：Pub/Sub 是长连接，需要持续等待消息
            # socket_connect_timeout 保持较短：连接建立不应太慢
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

    async def stop_redis_listener(self):
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
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None

        if hasattr(self, "_pubsub_redis") and self._pubsub_redis:
            try:
                await self._pubsub_redis.close()
            except Exception:
                pass

        self._redis_available = False
        logger.info(f"Redis Pub/Sub stopped | worker={self._worker_id}")

    async def _redis_listen_loop(self):
        """Redis 监听循环：接收其他 Worker 发来的消息，投递到本地连接（含自动重连）"""
        max_retries = 0  # 无限重连
        retry_delay = 2  # 初始重试间隔（秒）
        max_delay = 30   # 最大重试间隔
        consecutive_failures = 0

        while True:
            try:
                got_message = False
                async for raw_msg in self._pubsub.listen():
                    got_message = True
                    consecutive_failures = 0  # 成功接收消息，重置计数
                    if raw_msg["type"] != "message":
                        continue

                    try:
                        data = json.loads(raw_msg["data"])

                        # 跳过自身发出的消息（已经在本地投递过）
                        if data.get("source") == self._worker_id:
                            continue

                        await self._deliver_from_redis(data)
                    except json.JSONDecodeError:
                        logger.warning("Redis Pub/Sub received invalid JSON")
                    except Exception as e:
                        logger.warning(f"Redis message handling error | error={e}")

                # listen() 正常结束但没产出任何消息 → PubSub 状态异常（未订阅）
                if not got_message:
                    raise ConnectionError("PubSub listen() returned empty, likely not subscribed")

            except asyncio.CancelledError:
                return  # 正常退出
            except Exception as e:
                consecutive_failures += 1
                delay = min(retry_delay * (2 ** (consecutive_failures - 1)), max_delay)
                logger.warning(
                    f"Redis listener disconnected, reconnecting in {delay}s | "
                    f"worker={self._worker_id} | attempt={consecutive_failures} | error={e}"
                )
                self._redis_available = False

                await asyncio.sleep(delay)

                # 尝试重建 Pub/Sub 连接
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
                        f"Redis reconnect failed | worker={self._worker_id} | error={reconn_err}"
                    )

    async def _reconnect_pubsub(self):
        """重建 Redis Pub/Sub 连接"""
        # 清理旧连接
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

        # 建立新连接
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

    async def _deliver_from_redis(self, data: Dict[str, Any]):
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
            for conn_id in list(self._conn_index.keys()):
                await self.send_to_connection(conn_id, message)

    async def _publish(self, target_type: str, target_id: str, message: Dict[str, Any]):
        """发布消息到 Redis Channel，供其他 Worker 接收"""
        if not self._redis_available:
            return

        try:
            from core.redis import RedisClient
            client = await RedisClient.get_client()
            payload = json.dumps({
                "source": self._worker_id,
                "target_type": target_type,
                "target_id": target_id,
                "message": message,
            }, ensure_ascii=False)
            await client.publish(WS_CHANNEL, payload)
        except Exception as e:
            logger.warning(f"Redis publish failed | error={e}")

    # ================================================================
    # 连接管理（与原版相同，无改动）
    # ================================================================

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        conn_id: Optional[str] = None
    ) -> str:
        """注册新连接"""
        await websocket.accept()

        if not conn_id:
            conn_id = str(uuid.uuid4())

        connection = Connection(
            websocket=websocket,
            user_id=user_id,
            conn_id=conn_id
        )

        async with self._lock:
            if user_id in self._connections:
                if len(self._connections[user_id]) >= MAX_CONNECTIONS_PER_USER:
                    oldest_conn_id = min(
                        self._connections[user_id].keys(),
                        key=lambda cid: self._connections[user_id][cid].connected_at
                    )
                    await self._force_disconnect(oldest_conn_id)
                    logger.warning(
                        f"Max connections exceeded, closing oldest | "
                        f"user={user_id} | closed={oldest_conn_id}"
                    )

            if user_id not in self._connections:
                self._connections[user_id] = {}
            self._connections[user_id][conn_id] = connection
            self._conn_index[conn_id] = connection

        logger.info(f"WebSocket connected | user={user_id} | conn={conn_id}")
        return conn_id

    async def _force_disconnect(self, conn_id: str):
        """强制断开连接（不获取锁，由调用方确保线程安全）"""
        connection = self._conn_index.pop(conn_id, None)
        if not connection:
            return

        user_id = connection.user_id
        if user_id in self._connections:
            self._connections[user_id].pop(conn_id, None)
            if not self._connections[user_id]:
                del self._connections[user_id]

        for task_id in list(connection.subscribed_tasks):
            if task_id in self._task_subscribers:
                self._task_subscribers[task_id].discard(conn_id)
                if not self._task_subscribers[task_id]:
                    del self._task_subscribers[task_id]

        try:
            await connection.websocket.close(code=1000, reason="Connection replaced")
        except Exception:
            pass

    async def disconnect(self, conn_id: str):
        """断开连接"""
        async with self._lock:
            connection = self._conn_index.pop(conn_id, None)
            if not connection:
                return

            user_id = connection.user_id
            if user_id in self._connections:
                self._connections[user_id].pop(conn_id, None)
                if not self._connections[user_id]:
                    del self._connections[user_id]

            for task_id in list(connection.subscribed_tasks):
                if task_id in self._task_subscribers:
                    self._task_subscribers[task_id].discard(conn_id)
                    if not self._task_subscribers[task_id]:
                        del self._task_subscribers[task_id]

        logger.info(f"WebSocket disconnected | user={connection.user_id} | conn={conn_id}")

    async def subscribe_task(self, conn_id: str, task_id: str) -> bool:
        """订阅任务"""
        async with self._lock:
            connection = self._conn_index.get(conn_id)
            if not connection:
                return False

            if task_id not in self._task_subscribers:
                self._task_subscribers[task_id] = set()
            self._task_subscribers[task_id].add(conn_id)
            connection.subscribed_tasks.add(task_id)

            return True

    async def unsubscribe_task(self, conn_id: str, task_id: str):
        """取消订阅任务"""
        async with self._lock:
            connection = self._conn_index.get(conn_id)
            if connection:
                connection.subscribed_tasks.discard(task_id)

            if task_id in self._task_subscribers:
                self._task_subscribers[task_id].discard(conn_id)
                if not self._task_subscribers[task_id]:
                    del self._task_subscribers[task_id]

    # ================================================================
    # 消息发送（本地投递 + Redis 跨进程投递）
    # ================================================================

    async def send_to_connection(self, conn_id: str, message: Dict[str, Any]) -> bool:
        """发送消息到指定连接，返回是否成功"""
        connection = self._conn_index.get(conn_id)
        if not connection:
            return False

        try:
            await connection.websocket.send_json(message)
            return True
        except Exception as e:
            logger.warning(f"Send failed | conn={conn_id} | error={e}")
            await self.disconnect(conn_id)
            return False

    async def send_to_user(self, user_id: str, message: Dict[str, Any]):
        """发送消息到用户的所有连接（本地 + 跨进程）"""
        connections = self._connections.get(user_id, {})

        logger.debug(
            f"send_to_user | user={user_id} | "
            f"msg_type={message.get('type')} | "
            f"local_connections={len(connections)}"
        )

        # 本地投递
        for conn_id in list(connections.keys()):
            await self.send_to_connection(conn_id, message)

        # 跨进程投递
        await self._publish("user", user_id, message)

    async def send_to_task_subscribers(
        self,
        task_id: str,
        message: Dict[str, Any],
    ) -> int:
        """发送消息到任务的所有订阅者（本地 + 跨进程）"""
        subscribers = self._task_subscribers.get(task_id, set())

        logger.debug(
            f"send_to_task_subscribers | task={task_id} | "
            f"msg_type={message.get('type')} | "
            f"local_subscribers={len(subscribers)}"
        )

        # 本地投递
        delivered = 0
        for conn_id in list(subscribers):
            if await self.send_to_connection(conn_id, message):
                delivered += 1

        # 跨进程投递
        await self._publish("task", task_id, message)

        return delivered

    async def send_to_task_or_user(
        self,
        task_id: str,
        user_id: str,
        message: Dict[str, Any],
    ) -> None:
        """
        发送消息：优先走任务订阅，同时通过 Redis 确保跨进程送达。

        多 Worker 场景下，本地可能没有任务订阅者（订阅在其他 Worker）。
        因此始终通过 Redis 以 user 维度广播，确保消息不丢。
        """
        # 本地尝试任务订阅投递
        local_subscribers = self._task_subscribers.get(task_id, set())
        if local_subscribers:
            logger.info(
                f"send_to_task_or_user | task={task_id} | "
                f"path=local_task | count={len(local_subscribers)}"
            )
            for conn_id in list(local_subscribers):
                await self.send_to_connection(conn_id, message)
        else:
            # 本地没有订阅者，尝试本地 user 投递
            local_conns = self._connections.get(user_id, {})
            if local_conns:
                logger.info(
                    f"send_to_task_or_user | task={task_id} | "
                    f"path=local_user | user={user_id}"
                )
                for conn_id in list(local_conns.keys()):
                    await self.send_to_connection(conn_id, message)

        # 跨进程：以 user 维度广播，确保其他 Worker 上的连接也能收到
        await self._publish("user", user_id, message)

    async def broadcast_all(self, message: Dict[str, Any]):
        """广播消息到所有连接（本地 + 跨进程）"""
        # 本地投递
        for conn_id in list(self._conn_index.keys()):
            await self.send_to_connection(conn_id, message)

        # 跨进程投递
        await self._publish("broadcast", "", message)

    # ================================================================
    # 心跳与清理（与原版相同，无改动）
    # ================================================================

    async def update_heartbeat(self, conn_id: str):
        """更新心跳时间"""
        connection = self._conn_index.get(conn_id)
        if connection:
            connection.last_heartbeat = time.time()

    async def cleanup_stale_connections(self):
        """清理超时连接"""
        now = time.time()
        stale_connections = []

        for conn_id, connection in list(self._conn_index.items()):
            if now - connection.last_heartbeat > HEARTBEAT_TIMEOUT:
                stale_connections.append(conn_id)

        for conn_id in stale_connections:
            logger.warning(f"Cleaning stale connection | conn={conn_id}")
            await self.disconnect(conn_id)

    def get_connection_count(self) -> int:
        """获取当前连接数"""
        return len(self._conn_index)

    def get_user_connection_count(self, user_id: str) -> int:
        """获取用户连接数"""
        return len(self._connections.get(user_id, {}))

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_connections": len(self._conn_index),
            "total_users": len(self._connections),
            "total_subscriptions": sum(len(s) for s in self._task_subscribers.values()),
            "worker_id": self._worker_id,
            "redis_available": self._redis_available,
        }


# 全局单例
ws_manager = WebSocketManager()
