"""Web boundary for Runtime image message cancellation and slot retry."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from core.exceptions import AppException


@dataclass(frozen=True, kw_only=True)
class RuntimeMediaRetryReceipt:
    action_id: str
    run_id: str
    task_id: str
    slot_id: str
    slot_index: int
    slot_revision: int
    replayed: bool


class RuntimeMediaMessageControlService:
    """Call only the tenant-bound 228.07 RPC surface."""

    def __init__(self, database: Any, *, user_id: str, org_id: str | None) -> None:
        self._database = database
        self._user_id = user_id
        self._org_id = org_id

    async def cancel_message(
        self,
        message_id: str,
        *,
        idempotency_key: str,
        release_slot: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any] | None:
        data = await self._rpc(
            "request_agent_runtime_media_message_cancel_v1",
            {
                "p_output_message_id": message_id,
                "p_org_id": self._org_id,
                "p_user_id": self._user_id,
                "p_idempotency_key": idempotency_key,
            },
            unavailable_code="RUNTIME_MEDIA_CANCEL_UNAVAILABLE",
        )
        outcome = data.get("outcome")
        if outcome == "not_runtime_media":
            return None
        if outcome not in {"cancel_requested", "already_terminal"}:
            raise AppException(
                code="RUNTIME_MEDIA_CANCEL_CONFLICT",
                message="图片任务状态已变化，无法取消",
                status_code=409,
            )
        followup_failed = await self._release_chat_slots(
            data.get("release_task_ids"), release_slot,
        )
        return {
            "success": True,
            "runtime_media": True,
            "outcome": outcome,
            "cancelled_count": _integer(data.get("cancelled_count")),
            "reconcile_count": _integer(data.get("reconcile_count")),
            "completed_count": _integer(data.get("completed_count")),
            "partial": _integer(data.get("reconcile_count")) > 0,
            **({"failure_codes": ["SLOT_RELEASE_FAILED"]}
               if followup_failed else {}),
        }

    async def retry_slot(
        self,
        message_id: str,
        conversation_id: str,
        slot_index: int,
        *,
        slot_id: str,
        expected_slot_revision: int,
        idempotency_key: str,
        client_task_id: str | None,
        task_slot_id: str | None,
    ) -> RuntimeMediaRetryReceipt | None:
        data = await self._rpc(
            "retry_agent_runtime_media_slot_v1",
            {
                "p_output_message_id": message_id,
                "p_conversation_id": conversation_id,
                "p_slot_index": slot_index,
                "p_slot_id": slot_id,
                "p_expected_slot_revision": expected_slot_revision,
                "p_org_id": self._org_id,
                "p_user_id": self._user_id,
                "p_idempotency_key": idempotency_key,
                "p_client_task_id": client_task_id,
                "p_task_slot_id": task_slot_id,
            },
            unavailable_code="RUNTIME_MEDIA_RETRY_UNAVAILABLE",
        )
        outcome = data.get("outcome")
        if outcome == "not_runtime_media":
            return None
        errors = {
            "slot_active": (
                "RUNTIME_MEDIA_SLOT_ACTIVE", "该图片仍在处理中，请等待结果确认", 409,
            ),
            "slot_completed": (
                "RUNTIME_MEDIA_SLOT_COMPLETED", "已完成的图片默认不重复生成", 409,
            ),
            "slot_not_found": (
                "RUNTIME_MEDIA_SLOT_NOT_FOUND", "未找到可重试的图片槽位", 404,
            ),
            "projection_pending": (
                "RUNTIME_MEDIA_PROJECTION_PENDING", "图片状态正在结算，请稍后重试", 409,
            ),
            "slot_conflict": (
                "RUNTIME_MEDIA_SLOT_CONFLICT", "图片状态已变化，请刷新后重试", 409,
            ),
        }
        if outcome in errors:
            code, message, status = errors[outcome]
            raise AppException(code=code, message=message, status_code=status)
        if outcome not in {"created", "already_created"}:
            raise AppException(
                code="RUNTIME_MEDIA_RETRY_CONFLICT",
                message="图片状态已变化，无法重试",
                status_code=409,
            )
        return RuntimeMediaRetryReceipt(
            action_id=_text(data.get("action_id")),
            run_id=_text(data.get("run_id")),
            task_id=_text(data.get("task_id") or data.get("action_id")),
            slot_id=_text(data.get("slot_id")),
            slot_index=_integer(data.get("slot_index")),
            slot_revision=_integer(data.get("slot_revision")),
            replayed=outcome == "already_created",
        )

    async def _rpc(
        self, name: str, params: dict[str, object], *, unavailable_code: str,
    ) -> Mapping[str, object]:
        try:
            response = self._database.rpc(name, params).execute()
            if inspect.isawaitable(response):
                response = await response
        except AppException:
            raise
        except Exception as exc:
            state = _sqlstate(exc)
            if state in {"22023", "23505", "40001", "42501", "55000"}:
                raise AppException(
                    code=unavailable_code.replace("UNAVAILABLE", "CONFLICT"),
                    message="图片任务状态已变化，请刷新后重试",
                    status_code=409,
                ) from None
            if state == "P0001":
                raise AppException(
                    code="INSUFFICIENT_CREDITS",
                    message="积分不足",
                    status_code=400,
                ) from None
            raise AppException(
                code=unavailable_code,
                message="图片任务服务暂时不可用",
                status_code=503,
            ) from None
        data = getattr(response, "data", None)
        if not isinstance(data, Mapping) or not isinstance(data.get("outcome"), str):
            raise AppException(
                code=unavailable_code,
                message="图片任务服务暂时不可用",
                status_code=503,
            )
        return data

    async def _release_chat_slots(
        self,
        task_ids: object,
        release_slot: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> bool:
        if not isinstance(task_ids, list):
            return False
        failed = False
        for task_id in dict.fromkeys(str(value) for value in task_ids):
            try:
                response = self._database.table("tasks").select(
                    "id,user_id,org_id,conversation_id,request_params",
                ).eq("id", task_id).maybe_single().execute()
                task = getattr(response, "data", None)
                if isinstance(task, dict):
                    await release_slot(task)
            except Exception:
                failed = True
        return failed


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise AppException(
            code="RUNTIME_MEDIA_CONTROL_RECEIPT_INVALID",
            message="图片任务服务暂时不可用",
            status_code=503,
        )
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sqlstate(exc: Exception) -> str | None:
    for candidate in (exc, exc.__cause__, exc.__context__):
        state = getattr(candidate, "sqlstate", None)
        if isinstance(state, str):
            return state
    return None


__all__ = ["RuntimeMediaMessageControlService", "RuntimeMediaRetryReceipt"]
