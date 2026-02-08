"""
统一 WebSocket 消息模型

定义 WebSocket 通信中使用的消息格式。
遵循统一多模态消息系统设计规范。
"""

import time
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class WSMessageType(str, Enum):
    """
    统一 WebSocket 消息类型

    消息生命周期：
    - message_pending: 任务已提交，等待处理
    - message_start: 开始生成（流式）
    - message_chunk: 流式内容块
    - message_progress: 进度更新（0-100）
    - message_done: 生成完成
    - message_error: 生成失败

    系统消息：
    - credits_changed: 积分变化
    - notification: 通知

    连接管理：
    - subscribe: 订阅任务
    - unsubscribe: 取消订阅
    - subscribed: 订阅成功确认
    - ping/pong: 心跳
    - error: 错误消息
    """

    # === 消息生命周期 ===
    MESSAGE_PENDING = "message_pending"
    MESSAGE_START = "message_start"
    MESSAGE_CHUNK = "message_chunk"
    MESSAGE_PROGRESS = "message_progress"
    MESSAGE_DONE = "message_done"
    MESSAGE_ERROR = "message_error"

    # === 系统消息 ===
    CREDITS_CHANGED = "credits_changed"
    NOTIFICATION = "notification"

    # === 连接管理 ===
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    SUBSCRIBED = "subscribed"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"

    # === 兼容旧消息类型（过渡期保留） ===
    CHAT_START = "chat_start"
    CHAT_CHUNK = "chat_chunk"
    CHAT_DONE = "chat_done"
    CHAT_ERROR = "chat_error"
    TASK_STATUS = "task_status"
    TASK_PROGRESS = "task_progress"

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


# ============================================================
# 兼容旧格式的 Payload 模型（过渡期保留）
# ============================================================


class ChatStartPayload(BaseModel):
    """聊天开始消息的 payload（兼容旧格式）"""
    model: str
    assistant_message_id: str


class ChatChunkPayload(BaseModel):
    """聊天流式内容块的 payload（兼容旧格式）"""
    text: str
    accumulated: Optional[str] = None


class ChatDonePayload(BaseModel):
    """聊天完成消息的 payload（兼容旧格式）"""
    message_id: str
    content: str
    credits_consumed: int
    model: str
    usage: Optional[Dict[str, int]] = None


class ChatErrorPayload(BaseModel):
    """聊天错误消息的 payload（兼容旧格式）"""
    error: str
    error_code: Optional[str] = None


class TaskStatusPayload(BaseModel):
    """任务状态更新的 payload（兼容旧格式）"""
    status: str
    media_type: Optional[str] = None
    urls: Optional[List[str]] = None
    credits_consumed: Optional[int] = None
    error_message: Optional[str] = None
    message: Optional[Dict[str, Any]] = None


class TaskProgressPayload(BaseModel):
    """任务进度的 payload（兼容旧格式）"""
    status: str
    progress: int
    message: Optional[str] = None


# ============================================================
# 消息构建辅助函数
# ============================================================


def _build_ws_message(
    msg_type: WSMessageType,
    payload: Dict[str, Any],
    task_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 WebSocket 消息基础结构"""
    message = {
        "type": msg_type.value,
        "payload": payload,
        "timestamp": int(time.time() * 1000),
    }
    if task_id:
        message["task_id"] = task_id
    if conversation_id:
        message["conversation_id"] = conversation_id
    if message_id:
        message["message_id"] = message_id
    return message


# ============================================================
# 统一消息构建函数（新协议）
# ============================================================


def build_message_pending(
    task_id: str,
    conversation_id: str,
    message_id: str,
    estimated_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """构建任务已提交消息"""
    payload = {"message_id": message_id}
    if estimated_time_ms:
        payload["estimated_time_ms"] = estimated_time_ms
    return _build_ws_message(
        WSMessageType.MESSAGE_PENDING,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_message_start(
    task_id: str,
    conversation_id: str,
    message_id: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """构建开始生成消息"""
    payload: Dict[str, Any] = {}
    if model:
        payload["model"] = model
    return _build_ws_message(
        WSMessageType.MESSAGE_START,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_message_chunk(
    task_id: str,
    conversation_id: str,
    message_id: str,
    chunk: str,
    accumulated: Optional[str] = None,
) -> Dict[str, Any]:
    """构建流式内容块消息"""
    payload = {"chunk": chunk}
    if accumulated is not None:
        payload["accumulated"] = accumulated
    return _build_ws_message(
        WSMessageType.MESSAGE_CHUNK,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_message_progress(
    task_id: str,
    conversation_id: str,
    message_id: str,
    progress: int,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """构建进度更新消息"""
    payload: Dict[str, Any] = {"progress": progress}
    if message:
        payload["message"] = message
    return _build_ws_message(
        WSMessageType.MESSAGE_PROGRESS,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_message_done(
    task_id: str,
    conversation_id: str,
    message: Dict[str, Any],
    credits_consumed: Optional[int] = None,
) -> Dict[str, Any]:
    """构建生成完成消息"""
    payload: Dict[str, Any] = {"message": message}
    if credits_consumed is not None:
        payload["credits_consumed"] = credits_consumed
    return _build_ws_message(
        WSMessageType.MESSAGE_DONE,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message.get("id"),
    )


def build_message_error(
    task_id: str,
    conversation_id: str,
    message_id: str,
    error_code: str,
    error_message: str,
) -> Dict[str, Any]:
    """构建生成失败消息"""
    return _build_ws_message(
        WSMessageType.MESSAGE_ERROR,
        {"error": {"code": error_code, "message": error_message}},
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_credits_changed(
    credits: int,
    delta: int,
    reason: str,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建积分变化消息"""
    return _build_ws_message(
        WSMessageType.CREDITS_CHANGED,
        {
            "credits": credits,
            "delta": delta,
            "reason": reason,
            "task_id": task_id,
        },
    )


def build_subscribed(
    task_id: str,
    accumulated: str = "",
    current_index: int = -1,
) -> Dict[str, Any]:
    """构建订阅确认消息"""
    return _build_ws_message(
        WSMessageType.SUBSCRIBED,
        {
            "task_id": task_id,
            "accumulated": accumulated,
            "current_index": current_index,
        },
    )


def build_error(
    message: str,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    """构建错误消息"""
    payload: Dict[str, str] = {"message": message}
    if code:
        payload["code"] = code
    return _build_ws_message(WSMessageType.ERROR, payload)


def build_ping() -> Dict[str, Any]:
    """构建心跳请求消息"""
    return _build_ws_message(WSMessageType.PING, {})


def build_pong() -> Dict[str, Any]:
    """构建心跳响应消息"""
    return _build_ws_message(WSMessageType.PONG, {})


def build_server_restarting() -> Dict[str, Any]:
    """构建服务重启通知消息"""
    return _build_ws_message(
        WSMessageType.SERVER_RESTARTING,
        {"message": "Server is restarting, please reconnect"},
    )


# ============================================================
# 兼容旧消息构建函数（过渡期保留）
# ============================================================


def build_ws_message(
    msg_type: WSMessageType,
    payload: Dict[str, Any],
    task_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 WebSocket 消息（兼容旧接口）"""
    return _build_ws_message(msg_type, payload, task_id, conversation_id)


def build_chat_start_message(
    task_id: str,
    conversation_id: str,
    model: str,
    assistant_message_id: str,
) -> Dict[str, Any]:
    """构建聊天开始消息（兼容旧格式）"""
    return _build_ws_message(
        WSMessageType.CHAT_START,
        {"model": model, "assistant_message_id": assistant_message_id},
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_chat_chunk_message(
    task_id: str,
    text: str,
    conversation_id: str,
    accumulated: Optional[str] = None,
) -> Dict[str, Any]:
    """构建聊天流式内容块消息（兼容旧格式）"""
    payload = {"text": text}
    if accumulated is not None:
        payload["accumulated"] = accumulated
    return _build_ws_message(
        WSMessageType.CHAT_CHUNK,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_chat_done_message(
    task_id: str,
    conversation_id: str,
    message_id: str,
    content: str,
    credits_consumed: int,
    model: str,
    usage: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """构建聊天完成消息（兼容旧格式）"""
    payload: Dict[str, Any] = {
        "message_id": message_id,
        "content": content,
        "credits_consumed": credits_consumed,
        "model": model,
    }
    if usage:
        payload["usage"] = usage
    return _build_ws_message(
        WSMessageType.CHAT_DONE,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_chat_error_message(
    task_id: str,
    error: str,
    conversation_id: Optional[str] = None,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """构建聊天错误消息（兼容旧格式）"""
    payload: Dict[str, Any] = {"error": error}
    if error_code:
        payload["error_code"] = error_code
    return _build_ws_message(
        WSMessageType.CHAT_ERROR,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_task_status_message(
    task_id: str,
    conversation_id: str,
    status: str,
    media_type: Optional[str] = None,
    urls: Optional[List[str]] = None,
    credits_consumed: Optional[int] = None,
    error_message: Optional[str] = None,
    created_message: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建任务状态更新消息（兼容旧格式）"""
    payload: Dict[str, Any] = {"status": status}
    if media_type:
        payload["media_type"] = media_type
    if urls:
        payload["urls"] = urls
    if credits_consumed is not None:
        payload["credits_consumed"] = credits_consumed
    if error_message:
        payload["error_message"] = error_message
    if created_message:
        payload["message"] = created_message
    return _build_ws_message(
        WSMessageType.TASK_STATUS,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_credits_changed_message(
    credits: int,
    delta: int,
    reason: str,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建积分变化消息（兼容旧接口）"""
    return build_credits_changed(credits, delta, reason, task_id)


def build_subscribed_message(
    task_id: str,
    accumulated: str = "",
    current_index: int = -1,
) -> Dict[str, Any]:
    """构建订阅确认消息（兼容旧接口）"""
    return build_subscribed(task_id, accumulated, current_index)


def build_error_message(
    message: str,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    """构建错误消息（兼容旧接口）"""
    return build_error(message, code)


def build_ping_message() -> Dict[str, Any]:
    """构建心跳请求消息（兼容旧接口）"""
    return build_ping()


def build_pong_message() -> Dict[str, Any]:
    """构建心跳响应消息（兼容旧接口）"""
    return build_pong()


def build_server_restarting_message() -> Dict[str, Any]:
    """构建服务重启通知消息（兼容旧接口）"""
    return build_server_restarting()
