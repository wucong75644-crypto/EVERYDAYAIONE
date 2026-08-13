"""Aggregate Web task cancellation without hiding committed partial results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from loguru import logger

from core.exceptions import AppException
from services.agent.runtime.infrastructure.postgres.task_cancel_repository import (
    PostgresRuntimeTaskCancelRepository,
)
from services.agent.runtime.task_cancel import (
    RuntimeTaskCancelConflict,
    RuntimeTaskCancelRequest,
    RuntimeTaskCancelService,
    RuntimeTaskCancelUnavailable,
    TaskOwner,
    classify_task_owner,
)


_TASK_FIELDS = (
    "id, external_task_id, client_task_id, user_id, conversation_id, "
    "org_id, request_params, delivery_context, assistant_message_id"
)


@dataclass(frozen=True)
class _CancelPlan:
    task: dict[str, Any]
    owner: TaskOwner
    runtime_request: RuntimeTaskCancelRequest | None
    anchor_message_id: str


@dataclass(frozen=True)
class _Failure:
    code: str
    status_code: int


class WebTaskCancelService:
    """Union aliases, preflight every row, then aggregate durable outcomes."""

    def __init__(
        self, database: Any, *, user_id: str, org_id: str | None,
        release_slot: Callable[[dict[str, Any]], Awaitable[None]],
        anchor_message: Callable[[Any, str, str | None], bool],
    ) -> None:
        self._database = database
        self._user_id = user_id
        self._org_id = org_id
        self._release_slot = release_slot
        self._anchor_message = anchor_message
        self._runtime = RuntimeTaskCancelService(
            PostgresRuntimeTaskCancelRepository(database),
        )

    async def cancel_by_message(self, message_id: str) -> dict[str, Any]:
        from services.runtime_media_message_control import (
            RuntimeMediaMessageControlService,
        )
        media_result = await RuntimeMediaMessageControlService(
            self._database, user_id=self._user_id, org_id=self._org_id,
        ).cancel_message(
            message_id,
            idempotency_key=f"web-media-message-cancel:{message_id}",
            release_slot=self._release_slot,
        )
        if media_result is not None:
            return media_result
        plans = self._preflight(self._load_union(message_id), message_id)
        successes: list[_CancelPlan] = []
        failures: list[_Failure] = []
        for plan in plans:
            durable, failure = self._apply_durable(plan)
            if durable:
                successes.append(plan)
                logger.info(
                    "Task cancelled by user | task_id={} | ext={} | "
                    "message_id={} | user_id={}",
                    plan.task.get("id"),
                    plan.task.get("client_task_id")
                    or plan.task.get("external_task_id"),
                    message_id, self._user_id,
                )
            elif failure is not None:
                failures.append(failure)
        if not successes and failures:
            raise self._failure_exception(failures)
        followup_codes: list[str] = []
        for plan in successes:
            followup_codes.extend(await self._run_followups(plan))
        return self._response(successes, failures, followup_codes)

    def _load_union(self, message_id: str) -> list[dict[str, Any]]:
        tasks: dict[str, dict[str, Any]] = {}
        try:
            for field in ("placeholder_message_id", "assistant_message_id"):
                query = self._database.table("tasks").select(_TASK_FIELDS).eq(
                    field, message_id,
                ).eq("user_id", self._user_id).in_(
                    "status", ["pending", "running"],
                )
                query = (
                    query.eq("org_id", self._org_id) if self._org_id
                    else query.is_("org_id", "null")
                )
                result = query.execute()
                for row in result.data or []:
                    task = dict(row)
                    task_id = task.get("id")
                    if not isinstance(task_id, str) or not task_id:
                        raise self._conflict("TASK_CANCEL_BINDING_INVALID")
                    previous = tasks.get(task_id)
                    if previous is not None and previous != task:
                        raise self._conflict("TASK_CANCEL_BINDING_INVALID")
                    tasks.setdefault(task_id, task)
        except AppException:
            raise
        except Exception:
            raise AppException(
                code="CANCEL_TASK_QUERY_UNAVAILABLE",
                message="取消任务查询暂时不可用",
                status_code=503,
            ) from None
        return list(tasks.values())

    def _preflight(
        self, tasks: list[dict[str, Any]], message_id: str,
    ) -> list[_CancelPlan]:
        plans: list[_CancelPlan] = []
        for task in tasks:
            if (
                str(task.get("user_id")) != self._user_id
                or task.get("org_id") != self._org_id
            ):
                raise self._conflict("TASK_CANCEL_BINDING_INVALID")
            owner = classify_task_owner(task)
            if owner is TaskOwner.AMBIGUOUS:
                raise AppException(
                    code="TASK_OWNER_MARKER_INVALID",
                    message="任务归属标记异常，无法取消",
                    status_code=409,
                )
            request = None
            anchor_id = message_id
            if owner is TaskOwner.RUNTIME:
                try:
                    request = self._runtime.prepare_task(
                        task, user_id=self._user_id, org_id=self._org_id,
                    )
                except RuntimeTaskCancelConflict:
                    raise self._conflict("RUNTIME_TASK_CANCEL_CONFLICT") from None
                anchor_id = request.message_id
            plans.append(_CancelPlan(task, owner, request, anchor_id))
        return plans

    def _apply_durable(
        self, plan: _CancelPlan,
    ) -> tuple[bool, _Failure | None]:
        try:
            if plan.owner is TaskOwner.RUNTIME:
                if plan.runtime_request is None:
                    return False, _Failure("RUNTIME_TASK_CANCEL_CONFLICT", 409)
                self._runtime.cancel_prepared(plan.runtime_request)
                return True, None
            if plan.owner is TaskOwner.ACTOR:
                from services.conversation_task import cancel_actor_task
                return cancel_actor_task(
                    self._database, plan.task, self._user_id, self._org_id,
                ), None
            self._database.table("tasks").update({
                "status": "failed",
                "error_message": "用户取消了任务",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", plan.task["id"]).execute()
            return True, None
        except RuntimeTaskCancelConflict:
            return False, _Failure("RUNTIME_TASK_CANCEL_CONFLICT", 409)
        except RuntimeTaskCancelUnavailable:
            return False, _Failure("RUNTIME_TASK_CANCEL_UNAVAILABLE", 503)
        except Exception:
            return False, _Failure("CANCEL_TASK_BY_MESSAGE_ERROR", 500)

    async def _run_followups(self, plan: _CancelPlan) -> list[str]:
        codes: list[str] = []
        external_id = (
            plan.task.get("client_task_id")
            or plan.task.get("external_task_id")
        )
        if external_id:
            try:
                from services.cancel_metrics import (
                    mark_cancel_start,
                    record_cancel_event,
                )
                from services.websocket_manager import ws_manager
                mark_cancel_start(external_id)
                record_cancel_event(external_id, org_id=self._org_id)
                ws_manager.cancel_task(external_id, org_id=self._org_id)
            except Exception:
                codes.append("WS_CANCEL_FAILED")
        try:
            await self._release_slot(plan.task)
        except Exception:
            codes.append("SLOT_RELEASE_FAILED")
        try:
            anchored = self._anchor_message(
                self._database, plan.anchor_message_id,
                plan.task.get("conversation_id"),
            )
            if anchored is False:
                codes.append("ANCHOR_FAILED")
        except Exception:
            codes.append("ANCHOR_FAILED")
        for code in codes:
            logger.warning(
                "Task cancel follow-up failed | task_id={} | code={}",
                plan.task.get("id"), code,
            )
        return codes

    @staticmethod
    def _response(
        successes: list[_CancelPlan], failures: list[_Failure],
        followup_codes: list[str],
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "success": True,
            "cancelled_count": len(successes),
        }
        if failures or followup_codes:
            response.update({
                "failed_count": len(failures),
                "partial": True,
                "failure_codes": list(dict.fromkeys(
                    [failure.code for failure in failures] + followup_codes,
                )),
                "followup_failed_count": len(followup_codes),
            })
        return response

    @classmethod
    def _failure_exception(cls, failures: list[_Failure]) -> AppException:
        failure = next(
            (item for item in failures if item.status_code == 503),
            failures[0],
        )
        messages = {
            409: "任务状态已变化，无法取消",
            503: "取消结果暂时无法确认，请稍后重试",
        }
        return AppException(
            code=failure.code,
            message=messages.get(failure.status_code, "取消任务失败"),
            status_code=failure.status_code,
        )

    @staticmethod
    def _conflict(code: str) -> AppException:
        return AppException(
            code=code,
            message="任务状态已变化，无法取消",
            status_code=409,
        )
