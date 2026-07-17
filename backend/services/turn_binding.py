"""Turn 任务绑定与关闭的统一数据库出口。"""

from typing import Any, Dict, Optional

from loguru import logger


def insert_task_with_turn_binding(
    db: Any,
    task_data: Dict[str, Any],
    input_message_id: Optional[str],
    turn_id: Optional[str],
    execution_mode: str = "serial",
) -> Optional["ContextAnchor"]:
    """插入任务并固定上下文基线；新任务返回不可变锚点。"""
    db.table("tasks").insert(task_data).execute()
    if not input_message_id or not turn_id:
        return None

    try:
        binding_result = db.rpc("bind_generation_turn", {
            "p_conversation_id": task_data["conversation_id"],
            "p_task_id": task_data["id"],
            "p_input_message_id": input_message_id,
            "p_turn_id": turn_id,
            "p_execution_mode": execution_mode,
        }).execute()
    except Exception:
        try:
            db.table("tasks").delete().eq("id", task_data["id"]).execute()
        except Exception as cleanup_error:
            logger.critical(
                "Turn bind cleanup failed | "
                f"task_id={task_data['id']} | conversation_id={task_data['conversation_id']} | "
                f"turn_id={turn_id} | error={cleanup_error}"
            )
        raise

    binding_data = binding_result.data if binding_result else None
    if not isinstance(binding_data, dict):
        raise RuntimeError("TURN_BIND_RESULT_INVALID")

    from services.handlers.context_snapshot import context_anchor_from_binding

    anchor = context_anchor_from_binding(
        task_data, input_message_id, turn_id, binding_data,
    )

    logger.info(
        "turn_bound | "
        f"org_id={task_data.get('org_id')} | conversation_id={task_data['conversation_id']} | "
        f"task_id={task_data['id']} | turn_id={turn_id} | "
        f"input_message_id={input_message_id}"
    )
    return anchor


def close_bound_turn(
    db: Any,
    conversation_id: str,
    task_id: str,
    output_message_id: str,
) -> Any:
    """通过事务 RPC 关闭已绑定 Turn，并返回数据库结果。"""
    return db.rpc("close_generation_turn", {
        "p_conversation_id": conversation_id,
        "p_task_id": task_id,
        "p_output_message_id": output_message_id,
    }).execute()
