"""One-shot compatibility Projection worker without production wiring."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loguru import logger

from schemas.websocket import build_message_done, build_message_error
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.compat_projection import (
    PostgresCompatibilityProjection,
)
from services.agent.runtime.ports.projection import ProjectionClaim
from services.agent.runtime.projection import classify_event
from services.message_utils import format_message


class CompatibilityProjectionWorker:
    """Project claims and notify clients after durable terminal projection."""

    def __init__(
        self,
        projection: PostgresCompatibilityProjection,
        notifier: "CompatibilityProjectionNotifier | None" = None,
    ) -> None:
        self._projection = projection
        self._notifier = notifier

    async def run_once(self, batch_size: int = 50) -> int:
        claims = await self._projection.claim(batch_size=batch_size)
        for claim in claims:
            await self._process(claim)
        return len(claims)

    async def _process(self, claim: ProjectionClaim) -> None:
        event = claim.event
        try:
            projection = classify_event(event)
            result = await self._projection.apply(claim, projection.action.value)
            await self._notify_terminal(claim, result)
        except PersistenceContractError as error:
            await self._projection.fail(
                claim, _error_code("contract", error),
            )
        except Exception as error:
            if await self._projection.readback(claim) is not None:
                return
            logger.warning(
                "compat_projection_retry | "
                f"outbox_id={claim.outbox_id} | "
                f"event_id={event.event_id} | error={type(error).__name__}",
            )
            await self._projection.fail(
                claim, _error_code("apply", error),
            )

    async def _notify_terminal(
        self, claim: ProjectionClaim, result: Mapping[str, object],
    ) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier.notify(claim, result)
        except Exception as error:
            # The DB projection is authoritative. Notification failure must
            # never roll back or retry a committed Runtime terminal state.
            logger.warning(
                "compat_projection_ws_failed | outbox_id={} | event_id={} | error={}",
                claim.outbox_id, claim.event.event_id, type(error).__name__,
            )


def _error_code(prefix: str, error: Exception) -> str:
    return f"{prefix}_{type(error).__name__}".lower()[:200]


class CompatibilityProjectionNotifier:
    """Publish existing task terminal events after the DB projection commits."""

    _TERMINAL_ACTIONS = {"run_completed", "run_failed", "run_cancelled"}

    def __init__(self, database: Any, websocket_manager: Any) -> None:
        self._database = database
        self._websocket_manager = websocket_manager

    async def notify(
        self, claim: ProjectionClaim, projection_result: Mapping[str, object],
    ) -> None:
        result = projection_result.get("result")
        if not isinstance(result, Mapping):
            return
        action = result.get("projection_action")
        if action not in self._TERMINAL_ACTIONS:
            return
        task_id = _required_text(result.get("task_id"), "task_id")
        message_id = _required_text(result.get("message_id"), "message_id")
        payload = await self._load_terminal_payload(task_id, message_id)
        task = payload["task"]
        message = payload["message"]
        push_task_id = _push_task_id(task)
        conversation_id = _required_text(task.get("conversation_id"), "conversation_id")
        user_id = _required_text(task.get("user_id"), "user_id")
        if action == "run_completed":
            event = build_message_done(
                task_id=push_task_id,
                conversation_id=conversation_id,
                message=_format_message(message),
                credits_consumed=int(message.get("credits_cost") or 0),
            )
        else:
            event = build_message_error(
                task_id=push_task_id,
                conversation_id=conversation_id,
                message_id=message_id,
                error_code=(
                    "TASK_CANCELLED" if action == "run_cancelled"
                    else "GENERATION_FAILED"
                ),
                error_message=(
                    "任务已取消" if action == "run_cancelled"
                    else str(task.get("error_message") or "生成失败")
                ),
            )
        await self._websocket_manager.send_to_task_or_user(
            task_id=push_task_id,
            user_id=user_id,
            message=event,
            org_id=task.get("org_id"),
        )

    async def _load_terminal_payload(
        self, task_id: str, message_id: str,
    ) -> dict[str, Mapping[str, object]]:
        response = await self._database.rpc(
            "get_agent_runtime_web_terminal_notification_v1",
            {"p_task_id": task_id, "p_message_id": message_id},
        ).execute()
        payload = response.data
        if not isinstance(payload, Mapping) or payload.get("outcome") != "found":
            raise RuntimeError("RUNTIME_PROJECTION_TERMINAL_PAYLOAD_MISSING")
        task = payload.get("task")
        message = payload.get("message")
        if not isinstance(task, Mapping) or not isinstance(message, Mapping):
            raise RuntimeError("RUNTIME_PROJECTION_TERMINAL_PAYLOAD_INVALID")
        return {"task": task, "message": message}


def _format_message(message: Mapping[str, object]) -> dict[str, object]:
    return format_message(dict(message))


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"RUNTIME_PROJECTION_{name.upper()}_REQUIRED")
    return value


def _push_task_id(task: Mapping[str, object]) -> str:
    return _required_text(
        task.get("client_task_id")
        or task.get("external_task_id")
        or task.get("id"),
        "push_task_id",
    )
