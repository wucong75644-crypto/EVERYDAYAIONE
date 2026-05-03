"""
WebSocket 连接管理器（分布式版）

支持多 Worker 进程间通过 Redis Pub/Sub 投递 WebSocket 消息。

架构：
- 每个 Worker 维护本地连接池和任务订阅
- 发送消息时：本地投递 + Redis Publish
- 每个 Worker 监听 Redis Channel，收到后投递给本地连接
- 通过 worker_id 过滤自身消息，避免重复投递
"""

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket
from loguru import logger

from services.websocket_redis import RedisPubSubMixin


# === 配置常量 ===

HEARTBEAT_INTERVAL = 30
HEARTBEAT_TIMEOUT = 60
MAX_CONNECTIONS_PER_USER = 5
CONNECTION_CLEANUP_INTERVAL = 300


@dataclass
class Connection:
    """单个 WebSocket 连接"""
    websocket: WebSocket
    user_id: str
    conn_id: str
    org_id: str | None = None
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    subscribed_tasks: Set[str] = field(default_factory=set)


class WebSocketManager(RedisPubSubMixin):
    """
    WebSocket 连接管理器（分布式版）

    支持 uvicorn --workers N 多进程部署。
    通过 Redis Pub/Sub 实现跨进程消息投递。
    Redis 不可用时自动降级为本地投递（单进程模式）。
    """

    def __init__(self):
        # 本地连接管理
        self._connections: Dict[str, Dict[str, Connection]] = {}
        self._task_subscribers: Dict[str, Set[str]] = {}
        self._conn_index: Dict[str, Connection] = {}
        self._lock = asyncio.Lock()

        # 工具确认等待机制（Phase 3 B5）
        # key = tool_call_id → (Event, approved: bool | None)
        self._pending_confirms: Dict[str, Tuple[asyncio.Event, List]] = {}

        # 用户打断机制（Steer）
        # key = task_id → Event（打断信号）
        self._steer_signals: Dict[str, asyncio.Event] = {}
        # key = task_id → str（打断消息内容）
        self._steer_messages: Dict[str, str] = {}

        # 用户取消机制（Cancel）— 硬停止，区别于 steer 的软打断
        # key = task_id → Event（取消信号）
        self._cancel_signals: Dict[str, asyncio.Event] = {}

        # Redis Pub/Sub
        self._worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._init_redis_state()

    # ================================================================
    # 连接管理
    # ================================================================

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        conn_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> str:
        """注册新连接"""
        await websocket.accept()

        if not conn_id:
            conn_id = str(uuid.uuid4())

        connection = Connection(
            websocket=websocket,
            user_id=user_id,
            conn_id=conn_id,
            org_id=org_id,
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

    def _remove_connection(self, conn_id: str) -> Optional[Connection]:
        """从索引中移除连接并清理订阅关系（不获取锁，由调用方保证线程安全）"""
        connection = self._conn_index.pop(conn_id, None)
        if not connection:
            return None

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

        return connection

    async def _force_disconnect(self, conn_id: str):
        """强制断开连接（不获取锁，由调用方确保线程安全）"""
        connection = self._remove_connection(conn_id)
        if connection:
            try:
                await connection.websocket.close(
                    code=1000, reason="Connection replaced"
                )
            except Exception:
                pass

    async def disconnect(self, conn_id: str):
        """断开连接"""
        async with self._lock:
            connection = self._remove_connection(conn_id)

        if connection:
            logger.info(
                f"WebSocket disconnected | user={connection.user_id} | "
                f"conn={conn_id}"
            )

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

    async def send_to_connection(
        self, conn_id: str, message: Dict[str, Any]
    ) -> bool:
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

    async def send_to_user(
        self, user_id: str, message: Dict[str, Any],
        org_id: str | None = None,
    ):
        """发送消息到用户的连接（按 org 过滤，本地 + 跨进程）

        Args:
            org_id: 传入时只发给该 org 的连接；None 时发给所有连接（向后兼容）
        """
        connections = self._connections.get(user_id, {})

        logger.debug(
            f"send_to_user | user={user_id} | org={org_id} | "
            f"msg_type={message.get('type')} | "
            f"local_connections={len(connections)}"
        )

        for conn_id, conn in list(connections.items()):
            if org_id is not None and getattr(conn, "org_id", None) != org_id:
                continue
            await self.send_to_connection(conn_id, message)

        await self._publish("user", user_id, message, org_id=org_id)

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

        delivered = 0
        for conn_id in list(subscribers):
            if await self.send_to_connection(conn_id, message):
                delivered += 1

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
        local_subscribers = self._task_subscribers.get(task_id, set())
        if local_subscribers:
            logger.info(
                f"send_to_task_or_user | task={task_id} | "
                f"path=local_task | count={len(local_subscribers)}"
            )
            for conn_id in list(local_subscribers):
                await self.send_to_connection(conn_id, message)
        else:
            local_conns = self._connections.get(user_id, {})
            if local_conns:
                logger.info(
                    f"send_to_task_or_user | task={task_id} | "
                    f"path=local_user | user={user_id}"
                )
                for conn_id in list(local_conns.keys()):
                    await self.send_to_connection(conn_id, message)

        await self._publish("user", user_id, message)

    async def broadcast_all(self, message: Dict[str, Any], org_id: str | None = None):
        """广播消息到所有连接（本地 + 跨进程）

        Args:
            message: 消息数据
            org_id: 指定企业ID时只发给该企业的连接，None则发给所有
        """
        for conn_id, conn in list(self._conn_index.items()):
            if org_id is not None and conn.org_id != org_id:
                continue
            await self.send_to_connection(conn_id, message)

        await self._publish("broadcast", "", message, org_id=org_id)

    # ================================================================
    # 工具确认等待（Phase 3 B5）
    # ================================================================

    async def wait_for_confirm(
        self, tool_call_id: str, timeout: float = 60.0,
    ) -> bool:
        """等待用户确认写操作。

        Args:
            tool_call_id: 工具调用 ID（唯一标识）
            timeout: 超时秒数

        Returns:
            True = 用户确认执行，False = 用户拒绝或超时
        """
        event = asyncio.Event()
        result_holder: List = [None]  # [bool | None]
        self._pending_confirms[tool_call_id] = (event, result_holder)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return result_holder[0] is True
        except asyncio.TimeoutError:
            logger.info(
                f"Tool confirm timeout | tool_call_id={tool_call_id}"
            )
            return False
        finally:
            self._pending_confirms.pop(tool_call_id, None)

    def resolve_confirm(self, tool_call_id: str, approved: bool) -> bool:
        """前端确认/拒绝后调用，唤醒等待方。

        Returns:
            True = 找到并唤醒了等待方，False = 无匹配（已超时或不存在）
        """
        pending = self._pending_confirms.get(tool_call_id)
        if not pending:
            logger.warning(
                f"Tool confirm resolve miss | tool_call_id={tool_call_id}"
            )
            return False
        event, result_holder = pending
        result_holder[0] = approved
        event.set()
        return True

    # ================================================================
    # 用户打断（Steer）— 参考 Claude Code steering 队列
    # ================================================================

    def register_steer_listener(self, task_id: str) -> None:
        """注册打断监听（工具循环开始时调用）"""
        self._steer_signals[task_id] = asyncio.Event()

    def check_steer(self, task_id: str) -> str | None:
        """非阻塞检查是否有打断信号（每个工具执行完后调用）

        Returns:
            打断消息文本，无打断时返回 None
        """
        event = self._steer_signals.get(task_id)
        if event and event.is_set():
            msg = self._steer_messages.pop(task_id, None)
            self._steer_signals.pop(task_id, None)
            return msg
        return None

    def resolve_steer(self, task_id: str, message: str) -> bool:
        """前端打断消息到达时调用，唤醒等待方

        Returns:
            True = 找到并唤醒了监听方，False = 无匹配
        """
        self._steer_messages[task_id] = message
        event = self._steer_signals.get(task_id)
        if event:
            event.set()
            return True
        logger.warning(f"Steer resolve miss | task_id={task_id}")
        return False

    def unregister_steer_listener(self, task_id: str) -> None:
        """清理打断监听（工具循环结束时调用）"""
        self._steer_signals.pop(task_id, None)
        self._steer_messages.pop(task_id, None)

    # ================================================================
    # 用户取消（Cancel）— 硬停止 Agent 循环
    # ================================================================

    def register_cancel_listener(self, task_id: str) -> None:
        """注册取消监听（工具循环开始时调用）"""
        self._cancel_signals[task_id] = asyncio.Event()

    def is_cancelled(self, task_id: str) -> bool:
        """非阻塞检查是否已被用户取消"""
        event = self._cancel_signals.get(task_id)
        return bool(event and event.is_set())

    def cancel_task(self, task_id: str) -> bool:
        """触发取消信号（cancel API 调用）

        Returns:
            True = 找到运行中的任务并发送了取消信号
        """
        event = self._cancel_signals.get(task_id)
        if event:
            event.set()
            logger.info(f"Cancel signal sent | task_id={task_id}")
            return True
        logger.warning(f"Cancel signal miss (task not running) | task_id={task_id}")
        return False

    def unregister_cancel_listener(self, task_id: str) -> None:
        """清理取消监听（工具循环结束时调用）"""
        self._cancel_signals.pop(task_id, None)

    # ================================================================
    # 心跳与清理
    # ================================================================

    async def update_heartbeat(self, conn_id: str):
        """更新心跳时间"""
        connection = self._conn_index.get(conn_id)
        if connection:
            connection.last_heartbeat = time.time()

    async def cleanup_stale_connections(self):
        """清理超时连接"""
        now = time.time()
        stale_connections = [
            conn_id
            for conn_id, conn in self._conn_index.items()
            if now - conn.last_heartbeat > HEARTBEAT_TIMEOUT
        ]

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
            "total_subscriptions": sum(
                len(s) for s in self._task_subscribers.values()
            ),
            "worker_id": self._worker_id,
            "redis_available": self._redis_available,
        }


# 全局单例
ws_manager = WebSocketManager()
