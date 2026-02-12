"""
WebSocket 连接管理器

参考实现:
- https://github.com/DontPanicO/fastapi-distributed-websocket
- https://fastapi.tiangolo.com/advanced/websockets/

功能:
- 连接池管理（支持同一用户多连接）
- 任务订阅机制
- 消息广播
- 心跳保活
- 断点续传支持
- 字节+时间双维度缓冲区管理
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket
from loguru import logger


# === 配置常量 ===

# 心跳间隔（秒）
HEARTBEAT_INTERVAL = 30
# 心跳超时（秒）
HEARTBEAT_TIMEOUT = 60
# 单用户最大连接数
MAX_CONNECTIONS_PER_USER = 5
# 连接清理间隔（秒）
CONNECTION_CLEANUP_INTERVAL = 300  # 5 分钟


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
    """WebSocket 连接管理器（简化版）"""

    def __init__(self):
        # user_id -> {conn_id -> Connection}
        self._connections: Dict[str, Dict[str, Connection]] = {}
        # task_id -> Set[conn_id]
        self._task_subscribers: Dict[str, Set[str]] = {}
        # conn_id -> Connection (快速查找)
        self._conn_index: Dict[str, Connection] = {}
        # 锁
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        conn_id: Optional[str] = None
    ) -> str:
        """
        注册新连接

        Args:
            websocket: WebSocket 连接
            user_id: 用户 ID
            conn_id: 连接 ID（可选）

        Returns:
            连接 ID
        """
        await websocket.accept()

        if not conn_id:
            conn_id = str(uuid.uuid4())

        connection = Connection(
            websocket=websocket,
            user_id=user_id,
            conn_id=conn_id
        )

        async with self._lock:
            # 检查用户连接数限制
            if user_id in self._connections:
                if len(self._connections[user_id]) >= MAX_CONNECTIONS_PER_USER:
                    # 关闭最旧的连接
                    oldest_conn_id = min(
                        self._connections[user_id].keys(),
                        key=lambda cid: self._connections[user_id][cid].connected_at
                    )
                    await self._force_disconnect(oldest_conn_id)
                    logger.warning(
                        f"Max connections exceeded, closing oldest | "
                        f"user={user_id} | closed={oldest_conn_id}"
                    )

            # 添加到用户连接池
            if user_id not in self._connections:
                self._connections[user_id] = {}
            self._connections[user_id][conn_id] = connection

            # 添加到快速索引
            self._conn_index[conn_id] = connection

        logger.info(f"WebSocket connected | user={user_id} | conn={conn_id}")
        return conn_id

    async def _force_disconnect(self, conn_id: str):
        """强制断开连接（不获取锁，由调用方确保线程安全）"""
        connection = self._conn_index.pop(conn_id, None)
        if not connection:
            return

        user_id = connection.user_id

        # 从用户连接池移除
        if user_id in self._connections:
            self._connections[user_id].pop(conn_id, None)
            if not self._connections[user_id]:
                del self._connections[user_id]

        # 从所有任务订阅中移除
        for task_id in list(connection.subscribed_tasks):
            if task_id in self._task_subscribers:
                self._task_subscribers[task_id].discard(conn_id)
                if not self._task_subscribers[task_id]:
                    del self._task_subscribers[task_id]

        # 尝试关闭 WebSocket
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

            # 从用户连接池移除
            if user_id in self._connections:
                self._connections[user_id].pop(conn_id, None)
                if not self._connections[user_id]:
                    del self._connections[user_id]

            # 从所有任务订阅中移除
            for task_id in list(connection.subscribed_tasks):
                if task_id in self._task_subscribers:
                    self._task_subscribers[task_id].discard(conn_id)
                    if not self._task_subscribers[task_id]:
                        del self._task_subscribers[task_id]

        logger.info(f"WebSocket disconnected | user={connection.user_id} | conn={conn_id}")

    async def subscribe_task(self, conn_id: str, task_id: str) -> bool:
        """
        订阅任务（简化版）

        Args:
            conn_id: 连接 ID
            task_id: 任务 ID

        Returns:
            是否订阅成功
        """
        async with self._lock:
            connection = self._conn_index.get(conn_id)
            if not connection:
                return False

            # 添加订阅关系
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

    async def send_to_connection(self, conn_id: str, message: Dict[str, Any]):
        """发送消息到指定连接"""
        connection = self._conn_index.get(conn_id)
        if not connection:
            return

        try:
            await connection.websocket.send_json(message)
        except Exception as e:
            logger.warning(f"Send failed | conn={conn_id} | error={e}")
            await self.disconnect(conn_id)

    async def send_to_user(self, user_id: str, message: Dict[str, Any]):
        """发送消息到用户的所有连接"""
        connections = self._connections.get(user_id, {})

        logger.debug(
            f"send_to_user | user={user_id} | "
            f"msg_type={message.get('type')} | "
            f"connections={len(connections)}"
        )

        for conn_id in list(connections.keys()):
            await self.send_to_connection(conn_id, message)

    async def send_to_task_subscribers(
        self,
        task_id: str,
        message: Dict[str, Any],
    ) -> None:
        """
        发送消息到任务的所有订阅者（简化版）

        Args:
            task_id: 任务 ID
            message: 消息内容
        """
        subscribers = self._task_subscribers.get(task_id, set())

        logger.debug(
            f"send_to_task_subscribers | task={task_id} | "
            f"msg_type={message.get('type')} | "
            f"subscribers={len(subscribers)}"
        )

        for conn_id in list(subscribers):
            await self.send_to_connection(conn_id, message)

    async def send_to_task_or_user(
        self,
        task_id: str,
        user_id: str,
        message: Dict[str, Any],
    ) -> None:
        """
        发送消息：优先走任务订阅，无订阅者时降级到用户广播

        用于 image/video 任务完成推送，确保消息送达。

        Args:
            task_id: 任务 ID
            user_id: 用户 ID（降级备用）
            message: 消息内容
        """
        subscribers = self._task_subscribers.get(task_id, set())

        if subscribers:
            logger.info(
                f"send_to_task_or_user | task={task_id} | "
                f"path=task_subscribers | count={len(subscribers)}"
            )
            await self.send_to_task_subscribers(task_id, message)
        else:
            logger.warning(
                f"send_to_task_or_user | task={task_id} | "
                f"path=user_fallback | user={user_id} | "
                f"reason=no_task_subscribers"
            )
            await self.send_to_user(user_id, message)

    async def broadcast_all(self, message: Dict[str, Any]):
        """广播消息到所有连接"""
        for conn_id in list(self._conn_index.keys()):
            await self.send_to_connection(conn_id, message)

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
        }


# 全局单例
ws_manager = WebSocketManager()
