"""
消息服务工具函数

包含格式化和积分相关的辅助函数。
"""

from typing import Dict, Any
from loguru import logger
from supabase import Client


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
        "image_url": message.get("image_url"),
        "video_url": message.get("video_url"),
        "credits_cost": message.get("credits_cost", 0),
        "is_error": message.get("is_error", False),
    }


async def deduct_user_credits(
    db: Client,
    user_id: str,
    credits: int,
    description: str,
) -> None:
    """
    扣除用户积分

    Args:
        db: 数据库客户端
        user_id: 用户 ID
        credits: 扣除的积分数
        description: 扣除原因描述
    """
    # 查询用户当前积分
    user_result = db.table("users").select("credits").eq(
        "id", user_id
    ).single().execute()

    if not user_result.data:
        logger.error(f"User not found for credits deduction | user_id={user_id}")
        return

    current_credits = user_result.data["credits"]
    new_balance = current_credits - credits

    # 更新用户积分
    db.table("users").update({
        "credits": new_balance
    }).eq("id", user_id).execute()

    # 记录积分历史
    db.table("credits_history").insert({
        "user_id": user_id,
        "change_amount": -credits,
        "balance_after": new_balance,
        "change_type": "conversation_cost",
        "description": description,
    }).execute()

    logger.info(
        f"Credits deducted | user_id={user_id} | credits={credits} | "
        f"balance_after={new_balance}"
    )
