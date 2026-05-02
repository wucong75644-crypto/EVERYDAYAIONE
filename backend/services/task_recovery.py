"""
孤儿任务恢复

部署重启后，将中断的流式任务的 accumulated_content 回写到 messages 表，
避免用户刷新后看到空消息。

仅在启动时执行一次，通过 Redis 锁保证多 Worker 不重复执行。
"""

from datetime import datetime, timezone
from loguru import logger
from services.task_utils import save_accumulated_to_message, refund_task_credits


async def recover_orphan_tasks(db) -> int:
    """
    扫描所有 status=running/pending 的任务，将有 accumulated_content 的内容
    回写到 messages 表，并标记任务为 completed。

    注意：此函数使用 raw db（无 org_id 过滤），因为启动恢复需要一次性
    处理所有租户的中断任务。每个 task 按自身 ID 独立处理，不存在跨租户泄露。

    Returns:
        恢复的任务数量
    """
    try:
        response = db.table("tasks").select(
            "id, type, external_task_id, placeholder_message_id, conversation_id, "
            "model_id, client_task_id, accumulated_content, accumulated_blocks, credit_transaction_id"
        ).in_(
            "status", ["pending", "running"]
        ).execute()
    except Exception as e:
        logger.error(f"Failed to query orphan tasks | error={e}")
        return 0

    if not response or not response.data:
        return 0

    recovered = 0

    for task in response.data:
        accumulated = (task.get("accumulated_content") or "").strip()
        message_id = task.get("placeholder_message_id")

        # 跳过：无内容 或 无 message_id
        if not accumulated or not message_id:
            _mark_task_failed(
                db, task,
                error_msg="服务重启，任务中断（无已生成内容）",
            )
            continue

        # 将 accumulated_content 写入 messages 表（upsert 幂等）
        model_id = task.get("model_id", "unknown")
        client_task_id = task.get("client_task_id") or task.get("external_task_id", "")

        saved = save_accumulated_to_message(
            db,
            message_id=message_id,
            conversation_id=task["conversation_id"],
            accumulated_content=accumulated,
            model_id=model_id,
            client_task_id=client_task_id,
            task_type=task.get("type", "chat"),
            accumulated_blocks=task.get("accumulated_blocks"),
        )

        if saved:
            # 标记任务完成
            try:
                db.table("tasks").update({
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error_message": "服务重启，已恢复部分内容",
                }).eq("id", task["id"]).execute()
            except Exception as e:
                logger.error(f"Failed to update task status | id={task['id']} | error={e}")

            recovered += 1
            logger.info(
                f"Orphan task recovered | task_id={task.get('external_task_id')} | "
                f"message_id={message_id} | content_len={len(accumulated)}"
            )

    return recovered


def _mark_task_failed(db, task: dict, error_msg: str) -> None:
    """将无内容的孤儿任务标记为 failed + 退积分"""
    # 退还预扣积分
    transaction_id = task.get("credit_transaction_id")
    if transaction_id:
        refund_task_credits(db, transaction_id)

    try:
        db.table("tasks").update({
            "status": "failed",
            "error_message": error_msg,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task["id"]).execute()
    except Exception as e:
        logger.error(f"Failed to mark orphan task as failed | id={task['id']} | error={e}")
