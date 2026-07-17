"""企业微信同步生成的 Turn/task 生命周期适配。"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from loguru import logger

from services.handlers.base import TaskMetadata
from services.turn_binding import close_bound_turn


def create_wecom_turn_task(
    handler: Any,
    conversation_id: str,
    user_id: str,
    message_id: str,
    input_message_id: str,
    turn_id: str,
    text_content: str,
    org_id: Optional[str],
) -> Tuple[str, "ContextAnchor"]:
    """创建并绑定企微同步 chat task，返回任务 UUID 与固定上下文锚点。"""
    metadata = TaskMetadata(
        input_message_id=input_message_id,
        turn_id=turn_id,
        execution_mode="serial",
    )
    task_data = handler._build_task_data(
        task_id=f"wecom_{uuid.uuid4()}",
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        task_type="chat",
        status="running",
        model_id="auto",
        request_params={"content": text_content, "_org_id": org_id},
        metadata=metadata,
    )
    anchor = handler._insert_task_with_turn_binding(task_data, metadata)
    if anchor is None:
        raise RuntimeError("WECOM_CONTEXT_ANCHOR_MISSING")
    return task_data["id"], anchor


def complete_wecom_turn_task(
    db: Any,
    conversation_id: str,
    task_id: str,
    message_id: str,
    turn_id: str,
    org_id: Optional[str],
) -> None:
    """关闭企微 Turn，并把同步 task 标记为 completed。"""
    close_result = close_bound_turn(db, conversation_id, task_id, message_id)
    logger.info(
        "turn_closed | channel=wecom | "
        f"org_id={org_id} | conversation_id={conversation_id} | "
        f"task_id={task_id} | turn_id={turn_id} | "
        f"result={close_result.data if close_result else None}"
    )


def fail_wecom_turn_task(
    db: Any,
    task_id: str,
    conversation_id: str,
    turn_id: Optional[str],
    error: Exception,
) -> None:
    """记录企微同步 task 失败；失败 Turn 不关闭、不推进 revision。"""
    try:
        db.table("tasks").update({
            "status": "failed",
            "error_message": str(error)[:1000],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task_id).execute()
    except Exception as task_error:
        logger.critical(
            f"Wecom task failure persistence failed | task_id={task_id} | "
            f"conversation_id={conversation_id} | turn_id={turn_id} | error={task_error}"
        )
