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
# 缓冲区最大字节数（1MB）
MAX_BUFFER_SIZE_BYTES = 1 * 1024 * 1024
# 缓冲区消息最大存活时间（秒）
BUFFER_MAX_AGE_SECONDS = 300  # 5 分钟
# 单用户最大连接数
MAX_CONNECTIONS_PER_USER = 5
# 缓冲区清理间隔（秒）
BUFFER_CLEANUP_INTERVAL = 600  # 10 分钟


@dataclass
class Connection:
    """单个 WebSocket 连接"""
    websocket: WebSocket
    user_id: str
    conn_id: str
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    subscribed_tasks: Set[str] = field(default_factory=set)


@dataclass
class TaskBuffer:
    """
    任务消息缓冲区（字节 + 时间双维度）

    用于断点续传，当客户端重新连接时可以补发错过的消息。
    """
    # (timestamp, index, message_json)
    messages: List[Tuple[float, int, str]] = field(default_factory=list)
    accumulated_content: str = ""
    total_bytes: int = 0
    created_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    expire_at: Optional[float] = None  # 任务完成后设置过期时间
    next_index: int = 0  # 下一个消息的索引

    def add_message(self, msg: str) -> int:
        """
        添加消息，自动清理超限内容

        Args:
            msg: JSON 格式的消息字符串

        Returns:
            消息的索引
        """
        now = time.time()
        msg_bytes = len(msg.encode('utf-8'))
        current_index = self.next_index

        self.messages.append((now, current_index, msg))
        self.total_bytes += msg_bytes
        self.last_update = now
        self.next_index += 1

        # 清理超过 5 分钟的旧消息
        cutoff_time = now - BUFFER_MAX_AGE_SECONDS
        while self.messages and self.messages[0][0] < cutoff_time:
            _, _, old_msg = self.messages.pop(0)
            self.total_bytes -= len(old_msg.encode('utf-8'))

        # 清理超过 1MB 的旧消息
        while self.total_bytes > MAX_BUFFER_SIZE_BYTES and self.messages:
            _, _, old_msg = self.messages.pop(0)
            self.total_bytes -= len(old_msg.encode('utf-8'))

        return current_index

    def get_messages_after(self, last_index: int) -> List[Tuple[int, str]]:
        """
        获取指定索引之后的消息（用于断点续传）

        Args:
            last_index: 客户端收到的最后一条消息的索引

        Returns:
            [(index, message_json), ...]
        """
        result = []
        for _, idx, msg in self.messages:
            if idx > last_index:
                result.append((idx, msg))
        return result

    def get_current_index(self) -> int:
        """获取当前最新消息的索引"""
        return self.next_index - 1 if self.next_index > 0 else -1


class WebSocketManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # user_id -> {conn_id -> Connection}
        self._connections: Dict[str, Dict[str, Connection]] = {}
        # task_id -> Set[conn_id]
        self._task_subscribers: Dict[str, Set[str]] = {}
        # conn_id -> Connection (快速查找)
        self._conn_index: Dict[str, Connection] = {}
        # task_id -> TaskBuffer (消息缓冲)
        self._task_buffers: Dict[str, TaskBuffer] = {}
        # 锁
        self._lock = asyncio.Lock()
        # 清理任务
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start_cleanup_task(self):
        """启动定期清理任务"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            logger.info("WebSocket buffer cleanup task started")

    async def stop_cleanup_task(self):
        """停止清理任务"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

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

    async def subscribe_task(
        self,
        conn_id: str,
        task_id: str,
        last_index: int = -1
    ) -> Optional[Dict[str, Any]]:
        """
        订阅任务

        Args:
            conn_id: 连接 ID
            task_id: 任务 ID
            last_index: 上次收到的消息索引（用于断点续传）

        Returns:
            订阅结果，包含累积内容和需要补发的消息
        """
        async with self._lock:
            connection = self._conn_index.get(conn_id)
            if not connection:
                return None

            # 添加订阅关系
            if task_id not in self._task_subscribers:
                self._task_subscribers[task_id] = set()
            self._task_subscribers[task_id].add(conn_id)
            connection.subscribed_tasks.add(task_id)

            # 获取需要补发的消息（断点续传）
            buffer = self._task_buffers.get(task_id)
            if buffer:
                missed_messages = buffer.get_messages_after(last_index)
                return {
                    "accumulated": buffer.accumulated_content,
                    "missed_messages": missed_messages,
                    "current_index": buffer.get_current_index()
                }

            return {"accumulated": "", "missed_messages": [], "current_index": -1}

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

        for conn_id in list(connections.keys()):
            await self.send_to_connection(conn_id, message)

    async def send_to_task_subscribers(
        self,
        task_id: str,
        message: Dict[str, Any],
        buffer: bool = True
    ) -> int:
        """
        发送消息到任务的所有订阅者

        Args:
            task_id: 任务 ID
            message: 消息内容
            buffer: 是否缓存消息（用于断点续传）

        Returns:
            消息索引
        """
        message_index = -1

        # 缓存消息
        if buffer:
            async with self._lock:
                if task_id not in self._task_buffers:
                    self._task_buffers[task_id] = TaskBuffer()

                task_buffer = self._task_buffers[task_id]

                # 序列化消息
                import json
                msg_json = json.dumps(message, ensure_ascii=False)
                message_index = task_buffer.add_message(msg_json)

                # 更新累积内容
                if message.get("type") == "chat_chunk":
                    text = message.get("payload", {}).get("text", "")
                    task_buffer.accumulated_content += text

        # 添加消息索引到消息中
        message["message_index"] = message_index

        # 发送给所有订阅者
        subscribers = self._task_subscribers.get(task_id, set())
        for conn_id in list(subscribers):
            await self.send_to_connection(conn_id, message)

        return message_index

    async def broadcast_all(self, message: Dict[str, Any]):
        """广播消息到所有连接"""
        for conn_id in list(self._conn_index.keys()):
            await self.send_to_connection(conn_id, message)

    async def mark_task_completed(self, task_id: str, delay_seconds: int = 300):
        """
        标记任务缓冲区为过期（延迟删除）

        任务完成后不立即删除缓冲区，给客户端一定时间来恢复连接。

        Args:
            task_id: 任务 ID
            delay_seconds: 延迟删除时间（默认 5 分钟）
        """
        async with self._lock:
            if task_id in self._task_buffers:
                self._task_buffers[task_id].expire_at = time.time() + delay_seconds

    async def clear_task_buffer(self, task_id: str):
        """立即清理任务缓冲区"""
        async with self._lock:
            self._task_buffers.pop(task_id, None)
            self._task_subscribers.pop(task_id, None)

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

    async def _periodic_cleanup(self):
        """定期清理过期的任务缓冲区和超时连接"""
        while True:
            try:
                await asyncio.sleep(BUFFER_CLEANUP_INTERVAL)

                # 清理超时连接
                await self.cleanup_stale_connections()

                # 清理过期缓冲区
                async with self._lock:
                    now = time.time()
                    expired_tasks = []

                    for task_id, buffer in self._task_buffers.items():
                        # 清理已设置过期时间且已过期的
                        if buffer.expire_at and now > buffer.expire_at:
                            expired_tasks.append(task_id)
                        # 清理超过 30 分钟无更新的（兜底）
                        elif now - buffer.last_update > 1800:
                            expired_tasks.append(task_id)

                    for task_id in expired_tasks:
                        del self._task_buffers[task_id]
                        self._task_subscribers.pop(task_id, None)
                        logger.info(f"Cleaned up buffer | task={task_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic cleanup error: {e}")

    def get_task_buffer(self, task_id: str) -> Optional[TaskBuffer]:
        """获取任务缓冲区（用于 API 返回 last_index）"""
        return self._task_buffers.get(task_id)

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
            "total_tasks": len(self._task_buffers),
            "total_subscriptions": sum(len(s) for s in self._task_subscribers.values()),
        }


# 全局单例
ws_manager = WebSocketManager()
