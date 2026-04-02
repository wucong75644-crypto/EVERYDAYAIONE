"""
统一 WebSocket 消息模型

定义 WebSocket 通信中使用的消息格式。
遵循统一多模态消息系统设计规范。
"""

import time
from enum import Enum
from typing import Any, Dict, Optional, Union

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
    THINKING_CHUNK = "thinking_chunk"
    MESSAGE_PROGRESS = "message_progress"
    MESSAGE_DONE = "message_done"
    MESSAGE_ERROR = "message_error"
    MESSAGE_RETRY = "message_retry"
    IMAGE_PARTIAL_UPDATE = "image_partial_update"
    AGENT_STEP = "agent_step"
    ROUTING_COMPLETE = "routing_complete"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_CONFIRM_REQUEST = "tool_confirm_request"
    TOOL_CONFIRM_RESPONSE = "tool_confirm_response"

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
# 统一消息构建函数
# ============================================================


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


def build_thinking_chunk(
    task_id: str,
    conversation_id: str,
    message_id: str,
    chunk: str,
    accumulated: Optional[str] = None,
) -> Dict[str, Any]:
    """构建思考内容流式块消息"""
    payload = {"chunk": chunk}
    if accumulated is not None:
        payload["accumulated"] = accumulated
    return _build_ws_message(
        WSMessageType.THINKING_CHUNK,
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


def build_message_retry(
    task_id: str,
    conversation_id: str,
    new_model: str,
    attempt: int,
) -> Dict[str, Any]:
    """构建模型重试通知"""
    return _build_ws_message(
        WSMessageType.MESSAGE_RETRY,
        {"new_model": new_model, "attempt": attempt},
        task_id=task_id,
        conversation_id=conversation_id,
    )


def build_image_partial_update(
    task_id: str,
    conversation_id: str,
    message_id: str,
    image_index: int,
    completed_count: int,
    total_count: int,
    content_part: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """构建多图批次中单张图片完成/失败的通知"""
    payload: Dict[str, Any] = {
        "image_index": image_index,
        "content_part": content_part,
        "completed_count": completed_count,
        "total_count": total_count,
    }
    if error:
        payload["error"] = error
    return _build_ws_message(
        WSMessageType.IMAGE_PARTIAL_UPDATE,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
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


def build_routing_complete(
    task_id: str,
    conversation_id: str,
    generation_type: str,
    model: str,
    message_id: Optional[str] = None,
    generation_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建路由完成通知（smart mode 异步路由结果为 image/video 时通知前端变形占位符）"""
    payload: Dict[str, Any] = {
        "generation_type": generation_type,
        "model": model,
    }
    if generation_params:
        payload["generation_params"] = generation_params
    return _build_ws_message(
        WSMessageType.ROUTING_COMPLETE,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_agent_step(
    conversation_id: str,
    tool_name: str,
    status: str,
    turn: int,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 Agent Loop 步骤通知（前端显示「搜索中...」等临时状态）"""
    return _build_ws_message(
        WSMessageType.AGENT_STEP,
        {
            "tool_name": tool_name,
            "status": status,
            "turn": turn,
        },
        conversation_id=conversation_id,
        task_id=task_id,
    )


def build_tool_call(
    task_id: str,
    conversation_id: str,
    message_id: str,
    tool_calls: list[Dict[str, Any]],
    turn: int,
) -> Dict[str, Any]:
    """构建工具调用通知（AI 决定调用工具，前端显示工具执行中）"""
    return _build_ws_message(
        WSMessageType.TOOL_CALL,
        {
            "tool_calls": tool_calls,
            "turn": turn,
        },
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_tool_result(
    task_id: str,
    conversation_id: str,
    message_id: str,
    tool_name: str,
    tool_call_id: str,
    success: bool,
    summary: str,
    turn: int,
) -> Dict[str, Any]:
    """构建工具执行结果通知（工具执行完成，前端更新状态）"""
    return _build_ws_message(
        WSMessageType.TOOL_RESULT,
        {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "success": success,
            "summary": summary,
            "turn": turn,
        },
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


def build_tool_confirm_request(
    task_id: str,
    conversation_id: str,
    message_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    description: str,
    safety_level: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    """构建工具确认请求（dangerous 级别工具执行前发送，等用户确认）

    前端收到后弹确认框，用户点确认/拒绝后发 TOOL_CONFIRM_RESPONSE 回来。
    超时未回复视为拒绝。
    """
    return _build_ws_message(
        WSMessageType.TOOL_CONFIRM_REQUEST,
        {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "description": description,
            "safety_level": safety_level,
            "timeout": timeout,
        },
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
