"""Fail-closed cleanup for prepared Web media requests."""

from __future__ import annotations

import inspect
from typing import Any, Literal, NoReturn, Sequence

from core.exceptions import AppException
from schemas.message import MessageOperation, MessageStatus
from services.task_limit_service import extract_slot_id, release_task_slot_checked


class RuntimeMediaNotOwned(RuntimeError):
    """A conclusive Runtime ingress receipt declined media ownership."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        super().__init__(f"RUNTIME_MEDIA_{outcome.upper()}")


class RuntimeMediaPartialOwnership(AppException):
    """A batch has mixed Runtime ownership and requires reconciliation."""

    def __init__(self) -> None:
        super().__init__(
            code="RUNTIME_MEDIA_PARTIAL_OWNERSHIP_RECONCILE_REQUIRED",
            message="图片批次接管状态不一致，请先完成取消或对账",
            status_code=409,
        )


class RuntimeMediaOwnershipReconcileRequired(AppException):
    """Submission failed without a conclusive no-ownership readback."""

    def __init__(self, ownership: str) -> None:
        super().__init__(
            code="RUNTIME_MEDIA_OWNERSHIP_RECONCILE_REQUIRED",
            message="图片批次接管结果未知，请先完成读取或取消对账",
            status_code=503,
            details={"ownership": ownership, "reconcile_required": True},
        )


async def read_prepared_media_ownership(
    db: Any, task_ids: Sequence[str],
) -> Literal["none", "full", "partial", "unknown"]:
    """Read the task-side ownership facts committed atomically by Runtime v2."""
    try:
        response = db.table("tasks").select(
            "id,status,external_task_id,credit_transaction_id,delivery_context",
        ).in_("id", list(task_ids)).execute()
        if inspect.isawaitable(response):
            response = await response
    except Exception:
        return "unknown"
    rows = getattr(response, "data", None)
    if not isinstance(rows, list) or len(rows) != len(task_ids):
        return "unknown"
    by_id = {
        str(row.get("id")): row for row in rows
        if isinstance(row, dict) and row.get("id") is not None
    }
    if set(by_id) != set(task_ids):
        return "unknown"

    evidence_count = 0
    for task_id in task_ids:
        row = by_id[task_id]
        context = row.get("delivery_context")
        if not isinstance(context, dict):
            return "unknown"
        has_runtime_evidence = (
            context.get("runtime") is True
            or any(context.get(key) for key in (
                "runtime_owner", "runtime_action_id", "runtime_run_id",
            ))
        )
        evidence_count += int(has_runtime_evidence)
        if not has_runtime_evidence and (
            row.get("status") != "preparing"
            or row.get("external_task_id") is not None
            or row.get("credit_transaction_id") is not None
        ):
            return "unknown"
    if evidence_count == 0:
        return "none"
    if evidence_count == len(task_ids):
        return "full"
    return "partial"


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

    if media_kind == "image" and operation != MessageOperation.REGENERATE_SINGLE:
        content = [{
            "type": "image", "url": None, "failed": True,
            "error": error_message,
        } for _ in task_ids]
        db.table("messages").update({
            "content": content,
            "status": MessageStatus.FAILED.value,
            "is_error": False,
        }).eq("id", message_id).execute()
    elif media_kind == "image":
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
    "RuntimeMediaOwnershipReconcileRequired",
    "RuntimeMediaPartialOwnership",
    "fail_closed_prepared_media",
    "read_prepared_media_ownership",
]
