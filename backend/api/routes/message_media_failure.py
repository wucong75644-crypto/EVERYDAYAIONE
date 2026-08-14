"""Fail-closed cleanup for prepared Web media requests."""

from __future__ import annotations

from typing import Any, NoReturn, Sequence

from core.exceptions import AppException
from schemas.message import MessageOperation, MessageStatus
from services.task_limit_service import extract_slot_id, release_task_slot_checked


class RuntimeMediaNotOwned(RuntimeError):
    """A conclusive Runtime ingress receipt declined media ownership."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        super().__init__(f"RUNTIME_MEDIA_{outcome.upper()}")


async def fail_closed_prepared_media(
    *, db: Any, lifecycle: Any, task_ids: Sequence[str],
    task_payloads: Sequence[dict[str, Any]], message_id: str,
    operation: MessageOperation, params: dict[str, Any] | None,
    media_kind: str, outcome: str, org_id: str | None, user_id: str,
) -> NoReturn:
    """Close prepared state after Runtime conclusively declines ownership."""
    error_message = "生成服务暂未就绪，请稍后重试"
    for task_id in task_ids:
        lifecycle.fail_prepared_task(
            task_id=task_id, terminal_reason="runtime_media_unavailable",
            error_message=error_message, org_id=org_id, user_id=user_id,
        )

    if media_kind == "image":
        from api.routes.message_generation_helpers import finalize_image_request_failure

        finalize_image_request_failure(
            db=db, message_id=message_id, operation=operation, params=params,
            error_code="RUNTIME_MEDIA_UNAVAILABLE", error_message=error_message,
        )
    else:
        db.table("messages").update({
            "content": [{"type": "text", "text": error_message}],
            "status": MessageStatus.FAILED.value,
            "is_error": True,
        }).eq("id", message_id).execute()

    slot_task = next((task for task in task_payloads if extract_slot_id(task)), None)
    if slot_task is not None:
        await release_task_slot_checked(slot_task)

    raise AppException(
        code="RUNTIME_MEDIA_UNAVAILABLE", message=error_message,
        status_code=503, details={"outcome": outcome},
    )


__all__ = [
    "RuntimeMediaNotOwned",
    "fail_closed_prepared_media",
]
