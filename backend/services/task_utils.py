"""
任务恢复/清理公共工具函数

供 task_recovery、background_task_worker 共用，消除重复代码。
"""

from typing import List, Dict, Any

from loguru import logger


def merge_blocks_with_text(
    blocks: List[Dict[str, Any]],
    accumulated_text: str,
) -> List[Dict[str, Any]]:
    """将结构化 blocks 与累积文字合并，去重后返回完整 content 数组。

    blocks 中已有的 text 块覆盖了历史轮次的文字，accumulated_text 包含全部文字。
    计算差值（当前轮剩余文字），追加为最后一个 text 块。
    """
    blocks_text = "".join(
        b.get("text", "") for b in blocks if b.get("type") == "text"
    )
    remaining = ""
    if accumulated_text and accumulated_text.startswith(blocks_text):
        remaining = accumulated_text[len(blocks_text):]
    content = list(blocks)
    if remaining.strip():
        content.append({"type": "text", "text": remaining})
    return content


def save_accumulated_to_message(
    db,
    message_id: str,
    conversation_id: str,
    accumulated_content: str,
    model_id: str = "unknown",
    client_task_id: str = "",
    task_type: str = "chat",
    accumulated_blocks: list | None = None,
) -> bool:
    """
    将 tasks.accumulated_content + accumulated_blocks 回写到 messages 表（upsert 幂等）。

    Returns:
        True 写入成功，False 写入失败
    """
    try:
        if accumulated_blocks:
            content = merge_blocks_with_text(accumulated_blocks, accumulated_content)
        else:
            content = [{"type": "text", "text": accumulated_content}]

        db.table("messages").upsert({
            "id": message_id,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": content,
            "status": "completed",
            "credits_cost": 0,
            "task_id": client_task_id,
            "generation_params": {"type": task_type, "model": model_id},
        }, on_conflict="id").execute()
        return True
    except Exception as e:
        logger.error(
            f"Failed to save accumulated_content to messages | "
            f"message_id={message_id} | error={e}"
        )
        return False


def refund_task_credits(db, transaction_id: str) -> bool:
    """
    退回预扣积分（原子操作，幂等安全）。

    Returns:
        True 退回成功或已退回，False 失败
    """
    try:
        result = db.rpc(
            'atomic_refund_credits',
            {'p_transaction_id': transaction_id}
        ).execute()

        data = result.data
        if data and data.get('refunded'):
            logger.info(
                f"Credits refunded | transaction_id={transaction_id} | "
                f"user_id={data.get('user_id')} | amount={data.get('amount')}"
            )
            return True
        else:
            reason = data.get('reason', 'unknown') if data else 'no_response'
            logger.warning(f"Refund skipped | tx={transaction_id} | reason={reason}")
            return True  # 已退或不需退，不算失败
    except Exception as e:
        logger.error(f"Refund failed | transaction_id={transaction_id} | error={e}")
        return False
