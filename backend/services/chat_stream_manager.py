"""
聊天流管理器

管理后台流式处理协程，支持：
- SSE 断开后继续处理
- 多连接广播（解决并发竞争）
- 最终一致性保障
- 心跳超时优化
- 断点续传
- 保底计费
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional, Any, List, TYPE_CHECKING

from loguru import logger
from supabase import Client

from services.message_utils import deduct_user_credits

if TYPE_CHECKING:
    from services.message_service import MessageService
    from services.adapters.kie.chat_adapter import KieChatAdapter


# 心跳间隔（避免网关超时，Nginx 默认 60s）
HEARTBEAT_INTERVAL = 30  # 秒
# 消息缓冲区大小（用于恢复连接时补发）
MAX_BUFFER_SIZE = 500
# 数据库更新节流间隔
DB_UPDATE_INTERVAL = 0.5  # 秒


@dataclass
class StreamState:
    """单个流任务的状态"""
    task: asyncio.Task
    subscribers: Dict[str, asyncio.Queue] = field(default_factory=dict)
    connection_version: int = 0
    buffer: List[str] = field(default_factory=list)
    full_content: str = ""


class ChatStreamManager:
    """聊天流管理器 - 管理后台流式处理协程"""

    def __init__(self):
        # task_id -> StreamState
        self._active_streams: Dict[str, StreamState] = {}
        self._lock = asyncio.Lock()

    def is_active(self, task_id: str) -> bool:
        """检查任务是否还在处理中"""
        return task_id in self._active_streams

    async def subscribe(
        self,
        task_id: str,
        connection_id: Optional[str] = None,
        last_received_index: int = -1,
    ) -> tuple[Optional[asyncio.Queue], str, str, int]:
        """
        新连接订阅任务流

        Args:
            task_id: 任务 ID
            connection_id: 连接 ID（可选，自动生成）
            last_received_index: 上次收到的消息索引（-1 表示从头开始，用于断点续传）

        Returns:
            (queue, accumulated_content_so_far, connection_id, current_buffer_index)
            如果任务不活跃，返回 (None, "", "", 0)
        """
        async with self._lock:
            state = self._active_streams.get(task_id)
            if not state:
                return None, "", "", 0

            if not connection_id:
                connection_id = str(uuid.uuid4())

            state.connection_version += 1
            queue: asyncio.Queue = asyncio.Queue()
            state.subscribers[connection_id] = queue

            # 【断点续传】只发送 last_received_index 之后的缓冲消息
            start_index = max(0, last_received_index + 1)
            if start_index < len(state.buffer):
                for msg in state.buffer[start_index:]:
                    queue.put_nowait(msg)

            return queue, state.full_content, connection_id, len(state.buffer) - 1

    async def unsubscribe(self, task_id: str, connection_id: str):
        """断开订阅"""
        async with self._lock:
            state = self._active_streams.get(task_id)
            if state:
                state.subscribers.pop(connection_id, None)

    async def start_stream_processing(
        self,
        db: Client,
        message_service: "MessageService",
        task_id: str,
        conversation_id: str,
        user_id: str,
        assistant_message_id: str,
        stream,
        model: str,
        adapter: "KieChatAdapter",
    ) -> tuple[asyncio.Queue, str]:
        """
        启动后台流处理协程

        Returns:
            (queue, connection_id): 用于接收流式输出的队列和连接 ID
        """
        connection_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue()

        state = StreamState(
            task=None,  # 稍后设置
            subscribers={connection_id: queue},
            connection_version=1,
        )

        # 启动后台协程
        task = asyncio.create_task(
            self._process_stream(
                db, message_service, task_id, conversation_id,
                user_id, assistant_message_id, stream, model, adapter, state
            )
        )
        state.task = task

        async with self._lock:
            self._active_streams[task_id] = state

        # 任务完成后自动清理
        def cleanup(_):
            self._active_streams.pop(task_id, None)
        task.add_done_callback(cleanup)

        return queue, connection_id

    async def _process_stream(
        self,
        db: Client,
        message_service: "MessageService",
        task_id: str,
        conversation_id: str,
        user_id: str,
        assistant_message_id: str,
        stream,
        model: str,
        adapter: "KieChatAdapter",
        state: StreamState,
    ):
        """后台流处理协程 - 独立于 SSE 连接运行"""
        total_credits = 0
        last_update_time = time.time()
        completed_normally = False

        try:
            # 更新状态为 running
            db.table("tasks").update({
                "status": "running",
            }).eq("id", task_id).execute()

            # 广播 start 事件
            self._broadcast(task_id, state, {
                "type": "start",
                "data": {"model": model}
            })

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    delta_content = chunk.choices[0].delta.content
                    state.full_content += delta_content

                    # 广播 content 事件
                    self._broadcast(task_id, state, {
                        "type": "content",
                        "data": {"text": delta_content}
                    })

                    # 节流更新数据库
                    if time.time() - last_update_time > DB_UPDATE_INTERVAL:
                        db.table("tasks").update({
                            "accumulated_content": state.full_content,
                        }).eq("id", task_id).execute()
                        last_update_time = time.time()

                # 【关键】计算积分（捕获最后一帧的 usage）
                if chunk.usage:
                    cost = adapter.estimate_cost(
                        chunk.usage.prompt_tokens,
                        chunk.usage.completion_tokens,
                    )
                    total_credits = cost.estimated_credits

            # 完成：创建消息 + 更新任务状态
            if state.full_content:
                # 【保底计费】如果 usage 未返回（异常中断/content_filter 等）
                if total_credits == 0 and len(state.full_content) > 0:
                    # 保底估算：假设 1 token ≈ 3 字符（中文约 1.5 字符）
                    estimated_tokens = max(len(state.full_content) // 3, 10)
                    # 使用 adapter 的配置计算保底积分
                    credits_per_1k = adapter.config.get("credits_per_1k_output", Decimal("1.8"))
                    total_credits = int(Decimal(estimated_tokens) / 1000 * credits_per_1k) + 1
                    logger.warning(
                        f"Usage not received, fallback estimation: "
                        f"task_id={task_id}, content_len={len(state.full_content)}, "
                        f"estimated_credits={total_credits}"
                    )

                assistant_message = await message_service.create_message(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    content=state.full_content,
                    role="assistant",
                    credits_cost=total_credits,
                    message_id=assistant_message_id,
                )
                await deduct_user_credits(
                    db, user_id, total_credits, f"AI 对话 ({model})"
                )

                db.table("tasks").update({
                    "status": "completed",
                    "accumulated_content": state.full_content,
                    "total_credits": total_credits,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", task_id).execute()

                # 广播 done 事件
                self._broadcast(task_id, state, {
                    "type": "done",
                    "data": {
                        "assistant_message": assistant_message,
                        "credits_consumed": total_credits
                    }
                })
            else:
                # 无内容也标记完成
                db.table("tasks").update({
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", task_id).execute()

                self._broadcast(task_id, state, {
                    "type": "done",
                    "data": {"assistant_message": None, "credits_consumed": 0}
                })

            completed_normally = True
            logger.info(f"Chat stream completed: task_id={task_id}")

        except Exception as e:
            logger.error(f"Chat stream failed: task_id={task_id}, error={e}")

            # 创建错误消息
            error_message = await message_service.create_error_message(
                conversation_id=conversation_id,
                user_id=user_id,
                content="抱歉，AI 服务暂时不可用，请稍后重试。",
                message_id=assistant_message_id,
            )

            db.table("tasks").update({
                "status": "failed",
                "error_message": str(e),
                "accumulated_content": state.full_content,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", task_id).execute()

            # 广播 error 事件
            self._broadcast(task_id, state, {
                "type": "error",
                "data": {
                    "message": "抱歉，AI 服务暂时不可用，请稍后重试。",
                    "error_message": error_message
                }
            })

        finally:
            # 【关键】最终一致性保障
            if not completed_normally and state.full_content:
                try:
                    task = db.table("tasks").select("status").eq("id", task_id).single().execute()
                    if task.data and task.data["status"] in ("pending", "running"):
                        logger.warning(f"Ensuring final content sync: task_id={task_id}")
                        db.table("tasks").update({
                            "accumulated_content": state.full_content,
                            "status": "failed",
                            "error_message": "处理异常中断",
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("id", task_id).execute()
                except Exception as sync_error:
                    logger.error(f"Final sync failed: task_id={task_id}, error={sync_error}")

            # 广播结束信号
            self._broadcast(task_id, state, None)

    def _broadcast(self, task_id: str, state: StreamState, data: Any):
        """广播给所有订阅者"""
        if data is None:
            msg = None
        else:
            # 【断点续传】消息中附带索引，方便前端记录
            msg_index = len(state.buffer)
            data_with_index = {**data, "_index": msg_index}
            msg = f"data: {json.dumps(data_with_index)}\n\n"

        # 保存到缓冲区（用于恢复连接时补发，限制大小）
        if msg:
            state.buffer.append(msg)
            if len(state.buffer) > MAX_BUFFER_SIZE:
                state.buffer.pop(0)

        # 广播给所有订阅者
        dead_connections = []
        for conn_id, queue in state.subscribers.items():
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                dead_connections.append(conn_id)
            except Exception as e:
                logger.warning(f"Broadcast failed: conn_id={conn_id}, error={e}")
                dead_connections.append(conn_id)

        # 清理死连接
        for conn_id in dead_connections:
            state.subscribers.pop(conn_id, None)


# 全局单例
chat_stream_manager = ChatStreamManager()
