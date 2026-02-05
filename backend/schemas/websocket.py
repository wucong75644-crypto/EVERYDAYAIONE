"""
WebSocket 消息模型

定义 WebSocket 通信中使用的消息格式。
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class WSMessageType(str, Enum):
    """WebSocket 消息类型"""

    # === 聊天相关 ===
    CHAT_START = "chat_start"           # AI 开始生成
    CHAT_CHUNK = "chat_chunk"           # 流式内容块
    CHAT_DONE = "chat_done"             # 生成完成
    CHAT_ERROR = "chat_error"           # 生成失败

    # === 任务相关 ===
    TASK_STATUS = "task_status"         # 图片/视频任务状态更新
    TASK_PROGRESS = "task_progress"     # 任务进度

    # === 通知相关 ===
    CREDITS_CHANGED = "credits_changed"  # 积分变化
    NOTIFICATION = "notification"        # 通用通知

    # === 连接相关 ===
    PING = "ping"                       # 心跳请求
    PONG = "pong"                       # 心跳响应
    SUBSCRIBE = "subscribe"             # 订阅任务
    UNSUBSCRIBE = "unsubscribe"         # 取消订阅
    SUBSCRIBED = "subscribed"           # 订阅成功确认
    ERROR = "error"                     # 错误消息

    # === 系统相关 ===
    SERVER_RESTARTING = "server_restarting"  # 服务即将重启


# === 基础消息模型 ===

class WSBaseMessage(BaseModel):
    """WebSocket 基础消息"""
    type: WSMessageType
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: int = Field(description="Unix 时间戳（毫秒）")
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_index: Optional[int] = None


# === 客户端发送的消息 ===

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


# === 服务端发送的消息 ===

class ChatStartPayload(BaseModel):
    """聊天开始消息的 payload"""
    model: str
    assistant_message_id: str


class ChatChunkPayload(BaseModel):
    """聊天流式内容块的 payload"""
    text: str
    accumulated: Optional[str] = None


class ChatDonePayload(BaseModel):
    """聊天完成消息的 payload"""
    message_id: str
    content: str
    credits_consumed: int
    model: str
    usage: Optional[Dict[str, int]] = None


class ChatErrorPayload(BaseModel):
    """聊天错误消息的 payload"""
    error: str
    error_code: Optional[str] = None


class TaskStatusPayload(BaseModel):
    """任务状态更新的 payload"""
    status: str  # pending, running, completed, failed
    media_type: Optional[str] = None  # image, video
    urls: Optional[List[str]] = None
    credits_consumed: Optional[int] = None
    error_message: Optional[str] = None


class TaskProgressPayload(BaseModel):
    """任务进度的 payload"""
    status: str
    progress: int  # 0-100
    message: Optional[str] = None


class CreditsChangedPayload(BaseModel):
    """积分变化的 payload"""
    credits: int  # 当前积分
    delta: int    # 变化量（正数增加，负数减少）
    reason: str   # 原因
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


# === 服务端消息构建辅助函数 ===

def build_ws_message(
    msg_type: WSMessageType,
    payload: Dict[str, Any],
    task_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构建 WebSocket 消息

    Args:
        msg_type: 消息类型
        payload: 消息内容
        task_id: 任务 ID（可选）
        conversation_id: 对话 ID（可选）

    Returns:
        消息字典
    """
    import time
    message = {
        "type": msg_type.value,
        "payload": payload,
        "timestamp": int(time.time() * 1000),
    }
    if task_id:
        message["task_id"] = task_id
    if conversation_id:
        message["conversation_id"] = conversation_id
    return message


def build_chat_start_message(
    task_id: str,
    conversation_id: str,
    model: str,
    assistant_message_id: str,
) -> Dict[str, Any]:
    """构建聊天开始消息"""
    return build_ws_message(
        WSMessageType.CHAT_START,
        {"model": model, "assistant_message_id": assistant_message_id},
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_chat_chunk_message(
    task_id: str,
    text: str,
    accumulated: Optional[str] = None,
) -> Dict[str, Any]:
    """构建聊天流式内容块消息"""
    payload = {"text": text}
    if accumulated is not None:
        payload["accumulated"] = accumulated
    return build_ws_message(
        WSMessageType.CHAT_CHUNK,
        payload,
        task_id=task_id,
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
    """构建聊天完成消息"""
    payload = {
        "message_id": message_id,
        "content": content,
        "credits_consumed": credits_consumed,
        "model": model,
    }
    if usage:
        payload["usage"] = usage
    return build_ws_message(
        WSMessageType.CHAT_DONE,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_chat_error_message(
    task_id: str,
    error: str,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """构建聊天错误消息"""
    payload = {"error": error}
    if error_code:
        payload["error_code"] = error_code
    return build_ws_message(
        WSMessageType.CHAT_ERROR,
        payload,
        task_id=task_id,
    )


def build_task_status_message(
    task_id: str,
    conversation_id: str,
    status: str,
    media_type: Optional[str] = None,
    urls: Optional[List[str]] = None,
    credits_consumed: Optional[int] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    """构建任务状态更新消息"""
    payload = {"status": status}
    if media_type:
        payload["media_type"] = media_type
    if urls:
        payload["urls"] = urls
    if credits_consumed is not None:
        payload["credits_consumed"] = credits_consumed
    if error_message:
        payload["error_message"] = error_message
    return build_ws_message(
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
    """构建积分变化消息"""
    return build_ws_message(
        WSMessageType.CREDITS_CHANGED,
        {
            "credits": credits,
            "delta": delta,
            "reason": reason,
            "task_id": task_id,
        },
    )


def build_subscribed_message(
    task_id: str,
    accumulated: str = "",
    current_index: int = -1,
) -> Dict[str, Any]:
    """构建订阅确认消息"""
    return build_ws_message(
        WSMessageType.SUBSCRIBED,
        {
            "task_id": task_id,
            "accumulated": accumulated,
            "current_index": current_index,
        },
    )


def build_error_message(
    message: str,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    """构建错误消息"""
    payload = {"message": message}
    if code:
        payload["code"] = code
    return build_ws_message(WSMessageType.ERROR, payload)


def build_ping_message() -> Dict[str, Any]:
    """构建心跳请求消息"""
    return build_ws_message(WSMessageType.PING, {})


def build_pong_message() -> Dict[str, Any]:
    """构建心跳响应消息"""
    return build_ws_message(WSMessageType.PONG, {})


def build_server_restarting_message() -> Dict[str, Any]:
    """构建服务重启通知消息"""
    return build_ws_message(
        WSMessageType.SERVER_RESTARTING,
        {"message": "Server is restarting, please reconnect"},
    )
