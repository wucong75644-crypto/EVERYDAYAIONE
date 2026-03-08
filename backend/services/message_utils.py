"""
消息服务工具函数

包含格式化和积分相关的辅助函数。
"""

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
        "content": message["content"],
        "role": message["role"],
        "created_at": message["created_at"],
        "status": message.get("status", "completed"),
        "credits_cost": message.get("credits_cost", 0),
        "is_error": message.get("is_error", False),
        "generation_params": message.get("generation_params"),
        "task_id": message.get("task_id"),
        "client_request_id": message.get("client_request_id"),  # 客户端请求ID（用于乐观更新）
    }


