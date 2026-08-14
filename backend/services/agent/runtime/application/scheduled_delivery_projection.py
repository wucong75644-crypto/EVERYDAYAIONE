"""Durable Scheduled Runtime Web projection followed by best-effort wakeup."""

from __future__ import annotations

from loguru import logger

from services.agent.runtime.infrastructure.postgres.scheduled_delivery_projection import (
    PostgresScheduledDeliveryProjection,
    ScheduledWebProjectionClaim,
)


class ScheduledDeliveryProjectionWorker:
    """Projects DB facts once; WebSocket is only a recoverable refresh hint."""

    production_ready = False

    def __init__(
        self, projection: PostgresScheduledDeliveryProjection,
        websocket_manager: object,
    ) -> None:
        self._projection = projection
        self._websocket = websocket_manager

    async def run_once(self) -> bool:
        claim = await self._projection.claim()
        if claim is None:
            return False
        projected = await self._projection.apply(claim)
        delivered = False
        error_code = None
        try:
            delivered = bool(await self._websocket.send_to_user(
                projected.user_id,
                _wakeup_payload(projected),
                org_id=projected.org_id,
            ))
            if not delivered:
                error_code = "ws_not_connected"
        except Exception as error:
            error_code = _wakeup_error_code(error)
            logger.warning(
                "scheduled_web_wakeup_failed | intent_id={} | error_code={}",
                projected.intent_id,
                error_code,
            )
        await self._projection.complete_wakeup(
            projected, delivered=delivered, error_code=error_code,
        )
        return True


def _wakeup_payload(claim: ScheduledWebProjectionClaim) -> dict[str, object]:
    data: dict[str, object] = {
        "task_id": claim.task_id,
        "run_id": claim.scheduled_run_id,
        "status": claim.scheduled_run_status,
        "task_status": claim.task_status,
        "next_run_at": claim.next_run_at,
    }
    if claim.terminal_status == "completed":
        data["summary"] = claim.summary
        event_type = "scheduled_task_completed"
    else:
        data["reason"] = claim.reason_code or "scheduled_runtime_failed"
        data["consecutive_failures"] = claim.consecutive_failures
        event_type = "scheduled_task_failed"
    return {"type": event_type, "data": data}


def _wakeup_error_code(error: Exception) -> str:
    name = type(error).__name__.lower()
    safe = "".join(char if char.isalnum() else "_" for char in name)
    return f"ws_{safe}"[:80] or "ws_unavailable"
