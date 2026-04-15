"""
WebSocket 消息构建函数

所有 build_* 函数。从 websocket.py 拆分出来。
"""

import time
from typing import Any, Dict, Optional

from schemas.websocket_types import WSMessageType


# ============================================================
# 基础构建器
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
    task_id: str, conversation_id: str, message_id: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """构建开始生成消息"""
    payload: Dict[str, Any] = {}
    if model:
        payload["model"] = model
    return _build_ws_message(
        WSMessageType.MESSAGE_START, payload,
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_message_chunk(
    task_id: str, conversation_id: str, message_id: str,
    chunk: str, accumulated: Optional[str] = None,
) -> Dict[str, Any]:
    """构建流式内容块消息"""
    payload = {"chunk": chunk}
    if accumulated is not None:
        payload["accumulated"] = accumulated
    return _build_ws_message(
        WSMessageType.MESSAGE_CHUNK, payload,
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_thinking_chunk(
    task_id: str, conversation_id: str, message_id: str,
    chunk: str, accumulated: Optional[str] = None,
) -> Dict[str, Any]:
    """构建思考内容流式块消息"""
    payload = {"chunk": chunk}
    if accumulated is not None:
        payload["accumulated"] = accumulated
    return _build_ws_message(
        WSMessageType.THINKING_CHUNK, payload,
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_message_done(
    task_id: str, conversation_id: str,
    message: Dict[str, Any], credits_consumed: Optional[int] = None,
) -> Dict[str, Any]:
    """构建生成完成消息"""
    payload: Dict[str, Any] = {"message": message}
    if credits_consumed is not None:
        payload["credits_consumed"] = credits_consumed
    return _build_ws_message(
        WSMessageType.MESSAGE_DONE, payload,
        task_id=task_id, conversation_id=conversation_id, message_id=message.get("id"),
    )


def build_message_error(
    task_id: str, conversation_id: str, message_id: str,
    error_code: str, error_message: str,
) -> Dict[str, Any]:
    """构建生成失败消息"""
    return _build_ws_message(
        WSMessageType.MESSAGE_ERROR,
        {"error": {"code": error_code, "message": error_message}},
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_message_retry(
    task_id: str, conversation_id: str, new_model: str, attempt: int,
) -> Dict[str, Any]:
    """构建模型重试通知"""
    return _build_ws_message(
        WSMessageType.MESSAGE_RETRY,
        {"new_model": new_model, "attempt": attempt},
        task_id=task_id, conversation_id=conversation_id,
    )


def build_image_partial_update(
    task_id: str, conversation_id: str, message_id: str,
    image_index: int, completed_count: int, total_count: int,
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
        WSMessageType.IMAGE_PARTIAL_UPDATE, payload,
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_subscribed(
    task_id: str, accumulated: str = "", current_index: int = -1,
) -> Dict[str, Any]:
    """构建订阅确认消息"""
    return _build_ws_message(
        WSMessageType.SUBSCRIBED,
        {"task_id": task_id, "accumulated": accumulated, "current_index": current_index},
    )


def build_error(message: str, code: Optional[str] = None) -> Dict[str, Any]:
    """构建错误消息"""
    payload: Dict[str, str] = {"message": message}
    if code:
        payload["code"] = code
    return _build_ws_message(WSMessageType.ERROR, payload)


def build_ping() -> Dict[str, Any]:
    return _build_ws_message(WSMessageType.PING, {})


def build_pong() -> Dict[str, Any]:
    return _build_ws_message(WSMessageType.PONG, {})


def build_server_restarting() -> Dict[str, Any]:
    return _build_ws_message(
        WSMessageType.SERVER_RESTARTING,
        {"message": "Server is restarting, please reconnect"},
    )


def build_routing_complete(
    task_id: str, conversation_id: str, generation_type: str, model: str,
    message_id: Optional[str] = None,
    generation_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建路由完成通知"""
    payload: Dict[str, Any] = {"generation_type": generation_type, "model": model}
    if generation_params:
        payload["generation_params"] = generation_params
    return _build_ws_message(
        WSMessageType.ROUTING_COMPLETE, payload,
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_agent_step(
    conversation_id: str, tool_name: str, status: str, turn: int,
    task_id: Optional[str] = None,
    max_turns: Optional[int] = None,
    elapsed_s: Optional[float] = None,
    tools_completed: Optional[list] = None,
    estimated_s: Optional[int] = None,
) -> Dict[str, Any]:
    """构建 Agent Loop 步骤通知（含进度和预期管理信息）"""
    payload: Dict[str, Any] = {
        "tool_name": tool_name, "status": status, "turn": turn,
    }
    if max_turns is not None:
        payload["progress"] = f"{turn}/{max_turns}"
    if elapsed_s is not None:
        payload["elapsed_s"] = round(elapsed_s, 1)
    if tools_completed:
        payload["tools_completed"] = tools_completed
    if estimated_s is not None:
        payload["estimated_s"] = estimated_s
    return _build_ws_message(
        WSMessageType.AGENT_STEP, payload,
        conversation_id=conversation_id, task_id=task_id,
    )


def build_tool_call(
    task_id: str, conversation_id: str, message_id: str,
    tool_calls: list[Dict[str, Any]], turn: int,
) -> Dict[str, Any]:
    """构建工具调用通知"""
    return _build_ws_message(
        WSMessageType.TOOL_CALL,
        {"tool_calls": tool_calls, "turn": turn},
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_tool_result(
    task_id: str, conversation_id: str, message_id: str,
    tool_name: str, tool_call_id: str, success: bool, summary: str, turn: int,
) -> Dict[str, Any]:
    """构建工具执行结果通知"""
    return _build_ws_message(
        WSMessageType.TOOL_RESULT,
        {"tool_name": tool_name, "tool_call_id": tool_call_id,
         "success": success, "summary": summary, "turn": turn},
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_suggestions_ready(
    conversation_id: str,
    suggestions: list[str],
) -> Dict[str, Any]:
    """构建建议问题就绪通知

    message_done 后异步生成，前端收到后渲染为可点击按钮。
    不绑定 task_id（任务已结束），只按 conversation_id 投递。
    """
    return _build_ws_message(
        WSMessageType.SUGGESTIONS_READY,
        {"suggestions": suggestions},
        conversation_id=conversation_id,
    )


def build_content_block_add(
    task_id: str, conversation_id: str, message_id: str,
    block: Dict[str, Any],
) -> Dict[str, Any]:
    """构建内容块追加通知（工具结果等独立渲染块）

    前端收到后将 block 追加到当前消息的 content 数组中，
    后续 message_chunk 自动追加到新的 text block。
    """
    return _build_ws_message(
        WSMessageType.CONTENT_BLOCK_ADD,
        {"block": block},
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_ask_user_request(
    task_id: str,
    conversation_id: str,
    message_id: str,
    interaction_id: str,
    question: str,
    source: str = "chat",
    options: Optional[list[str]] = None,
    timeout: int = 86400,
) -> Dict[str, Any]:
    """构建 AI 追问请求（ask_user 工具触发）

    前端收到后展示追问消息 + 快捷选项，等待用户回答。
    """
    payload: Dict[str, Any] = {
        "interaction_id": interaction_id,
        "question": question,
        "source": source,
        "timeout": timeout,
    }
    if options:
        payload["options"] = options
    return _build_ws_message(
        WSMessageType.ASK_USER_REQUEST, payload,
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )


def build_tool_confirm_request(
    task_id: str, conversation_id: str, message_id: str,
    tool_call_id: str, tool_name: str, arguments: Dict[str, Any],
    description: str, safety_level: str, timeout: int = 60,
) -> Dict[str, Any]:
    """构建工具确认请求（dangerous 级别）"""
    return _build_ws_message(
        WSMessageType.TOOL_CONFIRM_REQUEST,
        {"tool_call_id": tool_call_id, "tool_name": tool_name,
         "arguments": arguments, "description": description,
         "safety_level": safety_level, "timeout": timeout},
        task_id=task_id, conversation_id=conversation_id, message_id=message_id,
    )
