"""
WebSocket 消息类型定义

枚举 + Payload 模型。从 websocket.py 拆分出来。
"""

import time
from enum import Enum
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field


class WSMessageType(str, Enum):
    """
    WebSocket 消息类型枚举

    命名规范：
    - 服务端 → 客户端：snake_case（如 message_start）
    - 客户端 → 服务端：snake_case（如 subscribe）
    """
    # === 消息生命周期 ===
    MESSAGE_PENDING = "message_pending"
    MESSAGE_START = "message_start"
    MESSAGE_CHUNK = "message_chunk"
    THINKING_CHUNK = "thinking_chunk"
    MESSAGE_PROGRESS = "message_progress"
    MESSAGE_DONE = "message_done"
    MESSAGE_ERROR = "message_error"
    MESSAGE_RETRY = "message_retry"
    IMAGE_PARTIAL_UPDATE = "image_partial_update"

    # === Agent 工具循环 ===
    ROUTING_COMPLETE = "routing_complete"
    AGENT_STEP = "agent_step"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_CONFIRM_REQUEST = "tool_confirm_request"
    TOOL_CONFIRM_RESPONSE = "tool_confirm_response"

    # === 积分变化 ===
    CREDITS_CHANGED = "credits_changed"

    # === 系统消息 ===
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    SUBSCRIBED = "subscribed"

    # === 连接管理 ===
    PING = "ping"
    PONG = "pong"
    ERROR = "error"

    # === 系统相关 ===
    SERVER_RESTARTING = "server_restarting"


# ============================================================
# 基础消息模型
# ============================================================


class WSBaseMessage(BaseModel):
    """WebSocket 基础消息"""
    type: WSMessageType
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: int = Field(description="Unix 时间戳（毫秒）")
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None


# ============================================================
# 客户端发送的消息
# ============================================================


class SubscribePayload(BaseModel):
    """订阅消息的 payload"""
    task_id: str
    last_index: int = -1  # 用于断点续传


class UnsubscribePayload(BaseModel):
    """取消订阅消息的 payload"""
    task_id: str


class ClientMessage(BaseModel):
    """客户端发送的消息"""
    type: WSMessageType
    payload: Union[SubscribePayload, UnsubscribePayload, Dict[str, Any]] = Field(
        default_factory=dict
    )


# ============================================================
# 统一消息 Payload 模型
# ============================================================


class MessagePendingPayload(BaseModel):
    """任务已提交的 payload"""
    message_id: str
    estimated_time_ms: Optional[int] = None


class MessageStartPayload(BaseModel):
    """开始生成的 payload"""
    model: Optional[str] = None


class MessageChunkPayload(BaseModel):
    """流式内容块的 payload"""
    chunk: str
    accumulated: Optional[str] = None


class MessageProgressPayload(BaseModel):
    """进度更新的 payload"""
    progress: int  # 0-100
    message: Optional[str] = None


class MessageDonePayload(BaseModel):
    """生成完成的 payload"""
    message: Dict[str, Any]  # 完整消息对象
    credits_consumed: Optional[int] = None


class MessageErrorPayload(BaseModel):
    """生成失败的 payload"""
    error: Dict[str, str]  # { code, message }


class CreditsChangedPayload(BaseModel):
    """积分变化的 payload"""
    credits: int
    delta: int
    reason: str
    task_id: Optional[str] = None


class SubscribedPayload(BaseModel):
    """订阅确认的 payload"""
    task_id: str
    accumulated: str = ""
    current_index: int = -1


class ErrorPayload(BaseModel):
    """错误消息的 payload"""
    message: str
    code: Optional[str] = None
