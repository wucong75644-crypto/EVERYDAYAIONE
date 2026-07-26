"""Replay terminal task state when a WebSocket subscribes after completion."""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from schemas.websocket import build_message_done, build_message_error
from services.websocket_manager import ws_manager


def get_task_accumulated_state(
    task: dict[str, Any],
) -> tuple[Optional[str], Optional[list]]:
    """Return resumable streaming state for a running chat task."""
    if task.get("type") != "chat" or task.get("status") != "running":
        return None, None
    content = task.get("accumulated_content")
    blocks = task.get("accumulated_blocks")
    return (content, blocks or []) if (content or blocks) else (None, None)


async def check_and_send_completed_task(
    conn_id: str,
    task_id: str,
    task: dict[str, Any],
    db: Any,
) -> None:
    """Replay a terminal task without changing its persisted state."""
    try:
        status = task.get("status")
        if status not in ("completed", "failed", "cancelled"):
            return

        task_type = task.get("type")
        conversation_id = task.get("conversation_id")
        message_id = (
            task.get("assistant_message_id")
            or task.get("placeholder_message_id")
        )
        push_task_id = (
            task.get("client_task_id")
            or task.get("external_task_id")
            or task_id
        )
        message_data = await _find_message_by_id(db, message_id)
        if not message_data:
            message_data = _build_fallback_message(
                task, message_id, conversation_id,
            )

        if status == "completed":
            await ws_manager.send_to_connection(
                conn_id,
                build_message_done(
                    task_id=push_task_id,
                    conversation_id=conversation_id or "",
                    message=message_data,
                    credits_consumed=message_data.get("credits_cost", 0),
                ),
            )
            logger.info(
                "Sent completed task | "
                f"conn={conn_id} | type={task_type} | task={push_task_id} | "
                f"message_id={message_id}"
            )
            return

        await ws_manager.send_to_connection(
            conn_id,
            build_message_error(
                task_id=push_task_id,
                conversation_id=conversation_id or "",
                message_id=message_id,
                error_code=(
                    "GENERATION_FAILED"
                    if status == "failed"
                    else "TASK_CANCELLED"
                ),
                error_message=(
                    task.get("error_message", "生成失败")
                    if status == "failed"
                    else "任务已取消"
                ),
            ),
        )
    except Exception as error:
        logger.warning(
            "Failed to replay completed task | "
            f"task={task_id} | error={type(error).__name__}"
        )


async def _find_message_by_id(
    db: Any,
    message_id: str | None,
) -> Optional[dict[str, Any]]:
    if not message_id:
        return None
    result = (
        db.table("messages")
        .select("*")
        .eq("id", message_id)
        .maybe_single()
        .execute()
    )
    return result.data if result and result.data else None


def _build_fallback_message(
    task: dict[str, Any],
    message_id: str | None,
    conversation_id: str | None,
) -> dict[str, Any]:
    task_type = task.get("type")
    if task_type == "chat":
        blocks = task.get("accumulated_blocks") or []
        text = task.get("accumulated_content", "")
        if blocks:
            from services.task_utils import merge_blocks_with_text

            content = merge_blocks_with_text(blocks, text)
        else:
            content = [{"type": "text", "text": text}]
    elif task_type == "image":
        urls = task.get("result", {}).get("image_urls", [])
        content = [{"type": "image", "url": url} for url in urls]
    elif task_type == "video":
        video_url = task.get("result", {}).get("video_url")
        content = [{"type": "video", "url": video_url}] if video_url else []
    else:
        content = []
    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": content,
        "status": "completed",
        "credits_cost": task.get("credits_locked", 0),
    }
