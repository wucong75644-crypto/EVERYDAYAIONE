# WebSocket 实时推送技术设计文档

> **版本**：v1.2 | **状态**：设计完成 | **最后更新**：2026-02-05

---

## 一、概述

### 1.1 目标

将现有的 SSE + 轮询 混合方案统一为 **WebSocket 单连接方案**，实现：

- ✅ 聊天消息流式传输（替代 SSE）
- ✅ 图片/视频生成状态推送（替代轮询）
- ✅ 积分变化实时通知
- ✅ 多对话多任务并发支持
- ✅ 断点续传、刷新恢复

### 1.2 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| **后端 WebSocket** | FastAPI 原生 | 已有技术栈，无需引入新依赖 |
| **消息分发** | Redis Pub/Sub | 支持多实例部署、高性能 |
| **前端 WebSocket** | 原生 WebSocket + 自定义 Hook | 轻量、可控，参考 react-use-websocket 设计 |
| **消息格式** | JSON | 统一格式，易解析 |

### 1.3 参考来源

- [FastAPI WebSocket 官方文档](https://fastapi.tiangolo.com/advanced/websockets/)
- [fastapi-distributed-websocket](https://github.com/DontPanicO/fastapi-distributed-websocket) - 分布式 WebSocket 方案
- [redis-streams-fastapi-chat](https://github.com/leonh/redis-streams-fastapi-chat) - Redis 支持的聊天方案
- [react-use-websocket](https://github.com/robtaussig/react-use-websocket) - React WebSocket Hook 设计
- [encode/broadcaster](https://github.com/encode/broadcaster) - FastAPI 推荐的广播库

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         前端 (React)                             │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                  WebSocketProvider (全局)                   │ │
│  │                                                             │ │
│  │  useWebSocket() Hook                                        │ │
│  │  ├── 连接管理（自动重连、心跳）                              │ │
│  │  ├── 消息订阅（按 type 分发）                               │ │
│  │  └── 状态管理（connected/reconnecting/disconnected）        │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│                              │ wss://api.example.com/ws          │
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      后端 (FastAPI)                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                 WebSocket Endpoint (/ws)                    │ │
│  │                                                             │ │
│  │  1. 认证（token 验证）                                      │ │
│  │  2. 注册连接（user_id → connection）                        │ │
│  │  3. 消息路由（接收客户端消息，分发服务端消息）               │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│                              ▼                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              WebSocketManager (连接管理器)                   │ │
│  │                                                             │ │
│  │  _connections: Dict[user_id, Dict[conn_id, WebSocket]]     │ │
│  │  _subscriptions: Dict[task_id, Set[conn_id]]               │ │
│  │                                                             │ │
│  │  方法:                                                      │ │
│  │  ├── connect(user_id, websocket)                           │ │
│  │  ├── disconnect(user_id, conn_id)                          │ │
│  │  ├── subscribe_task(conn_id, task_id)                      │ │
│  │  ├── unsubscribe_task(conn_id, task_id)                    │ │
│  │  ├── send_to_user(user_id, message)                        │ │
│  │  ├── send_to_task_subscribers(task_id, message)            │ │
│  │  └── broadcast_all(message)                                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│         ┌────────────────────┼────────────────────┐             │
│         ▼                    ▼                    ▼             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────────┐   │
│  │ ChatStream  │     │ ImageService│     │ CreditService   │   │
│  │ Manager     │     │ VideoService│     │                 │   │
│  │             │     │             │     │                 │   │
│  │ 流式内容    │     │ 任务状态    │     │ 积分变化        │   │
│  │ → WS 广播   │     │ → WS 推送   │     │ → WS 推送       │   │
│  └─────────────┘     └─────────────┘     └─────────────────┘   │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                   Redis Pub/Sub (可选)                      │ │
│  │                   用于多实例部署时的消息分发                 │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 消息流向

```
【聊天消息流式传输】

用户发送消息
    │
    ▼
POST /conversations/{id}/messages/stream  ──────────────────┐
    │                                                        │
    ▼                                                        │
创建 task (pending)                                          │
    │                                                        │
    ▼                                                        │
ChatStreamManager 启动后台协程                                │
    │                                                        │
    │  流式内容                                              │
    ├──────────────────────────────────────────────────────►│
    │  ws_manager.send_to_task_subscribers(task_id, chunk)  │
    │                                                        │
    ▼                                                        ▼
WebSocket 推送给所有订阅了该 task 的连接                     客户端
    │                                                        │
    ▼                                                        ▼
{ "type": "chat_chunk", "task_id": "...", "text": "..." }   实时显示


【图片生成状态推送】

用户请求生成图片
    │
    ▼
POST /images/generate
    │
    ▼
创建 task + 调用 kie.ai
    │
    ▼
后台轮询 kie.ai 任务状态
    │
    │  状态变化时
    ├──────────────────────────────────────────────────────►
    │  ws_manager.send_to_user(user_id, status_update)
    │
    ▼
{ "type": "task_status", "task_id": "...", "status": "completed", "urls": [...] }
```

---

## 三、消息协议设计

### 3.1 消息格式

```typescript
// 通用消息格式
interface WSMessage {
  type: WSMessageType;
  payload: Record<string, any>;
  timestamp: number;           // Unix timestamp (ms)
  task_id?: string;            // 关联的任务 ID
  conversation_id?: string;    // 关联的对话 ID
  message_index?: number;      // 消息索引（用于断点续传）
}

// 消息类型枚举
type WSMessageType =
  // === 聊天相关 ===
  | 'chat_start'           // AI 开始生成
  | 'chat_chunk'           // 流式内容块
  | 'chat_done'            // 生成完成
  | 'chat_error'           // 生成失败

  // === 任务相关 ===
  | 'task_status'          // 图片/视频任务状态更新
  | 'task_progress'        // 任务进度（可选）

  // === 通知相关 ===
  | 'credits_changed'      // 积分变化
  | 'notification'         // 通用通知

  // === 连接相关 ===
  | 'ping'                 // 心跳请求
  | 'pong'                 // 心跳响应
  | 'subscribe'            // 订阅任务
  | 'unsubscribe'          // 取消订阅
  | 'subscribed'           // 订阅成功确认
  | 'error';               // 错误消息
```

### 3.2 具体消息示例

#### 聊天流式消息

```json
// AI 开始生成
{
  "type": "chat_start",
  "payload": {
    "model": "gemini-3-pro",
    "assistant_message_id": "msg_abc123"
  },
  "task_id": "task_001",
  "conversation_id": "conv_001",
  "timestamp": 1706000000000
}

// 流式内容块
{
  "type": "chat_chunk",
  "payload": {
    "text": "你好",
    "accumulated": "你好"
  },
  "task_id": "task_001",
  "message_index": 0,
  "timestamp": 1706000000100
}

{
  "type": "chat_chunk",
  "payload": {
    "text": "！我是",
    "accumulated": "你好！我是"
  },
  "task_id": "task_001",
  "message_index": 1,
  "timestamp": 1706000000200
}

// 生成完成
{
  "type": "chat_done",
  "payload": {
    "message_id": "msg_abc123",
    "content": "你好！我是 AI 助手。",
    "credits_consumed": 5,
    "model": "gemini-3-pro",
    "usage": {
      "input_tokens": 100,
      "output_tokens": 50
    }
  },
  "task_id": "task_001",
  "conversation_id": "conv_001",
  "timestamp": 1706000001000
}
```

#### 任务状态消息

```json
// 图片生成完成
{
  "type": "task_status",
  "payload": {
    "status": "completed",
    "media_type": "image",
    "urls": [
      "https://cdn.example.com/img1.png",
      "https://cdn.example.com/img2.png"
    ],
    "credits_consumed": 10
  },
  "task_id": "img_task_001",
  "conversation_id": "conv_001",
  "timestamp": 1706000010000
}

// 视频生成进度
{
  "type": "task_progress",
  "payload": {
    "status": "generating",
    "progress": 45,
    "message": "正在生成视频..."
  },
  "task_id": "video_task_001",
  "timestamp": 1706000020000
}
```

#### 积分变化消息

```json
{
  "type": "credits_changed",
  "payload": {
    "credits": 950,
    "delta": -10,
    "reason": "image_generation",
    "task_id": "img_task_001"
  },
  "timestamp": 1706000010000
}
```

#### 客户端发送的消息

```json
// 订阅任务
{
  "type": "subscribe",
  "payload": {
    "task_id": "task_001",
    "last_index": 5
  }
}

// 取消订阅
{
  "type": "unsubscribe",
  "payload": {
    "task_id": "task_001"
  }
}

// 心跳
{
  "type": "ping",
  "payload": {}
}
```

---

## 四、后端实现设计

### 4.1 文件结构

```
backend/
├── services/
│   ├── websocket_manager.py      # 【新增】WebSocket 连接管理器
│   ├── chat_stream_manager.py    # 【改造】输出从 SSE 改为 WS
│   ├── image_service.py          # 【改造】完成时调用 WS 推送
│   ├── video_service.py          # 【改造】完成时调用 WS 推送
│   └── credit_service.py         # 【改造】变化时调用 WS 推送
├── api/
│   └── routes/
│       ├── ws.py                 # 【新增】WebSocket 端点
│       └── ...
└── schemas/
    └── websocket.py              # 【新增】WebSocket 消息模型
```

### 4.2 WebSocketManager 核心实现

```python
# backend/services/websocket_manager.py

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
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Set, Optional, Any, List
from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger


# 心跳间隔（秒）
HEARTBEAT_INTERVAL = 30
# 心跳超时（秒）
HEARTBEAT_TIMEOUT = 60
# 消息缓冲区大小（用于断点续传）
MAX_BUFFER_SIZE = 500


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
    """任务消息缓冲区（用于断点续传）"""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    accumulated_content: str = ""


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
            # 添加到用户连接池
            if user_id not in self._connections:
                self._connections[user_id] = {}
            self._connections[user_id][conn_id] = connection

            # 添加到快速索引
            self._conn_index[conn_id] = connection

        logger.info(f"WebSocket connected | user={user_id} | conn={conn_id}")
        return conn_id

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
                start_index = max(0, last_index + 1)
                missed_messages = buffer.messages[start_index:] if start_index < len(buffer.messages) else []
                return {
                    "accumulated": buffer.accumulated_content,
                    "missed_messages": missed_messages,
                    "current_index": len(buffer.messages) - 1
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
    ):
        """
        发送消息到任务的所有订阅者

        Args:
            task_id: 任务 ID
            message: 消息内容
            buffer: 是否缓存消息（用于断点续传）
        """
        # 缓存消息
        if buffer:
            async with self._lock:
                if task_id not in self._task_buffers:
                    self._task_buffers[task_id] = TaskBuffer()

                task_buffer = self._task_buffers[task_id]

                # 添加消息索引
                message["message_index"] = len(task_buffer.messages)
                task_buffer.messages.append(message)

                # 更新累积内容
                if message.get("type") == "chat_chunk":
                    task_buffer.accumulated_content += message.get("payload", {}).get("text", "")

                # 限制缓冲区大小
                if len(task_buffer.messages) > MAX_BUFFER_SIZE:
                    task_buffer.messages = task_buffer.messages[-MAX_BUFFER_SIZE:]

        # 发送给所有订阅者
        subscribers = self._task_subscribers.get(task_id, set())
        for conn_id in list(subscribers):
            await self.send_to_connection(conn_id, message)

    async def broadcast_all(self, message: Dict[str, Any]):
        """广播消息到所有连接"""
        for conn_id in list(self._conn_index.keys()):
            await self.send_to_connection(conn_id, message)

    async def clear_task_buffer(self, task_id: str):
        """清理任务缓冲区"""
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

    def get_connection_count(self) -> int:
        """获取当前连接数"""
        return len(self._conn_index)

    def get_user_connection_count(self, user_id: str) -> int:
        """获取用户连接数"""
        return len(self._connections.get(user_id, {}))


# 全局单例
ws_manager = WebSocketManager()
```

### 4.3 WebSocket 端点实现

```python
# backend/api/routes/ws.py

"""
WebSocket 端点

功能:
- 认证（token 验证）
- 消息路由
- 心跳处理
- 错误处理
"""

import asyncio
import json
import time
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from loguru import logger

from services.websocket_manager import ws_manager, HEARTBEAT_INTERVAL
from services.auth_service import verify_token
from services.chat_stream_manager import chat_stream_manager

router = APIRouter()


async def get_user_from_token(token: str) -> Optional[str]:
    """从 token 获取用户 ID"""
    try:
        payload = verify_token(token)
        return payload.get("sub")  # user_id
    except Exception:
        return None


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(..., description="认证 token")
):
    """
    WebSocket 主端点

    连接流程:
    1. 验证 token
    2. 注册连接
    3. 启动心跳任务
    4. 消息循环
    """
    # 1. 认证
    user_id = await get_user_from_token(token)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 2. 注册连接
    conn_id = await ws_manager.connect(websocket, user_id)

    # 3. 启动心跳任务
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(conn_id, websocket)
    )

    try:
        # 4. 消息循环
        while True:
            try:
                data = await websocket.receive_json()
                await _handle_message(conn_id, user_id, data)
            except json.JSONDecodeError:
                await ws_manager.send_to_connection(conn_id, {
                    "type": "error",
                    "payload": {"message": "Invalid JSON"}
                })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected normally | conn={conn_id}")
    except Exception as e:
        logger.error(f"WebSocket error | conn={conn_id} | error={e}")
    finally:
        heartbeat_task.cancel()
        await ws_manager.disconnect(conn_id)


async def _heartbeat_loop(conn_id: str, websocket: WebSocket):
    """心跳循环"""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json({
                    "type": "ping",
                    "payload": {},
                    "timestamp": int(time.time() * 1000)
                })
            except Exception:
                break
    except asyncio.CancelledError:
        pass


async def _handle_message(conn_id: str, user_id: str, data: dict):
    """处理客户端消息"""
    msg_type = data.get("type")
    payload = data.get("payload", {})

    if msg_type == "pong":
        # 心跳响应
        await ws_manager.update_heartbeat(conn_id)

    elif msg_type == "subscribe":
        # 订阅任务
        task_id = payload.get("task_id")
        last_index = payload.get("last_index", -1)

        if task_id:
            result = await ws_manager.subscribe_task(conn_id, task_id, last_index)

            # 发送订阅确认
            await ws_manager.send_to_connection(conn_id, {
                "type": "subscribed",
                "payload": {
                    "task_id": task_id,
                    "accumulated": result.get("accumulated", "") if result else "",
                    "current_index": result.get("current_index", -1) if result else -1
                },
                "timestamp": int(time.time() * 1000)
            })

            # 补发错过的消息
            if result and result.get("missed_messages"):
                for msg in result["missed_messages"]:
                    await ws_manager.send_to_connection(conn_id, msg)

    elif msg_type == "unsubscribe":
        # 取消订阅
        task_id = payload.get("task_id")
        if task_id:
            await ws_manager.unsubscribe_task(conn_id, task_id)

    else:
        logger.warning(f"Unknown message type | conn={conn_id} | type={msg_type}")
```

### 4.4 ChatStreamManager 改造

```python
# backend/services/chat_stream_manager.py

# 在现有的 ChatStreamManager 中添加 WebSocket 广播

from services.websocket_manager import ws_manager

class ChatStreamManager:
    """聊天流管理器 - 改造为 WebSocket 广播"""

    # ... 保留现有代码 ...

    async def _broadcast_chunk(self, task_id: str, text: str, accumulated: str):
        """广播流式内容块"""
        message = {
            "type": "chat_chunk",
            "payload": {
                "text": text,
                "accumulated": accumulated
            },
            "task_id": task_id,
            "timestamp": int(time.time() * 1000)
        }
        await ws_manager.send_to_task_subscribers(task_id, message)

    async def _broadcast_done(
        self,
        task_id: str,
        conversation_id: str,
        message_id: str,
        content: str,
        credits_consumed: int,
        model: str,
        usage: dict
    ):
        """广播生成完成"""
        message = {
            "type": "chat_done",
            "payload": {
                "message_id": message_id,
                "content": content,
                "credits_consumed": credits_consumed,
                "model": model,
                "usage": usage
            },
            "task_id": task_id,
            "conversation_id": conversation_id,
            "timestamp": int(time.time() * 1000)
        }
        await ws_manager.send_to_task_subscribers(task_id, message, buffer=False)

        # 清理任务缓冲区
        await ws_manager.clear_task_buffer(task_id)

    async def _broadcast_error(self, task_id: str, error: str):
        """广播错误"""
        message = {
            "type": "chat_error",
            "payload": {
                "error": error
            },
            "task_id": task_id,
            "timestamp": int(time.time() * 1000)
        }
        await ws_manager.send_to_task_subscribers(task_id, message, buffer=False)

        # 清理任务缓冲区
        await ws_manager.clear_task_buffer(task_id)
```

### 4.5 图片/视频服务集成

```python
# backend/services/image_service.py (示例改造)

from services.websocket_manager import ws_manager

class ImageService:

    async def _on_task_completed(
        self,
        task_id: str,
        user_id: str,
        conversation_id: str,
        status: str,
        urls: list,
        credits_consumed: int
    ):
        """任务完成时推送"""
        message = {
            "type": "task_status",
            "payload": {
                "status": status,
                "media_type": "image",
                "urls": urls,
                "credits_consumed": credits_consumed
            },
            "task_id": task_id,
            "conversation_id": conversation_id,
            "timestamp": int(time.time() * 1000)
        }
        await ws_manager.send_to_user(user_id, message)
```

---

## 五、前端实现设计

### 5.1 文件结构

```
frontend/src/
├── contexts/
│   └── WebSocketContext.tsx      # 【新增】WebSocket Context
├── hooks/
│   └── useWebSocket.ts           # 【新增】WebSocket Hook
├── services/
│   └── websocket.ts              # 【新增】WebSocket 服务
├── utils/
│   ├── taskRestoration.ts        # 【改造】移除 SSE，使用 WS
│   └── polling.ts                # 【移除】不再需要轮询
└── stores/
    └── useTaskStore.ts           # 【改造】使用 WS 事件更新状态
```

### 5.2 WebSocket Hook 实现

```typescript
// frontend/src/hooks/useWebSocket.ts

/**
 * WebSocket Hook
 *
 * 参考实现:
 * - https://github.com/robtaussig/react-use-websocket
 *
 * 功能:
 * - 自动连接/重连
 * - 心跳保活
 * - 消息订阅
 * - 断点续传支持
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { useAuthStore } from '../stores/useAuthStore';
import { logger } from '../utils/logger';

// 配置常量
const WS_URL = import.meta.env.VITE_WS_URL || 'wss://api.example.com/ws';
const HEARTBEAT_INTERVAL = 30000;  // 30秒
const RECONNECT_INTERVAL_BASE = 1000;  // 基础重连间隔
const RECONNECT_INTERVAL_MAX = 30000;  // 最大重连间隔
const MAX_RECONNECT_ATTEMPTS = 20;

// 消息类型
export type WSMessageType =
  | 'chat_start'
  | 'chat_chunk'
  | 'chat_done'
  | 'chat_error'
  | 'task_status'
  | 'task_progress'
  | 'credits_changed'
  | 'notification'
  | 'ping'
  | 'pong'
  | 'subscribe'
  | 'unsubscribe'
  | 'subscribed'
  | 'error';

export interface WSMessage {
  type: WSMessageType;
  payload: Record<string, any>;
  timestamp: number;
  task_id?: string;
  conversation_id?: string;
  message_index?: number;
}

// 连接状态
export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

// 订阅回调
type MessageHandler = (message: WSMessage) => void;

interface UseWebSocketReturn {
  // 状态
  connectionState: ConnectionState;
  isConnected: boolean;

  // 方法
  subscribe: (type: WSMessageType, handler: MessageHandler) => () => void;
  subscribeTask: (taskId: string, lastIndex?: number) => void;
  unsubscribeTask: (taskId: string) => void;
  send: (message: Omit<WSMessage, 'timestamp'>) => void;
}

export function useWebSocket(): UseWebSocketReturn {
  const { token, isAuthenticated } = useAuthStore();
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Map<WSMessageType, Set<MessageHandler>>>(new Map());
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const heartbeatIntervalRef = useRef<NodeJS.Timeout | null>(null);

  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');

  // 清理函数
  const cleanup = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  // 分发消息给订阅者
  const dispatchMessage = useCallback((message: WSMessage) => {
    const handlers = handlersRef.current.get(message.type);
    if (handlers) {
      handlers.forEach(handler => {
        try {
          handler(message);
        } catch (error) {
          logger.error('WebSocket handler error:', error);
        }
      });
    }
  }, []);

  // 启动心跳
  const startHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
    }

    heartbeatIntervalRef.current = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: 'pong',
          payload: {},
          timestamp: Date.now()
        }));
      }
    }, HEARTBEAT_INTERVAL);
  }, []);

  // 计算重连延迟（指数退避）
  const getReconnectDelay = useCallback(() => {
    const delay = Math.min(
      RECONNECT_INTERVAL_BASE * Math.pow(2, reconnectAttemptsRef.current),
      RECONNECT_INTERVAL_MAX
    );
    return delay;
  }, []);

  // 连接 WebSocket
  const connect = useCallback(() => {
    if (!token || !isAuthenticated) {
      logger.info('WebSocket: Not authenticated, skip connection');
      return;
    }

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    cleanup();
    setConnectionState('connecting');

    const url = `${WS_URL}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      logger.info('WebSocket connected');
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
      startHeartbeat();
    };

    ws.onclose = (event) => {
      logger.info(`WebSocket closed: ${event.code} ${event.reason}`);
      setConnectionState('disconnected');

      // 非正常关闭，尝试重连
      if (event.code !== 1000 && reconnectAttemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
        setConnectionState('reconnecting');
        const delay = getReconnectDelay();
        reconnectAttemptsRef.current++;
        logger.info(`WebSocket reconnecting in ${delay}ms (attempt ${reconnectAttemptsRef.current})`);

        reconnectTimeoutRef.current = setTimeout(() => {
          connect();
        }, delay);
      }
    };

    ws.onerror = (error) => {
      logger.error('WebSocket error:', error);
    };

    ws.onmessage = (event) => {
      try {
        const message: WSMessage = JSON.parse(event.data);

        // 处理心跳
        if (message.type === 'ping') {
          ws.send(JSON.stringify({
            type: 'pong',
            payload: {},
            timestamp: Date.now()
          }));
          return;
        }

        // 分发消息
        dispatchMessage(message);
      } catch (error) {
        logger.error('WebSocket message parse error:', error);
      }
    };
  }, [token, isAuthenticated, cleanup, startHeartbeat, getReconnectDelay, dispatchMessage]);

  // 订阅消息类型
  const subscribe = useCallback((type: WSMessageType, handler: MessageHandler) => {
    if (!handlersRef.current.has(type)) {
      handlersRef.current.set(type, new Set());
    }
    handlersRef.current.get(type)!.add(handler);

    // 返回取消订阅函数
    return () => {
      handlersRef.current.get(type)?.delete(handler);
    };
  }, []);

  // 订阅任务
  const subscribeTask = useCallback((taskId: string, lastIndex: number = -1) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'subscribe',
        payload: { task_id: taskId, last_index: lastIndex },
        timestamp: Date.now()
      }));
    }
  }, []);

  // 取消订阅任务
  const unsubscribeTask = useCallback((taskId: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'unsubscribe',
        payload: { task_id: taskId },
        timestamp: Date.now()
      }));
    }
  }, []);

  // 发送消息
  const send = useCallback((message: Omit<WSMessage, 'timestamp'>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        ...message,
        timestamp: Date.now()
      }));
    }
  }, []);

  // 自动连接
  useEffect(() => {
    connect();
    return cleanup;
  }, [connect, cleanup]);

  return {
    connectionState,
    isConnected: connectionState === 'connected',
    subscribe,
    subscribeTask,
    unsubscribeTask,
    send
  };
}
```

### 5.3 WebSocket Context

```typescript
// frontend/src/contexts/WebSocketContext.tsx

/**
 * WebSocket Context
 *
 * 提供全局 WebSocket 连接，避免重复连接
 */

import React, { createContext, useContext, useEffect, ReactNode } from 'react';
import { useWebSocket, WSMessage, WSMessageType, ConnectionState } from '../hooks/useWebSocket';
import { useAuthStore } from '../stores/useAuthStore';
import { useChatStore } from '../stores/useChatStore';
import { useTaskStore } from '../stores/useTaskStore';
import { logger } from '../utils/logger';

interface WebSocketContextValue {
  connectionState: ConnectionState;
  isConnected: boolean;
  subscribe: (type: WSMessageType, handler: (msg: WSMessage) => void) => () => void;
  subscribeTask: (taskId: string, lastIndex?: number) => void;
  unsubscribeTask: (taskId: string) => void;
  send: (message: Omit<WSMessage, 'timestamp'>) => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const ws = useWebSocket();

  // 设置全局消息处理
  useEffect(() => {
    // 处理聊天流式消息
    const unsubChat = ws.subscribe('chat_chunk', (msg) => {
      useChatStore.getState().updateStreamingContent(
        msg.task_id!,
        msg.payload.text
      );
    });

    const unsubChatDone = ws.subscribe('chat_done', (msg) => {
      useChatStore.getState().finalizeMessage(
        msg.task_id!,
        msg.payload
      );
    });

    const unsubChatError = ws.subscribe('chat_error', (msg) => {
      useChatStore.getState().handleError(
        msg.task_id!,
        msg.payload.error
      );
    });

    // 处理任务状态更新
    const unsubTaskStatus = ws.subscribe('task_status', (msg) => {
      useTaskStore.getState().updateTaskStatus(
        msg.task_id!,
        msg.payload
      );
    });

    // 处理积分变化
    const unsubCredits = ws.subscribe('credits_changed', (msg) => {
      useAuthStore.getState().updateCredits(msg.payload.credits);
    });

    return () => {
      unsubChat();
      unsubChatDone();
      unsubChatError();
      unsubTaskStatus();
      unsubCredits();
    };
  }, [ws]);

  return (
    <WebSocketContext.Provider value={ws}>
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocketContext(): WebSocketContextValue {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocketContext must be used within WebSocketProvider');
  }
  return context;
}
```

### 5.4 任务恢复改造

```typescript
// frontend/src/utils/taskRestoration.ts (改造版)

/**
 * 任务恢复工具（WebSocket 版）
 *
 * 改造点:
 * - 移除 SSE 逻辑
 * - 使用 WebSocket 订阅替代
 */

import { useTaskStore } from '../stores/useTaskStore';
import { useChatStore } from '../stores/useChatStore';
import api from '../services/api';
import { logger } from './logger';
import { tabSync } from './tabSync';

interface PendingTask {
  id: string;
  external_task_id: string;
  conversation_id: string;
  type: 'image' | 'video' | 'chat';
  status: string;
  accumulated_content?: string | null;
}

// 被其他标签页恢复的任务
const restoredByOtherTabs = new Set<string>();

tabSync.on('task_restored', (payload) => {
  if (payload.taskId) {
    restoredByOtherTabs.add(payload.taskId as string);
  }
});

interface WebSocketLike {
  subscribeTask: (taskId: string, lastIndex?: number) => void;
}

export async function restorePendingTasks(ws: WebSocketLike) {
  logger.info('[TaskRestore] Starting restoration (WebSocket version)');

  try {
    // 1. 获取未完成任务列表
    const response = await api.get<{ tasks: PendingTask[]; count: number }>('/tasks/pending');
    const tasks = response.data.tasks || [];

    logger.info(`[TaskRestore] Found ${tasks.length} pending tasks`);

    for (const task of tasks) {
      // 检查是否已被其他标签页恢复
      if (restoredByOtherTabs.has(task.external_task_id)) {
        logger.info(`[TaskRestore] Task ${task.external_task_id} already restored by another tab`);
        continue;
      }

      // 广播恢复通知（防止其他标签页重复恢复）
      tabSync.broadcast('task_restored', {
        taskId: task.external_task_id,
        conversationId: task.conversation_id
      });

      if (task.type === 'chat') {
        await restoreChatTask(ws, task);
      } else {
        await restoreMediaTask(ws, task);
      }
    }
  } catch (error) {
    logger.error('[TaskRestore] Failed to restore tasks:', error);
  }
}

async function restoreChatTask(ws: WebSocketLike, task: PendingTask) {
  logger.info(`[TaskRestore] Restoring chat task: ${task.external_task_id}`);

  // 1. 如果有累积内容，先显示
  if (task.accumulated_content) {
    useChatStore.getState().createStreamingMessage(
      task.conversation_id,
      task.external_task_id,
      task.accumulated_content
    );
  }

  // 2. 订阅任务（WebSocket 会自动补发错过的消息）
  ws.subscribeTask(task.external_task_id, -1);
}

async function restoreMediaTask(ws: WebSocketLike, task: PendingTask) {
  logger.info(`[TaskRestore] Restoring ${task.type} task: ${task.external_task_id}`);

  // 1. 添加到任务列表
  useTaskStore.getState().addTask({
    id: task.external_task_id,
    type: task.type,
    status: task.status,
    conversationId: task.conversation_id
  });

  // 图片/视频任务通过 user 级别推送，不需要单独订阅
}
```

---

## 六、改动范围汇总

### 6.1 后端改动

| 文件 | 操作 | 说明 |
|------|------|------|
| `services/websocket_manager.py` | **新增** | WebSocket 连接管理器 |
| `api/routes/ws.py` | **新增** | WebSocket 端点 |
| `schemas/websocket.py` | **新增** | 消息模型定义 |
| `services/chat_stream_manager.py` | **改造** | 添加 WS 广播方法，保留 SSE 兼容 |
| `services/image_service.py` | **改造** | 完成时调用 WS 推送 |
| `services/video_service.py` | **改造** | 完成时调用 WS 推送 |
| `services/credit_service.py` | **改造** | 变化时调用 WS 推送 |
| `main.py` | **改造** | 注册 WS 路由，启动清理任务 |

### 6.2 前端改动

| 文件 | 操作 | 说明 |
|------|------|------|
| `contexts/WebSocketContext.tsx` | **新增** | WebSocket Context Provider |
| `hooks/useWebSocket.ts` | **新增** | WebSocket Hook |
| `services/websocket.ts` | **新增** | WebSocket 服务层（可选） |
| `utils/taskRestoration.ts` | **改造** | 移除 SSE，使用 WS 订阅 |
| `utils/polling.ts` | **移除** | 不再需要轮询 |
| `stores/useTaskStore.ts` | **改造** | 添加 WS 事件处理方法 |
| `stores/useChatStore.ts` | **改造** | 添加 WS 流式消息处理 |
| `App.tsx` | **改造** | 包装 WebSocketProvider |

---

## 七、实施计划

### 7.1 阶段划分

| 阶段 | 内容 | 产出 |
|------|------|------|
| **阶段 1** | 后端 WebSocket 基础设施 | `websocket_manager.py`, `ws.py` |
| **阶段 2** | 聊天流式改造 | `chat_stream_manager.py` 改造 |
| **阶段 3** | 前端 WebSocket Hook | `useWebSocket.ts`, `WebSocketContext.tsx` |
| **阶段 4** | 任务恢复改造 | `taskRestoration.ts` 改造 |
| **阶段 5** | 图片/视频服务集成 | 服务层推送集成 |
| **阶段 6** | 测试与优化 | 压力测试、边界测试 |

### 7.2 测试要点

| 场景 | 测试内容 |
|------|---------|
| **连接管理** | 建立连接、断开重连、多设备同时在线 |
| **心跳保活** | 心跳正常、心跳超时、网关超时 |
| **聊天流式** | 正常流式、刷新恢复、断点续传 |
| **任务推送** | 图片完成推送、视频完成推送、积分变化 |
| **多任务并发** | 多对话多任务同时进行、刷新恢复所有任务 |
| **边界情况** | 网络波动、服务端重启、客户端异常关闭 |

---

## 八、风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| Nginx 不支持 WS | 连接失败 | 配置 `proxy_pass` + `Upgrade` 头 |
| 连接数过多 | 内存压力 | 定期清理超时连接，限制单用户连接数 |
| 消息丢失 | 数据不一致 | 消息缓冲 + 断点续传 + DB fallback |
| 浏览器兼容性 | 部分用户无法使用 | 保留 SSE 降级方案（可选） |
| **多实例部署** | 跨实例消息丢失 | Redis Pub/Sub 消息分发（见 8.1） |
| **Buffer 内存溢出** | 服务内存压力 | 定期清理 + expire_at 机制（见 8.2） |
| **订阅竞态条件** | 消息丢失 | API 返回 last_index，客户端带上（见 8.3） |
| **惊群效应** | 服务器重启时瞬时压力 | server_restarting 消息 + Jitter 重连（见 8.4） |

### 8.1 多实例分布式方案（扩展部署时启用）

当部署多个后端实例时，用户 A 可能连接在实例 1，而图片生成任务在实例 2 完成。

**解决方案**：引入 Redis Pub/Sub

```python
# services/websocket_manager.py - 分布式扩展

import aioredis

class DistributedWebSocketManager(WebSocketManager):
    """支持分布式部署的 WebSocket 管理器"""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        super().__init__()
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub_task: Optional[asyncio.Task] = None

    async def initialize(self):
        """初始化 Redis 连接并启动订阅"""
        self._redis = await aioredis.from_url(self._redis_url)
        self._pubsub_task = asyncio.create_task(self._subscribe_redis())

    async def _subscribe_redis(self):
        """订阅 Redis 频道，接收其他实例的消息"""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("ws:broadcast")

        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                await self._handle_redis_message(data)

    async def _handle_redis_message(self, data: dict):
        """处理 Redis 消息，检查本地是否有对应连接"""
        target_user = data.get("user_id")
        target_task = data.get("task_id")
        message = data.get("message")

        if target_user and target_user in self._connections:
            await super().send_to_user(target_user, message)
        elif target_task and target_task in self._task_subscribers:
            await super().send_to_task_subscribers(target_task, message, buffer=False)

    async def send_to_user(self, user_id: str, message: dict):
        """发送消息到用户（本地 + Redis 广播）"""
        # 先尝试本地发送
        await super().send_to_user(user_id, message)

        # 同时广播到 Redis（其他实例可能有该用户的连接）
        if self._redis:
            await self._redis.publish("ws:broadcast", json.dumps({
                "user_id": user_id,
                "message": message
            }))
```

### 8.2 Buffer 内存管理

任务缓冲区需要定期清理，防止内存无限增长。

```python
# services/websocket_manager.py - 增强 TaskBuffer

from dataclasses import dataclass, field
import time

@dataclass
class TaskBuffer:
    """任务消息缓冲区（增加过期机制）"""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    accumulated_content: str = ""
    created_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    expire_at: Optional[float] = None  # 任务完成后设置过期时间


class WebSocketManager:
    # ... 其他代码 ...

    async def clear_task_buffer(self, task_id: str, delay_seconds: int = 300):
        """标记任务缓冲区为过期（延迟 5 分钟后实际删除）"""
        async with self._lock:
            if task_id in self._task_buffers:
                self._task_buffers[task_id].expire_at = time.time() + delay_seconds

    async def _periodically_cleanup_buffers(self):
        """定期清理过期的任务缓冲区（每 10 分钟执行一次）"""
        while True:
            await asyncio.sleep(600)
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
```

### 8.3 订阅竞态条件处理

问题：客户端调用 `/api/tasks/pending` 获取任务列表后，在建立 WebSocket 订阅前可能丢失消息。

**后端 API 返回 last_index**：

```python
# api/routes/tasks.py

@router.get("/tasks/pending")
async def get_pending_tasks(user: User = Depends(get_current_user)):
    tasks = await task_service.get_pending_tasks(user.id)

    # 从 WebSocket 管理器获取每个任务的当前消息索引
    for task in tasks:
        buffer = ws_manager._task_buffers.get(task.external_task_id)
        task.last_index = len(buffer.messages) - 1 if buffer else -1
        task.accumulated_content = buffer.accumulated_content if buffer else None

    return {"tasks": tasks, "count": len(tasks)}
```

**前端订阅时带上 last_index**：

```typescript
// utils/taskRestoration.ts

async function restoreChatTask(ws: WebSocketLike, task: PendingTask) {
  // 使用 API 返回的 last_index
  ws.subscribeTask(task.external_task_id, task.last_index ?? -1);
}
```

### 8.4 优雅关闭与惊群效应防护

**后端：发送重启通知**

```python
# main.py

@app.on_event("shutdown")
async def shutdown_event():
    """优雅关闭：通知所有客户端服务即将重启"""
    await ws_manager.broadcast_all({
        "type": "server_restarting",
        "payload": {"message": "Server is restarting, please reconnect"},
        "timestamp": int(time.time() * 1000)
    })
    # 给客户端一点时间接收消息
    await asyncio.sleep(1)
```

**前端：Jitter 随机延迟重连**

```typescript
// hooks/useWebSocket.ts

// 接收到 server_restarting 消息时的处理
const handleServerRestart = useCallback(() => {
  // 增加随机抖动（0-5秒），错开重连峰值
  const jitter = Math.random() * 5000;
  reconnectAttemptsRef.current = 0;  // 重置重连计数

  setTimeout(() => {
    connect();
  }, jitter);
}, [connect]);

// 在消息处理中
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);

  if (message.type === 'server_restarting') {
    cleanup();
    handleServerRestart();
    return;
  }
  // ... 其他处理
};
```

---

## 九、配置参考

### 9.1 Nginx 配置

```nginx
location /ws {
    proxy_pass http://backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 86400;  # 24小时
}
```

### 9.2 环境变量

```bash
# 后端
WS_HEARTBEAT_INTERVAL=30
WS_HEARTBEAT_TIMEOUT=60
WS_MAX_BUFFER_SIZE_BYTES=1048576     # 1MB（替代固定条数）
WS_BUFFER_MAX_AGE_SECONDS=300        # 5 分钟（时间维度清理）
WS_MAX_CONNECTIONS_PER_USER=5

# 前端
VITE_WS_URL=wss://api.example.com/ws
```

---

## 十、参考资料

### 10.1 官方文档

- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
- [MDN WebSocket API](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)

### 10.2 开源项目

- [fastapi-distributed-websocket](https://github.com/DontPanicO/fastapi-distributed-websocket)
- [redis-streams-fastapi-chat](https://github.com/leonh/redis-streams-fastapi-chat)
- [react-use-websocket](https://github.com/robtaussig/react-use-websocket)
- [encode/broadcaster](https://github.com/encode/broadcaster)

### 10.3 技术文章

- [Scaling WebSockets with PUB/SUB using Python, Redis & FastAPI](https://medium.com/@nandagopal05/scaling-websockets-with-pub-sub-using-python-redis-fastapi-b16392ffe291)
- [Top Ten Advanced Techniques for Scaling WebSocket Applications with FastAPI](https://hexshift.medium.com/top-ten-advanced-techniques-for-scaling-websocket-applications-with-fastapi-a5af1e5e901f)
- [The complete guide to WebSockets with React](https://ably.com/blog/websockets-react-tutorial)

---

## 十一、生产优化补充

### 11.1 前端 Token 刷新处理

当用户的 Access Token 刷新时，需要断开旧连接并使用新 Token 建立新连接。

```typescript
// hooks/useWebSocket.ts - Token 变化监听

export function useWebSocket(): UseWebSocketReturn {
  const { token, isAuthenticated } = useAuthStore();
  // ... 其他代码 ...

  // 监听 token 变化
  useEffect(() => {
    if (token && isAuthenticated) {
      // Token 有效，尝试连接
      connect();
    } else {
      // Token 消失（登出或过期），立即断开
      cleanup();
      setConnectionState('disconnected');
    }
  }, [token, isAuthenticated, connect, cleanup]);

  // 登出时的清理
  useEffect(() => {
    const unsubscribe = useAuthStore.subscribe(
      (state) => state.isAuthenticated,
      (isAuth) => {
        if (!isAuth) {
          cleanup();
        }
      }
    );
    return unsubscribe;
  }, [cleanup]);

  // ...
}
```

### 11.2 消息顺序性保证

虽然 WebSocket 基于 TCP 保证顺序，但在前端异步分发时仍需校验。

```typescript
// stores/useChatStore.ts - 按 index 排序/插入

interface StreamingMessage {
  taskId: string;
  chunks: Array<{ index: number; text: string }>;
  lastIndex: number;
}

const useChatStore = create<ChatState>((set, get) => ({
  streamingMessages: new Map<string, StreamingMessage>(),

  updateStreamingContent: (taskId: string, text: string, index: number) => {
    set((state) => {
      const streaming = state.streamingMessages.get(taskId) || {
        taskId,
        chunks: [],
        lastIndex: -1
      };

      // 检查是否乱序
      if (index <= streaming.lastIndex) {
        // 已处理过，跳过
        return state;
      }

      // 检查是否有 gap（丢包）
      if (index > streaming.lastIndex + 1) {
        console.warn(`[Chat] Gap detected: expected ${streaming.lastIndex + 1}, got ${index}`);
        // 可以选择等待或请求重发
      }

      streaming.chunks.push({ index, text });
      streaming.lastIndex = index;

      // 按 index 排序后拼接
      const sortedChunks = [...streaming.chunks].sort((a, b) => a.index - b.index);
      const content = sortedChunks.map(c => c.text).join('');

      state.streamingMessages.set(taskId, streaming);
      // 更新显示内容...
      return { ...state };
    });
  }
}));
```

### 11.3 连接状态 UI 指示

建议在前端添加连接状态指示器，让用户知道当前连接状态。

```typescript
// components/ConnectionIndicator.tsx

export function ConnectionIndicator() {
  const { connectionState } = useWebSocketContext();

  const statusConfig = {
    connected: { color: 'green', text: '' },  // 正常不显示
    connecting: { color: 'yellow', text: '连接中...' },
    reconnecting: { color: 'orange', text: '重新连接中...' },
    disconnected: { color: 'red', text: '连接已断开' }
  };

  const config = statusConfig[connectionState];

  if (connectionState === 'connected') {
    return null;  // 正常状态不显示
  }

  return (
    <div className={`connection-indicator ${connectionState}`}>
      <span className="dot" style={{ backgroundColor: config.color }} />
      <span>{config.text}</span>
    </div>
  );
}
```

### 11.4 实施优先级

| 优化项 | 优先级 | 阶段 | 备注 |
|--------|--------|------|------|
| Buffer 定期清理 | **必须** | 阶段 1 | 防止内存溢出 |
| Token 刷新处理 | **必须** | 阶段 3 | 登出/刷新场景 |
| 订阅竞态处理 | **必须** | 阶段 4 | 断点续传必需 |
| 消息顺序校验 | 建议 | 阶段 3 | 生产环境兜底 |
| 优雅关闭 + Jitter | 建议 | 阶段 6 | 运维友好 |
| Redis Pub/Sub | 可选 | 扩展 | 多实例部署时启用 |
| 连接状态指示 | 可选 | 阶段 3 | UX 优化 |

### 11.5 细节优化（实施必须）

#### 11.5.1 缓冲区按字节大小 + 时间维度清理

**问题**：固定 500 条消息的缓冲区对于长文本/Reasoning 模型可能在 30-60 秒内耗尽。

**解决方案**：改为按字节大小 + 时间跨度双维度控制

```python
# services/websocket_manager.py

MAX_BUFFER_SIZE_BYTES = 1 * 1024 * 1024  # 1MB
BUFFER_MAX_AGE_SECONDS = 300  # 5 分钟

@dataclass
class TaskBuffer:
    """任务消息缓冲区（字节 + 时间双维度）"""
    messages: List[Tuple[float, str]] = field(default_factory=list)  # (timestamp, msg)
    accumulated_content: str = ""
    total_bytes: int = 0
    created_at: float = field(default_factory=time.time)
    last_update: float = field(default_factory=time.time)
    expire_at: Optional[float] = None

    def add_message(self, msg: str):
        """添加消息，自动清理超限内容"""
        now = time.time()
        msg_bytes = len(msg.encode('utf-8'))

        self.messages.append((now, msg))
        self.total_bytes += msg_bytes
        self.last_update = now

        # 清理超过 5 分钟的旧消息
        cutoff_time = now - BUFFER_MAX_AGE_SECONDS
        while self.messages and self.messages[0][0] < cutoff_time:
            _, old_msg = self.messages.pop(0)
            self.total_bytes -= len(old_msg.encode('utf-8'))

        # 清理超过 1MB 的旧消息
        while self.total_bytes > MAX_BUFFER_SIZE_BYTES and self.messages:
            _, old_msg = self.messages.pop(0)
            self.total_bytes -= len(old_msg.encode('utf-8'))

    def get_messages_after(self, last_index: int) -> List[str]:
        """获取指定索引之后的消息（用于断点续传）"""
        # 注意：索引基于消息添加顺序，不是列表索引
        # 需要额外维护一个 sequence number
        start = max(0, last_index + 1)
        return [msg for _, msg in self.messages[start:]]
```

#### 11.5.2 时间戳精度保证（微秒级 + 序列号）

**问题**：毫秒级时间戳在并发场景下可能导致排序跳变。

**解决方案**：使用微秒级时间戳 + 序列号作为排序依据

```python
# 后端：确保微秒级精度
from datetime import datetime, timezone

def get_precise_timestamp() -> str:
    """获取微秒级 ISO 格式时间戳"""
    now = datetime.now(timezone.utc)
    # 确保包含微秒：2026-02-05T12:34:56.123456+00:00
    return now.isoformat(timespec='microseconds')

# 创建任务时
self.db.table("tasks").insert({
    "id": task_id,
    "created_at": get_precise_timestamp(),
    "sequence": await get_next_sequence(user_id),  # 全局序列号
    # ...
}).execute()
```

```typescript
// 前端：排序时优先时间戳，其次序列号
const sortedMessages = messages.sort((a, b) => {
  // 1. 优先按 created_at 排序（微秒级精度）
  const timeA = new Date(a.created_at).getTime();
  const timeB = new Date(b.created_at).getTime();
  if (timeA !== timeB) return timeA - timeB;

  // 2. 时间相同时按 sequence 排序（兜底）
  return (a.sequence ?? 0) - (b.sequence ?? 0);
});
```

#### 11.5.3 TabId 碰撞防护

**问题**：`Date.now() + Math.random()` 理论上有极低概率碰撞。

**解决方案**：使用 `crypto.randomUUID()`（现代浏览器）或增强随机性

```typescript
// utils/tabSync.ts

function generateTabId(): string {
  // 优先使用 crypto.randomUUID()（现代浏览器，碰撞概率为 0）
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `tab-${crypto.randomUUID()}`;
  }

  // 降级方案：增强随机性
  const timestamp = Date.now().toString(36);
  const random1 = Math.random().toString(36).slice(2, 10);
  const random2 = Math.random().toString(36).slice(2, 10);
  const performance = (typeof window !== 'undefined' && window.performance)
    ? Math.floor(window.performance.now() * 1000).toString(36)
    : '';

  return `tab-${timestamp}-${random1}-${random2}-${performance}`;
}

class TabSyncManager {
  private tabId: string;

  constructor() {
    this.tabId = generateTabId();
    // ...
  }
}
```

**验证**：在控制台运行以下代码确认 `crypto.randomUUID()` 可用：

```javascript
console.log(crypto.randomUUID());  // 应输出类似 "550e8400-e29b-41d4-a716-446655440000"
```
