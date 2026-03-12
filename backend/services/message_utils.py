"""
消息服务工具函数

包含格式化和积分相关的辅助函数。
"""

import json
from typing import Any, List


def parse_content(content: Any) -> List[dict]:
    """将 DB content 字段统一解析为 list（兼容 str / list）

    Supabase JSONB 字段可能返回 Python list 或 JSON 字符串，
    此函数确保所有消费者拿到的都是 list[dict]。
    """
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        # 纯文本字符串 → 包装成标准格式
        return [{"type": "text", "text": content}]
    return []


def format_message(message: dict) -> dict:
    """
    格式化消息对象

    Args:
        message: 数据库消息记录

    Returns:
        格式化后的消息对象
    """
    return {
        "id": message["id"],
        "conversation_id": message["conversation_id"],
        "content": parse_content(message["content"]),
        "role": message["role"],
        "created_at": message["created_at"],
        "status": message.get("status", "completed"),
        "credits_cost": message.get("credits_cost", 0),
        "is_error": message.get("is_error", False),
        "generation_params": message.get("generation_params"),
        "task_id": message.get("task_id"),
        "client_request_id": message.get("client_request_id"),
    }
