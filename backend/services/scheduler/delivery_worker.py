"""定时任务企微结果的持久化 Outbox 投递 Worker。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from loguru import logger

from services.wecom.delivery_sender import WecomDeliveryItem, WecomDeliverySender


_OWNERSHIP_LOST = {"ownership_lost", "lease_expired"}


@dataclass(frozen=True)
class ScheduledTaskDeliveryClaim:
    delivery_id: str
    run_id: str
    delivery_kind: str
    lease_token: str
    org_id: str
    target_context: Mapping[str, Any]
    payload: Mapping[str, Any]

    @classmethod
    def from_result(cls, result: Mapping[str, Any]) -> "ScheduledTaskDeliveryClaim":
        required = ("delivery_id", "run_id", "lease_token", "org_id")
        if result.get("outcome") != "claimed" or any(
            not result.get(key) for key in required
        ):
            raise RuntimeError("SCHEDULED_TASK_DELIVERY_CLAIM_INVALID")
        target = result.get("target_context")
        payload = result.get("payload")
        kind = str(result.get("delivery_kind") or "")
        if (
            not isinstance(target, Mapping)
            or not isinstance(payload, Mapping)
            or kind not in {"result", "owner_alert"}
            or target.get("type") not in {"wecom_user", "wecom_group"}
            or not target.get("chatid")
        ):
            raise RuntimeError("SCHEDULED_TASK_DELIVERY_CLAIM_INVALID")
        return cls(
            delivery_id=str(result["delivery_id"]),
            run_id=str(result["run_id"]),
            delivery_kind=kind,
            lease_token=str(result["lease_token"]),
            org_id=str(result["org_id"]),
            target_context=target,
            payload=payload,
        )


class ScheduledTaskDeliveryWorker:
    """从数据库租约领取定时企微投递，确保 WS 断线后仍会重试。"""

    def __init__(
        self,
        db: Any,
        sender: WecomDeliverySender,
        *,
        poll_interval_seconds: float = 2,
        lease_seconds: int = 120,
        max_attempts: int = 8,
        send_timeout_seconds: float = 30,
    ) -> None:
        if (
            poll_interval_seconds <= 0
            or not 15 <= lease_seconds <= 300
            or send_timeout_seconds <= 0
            or send_timeout_seconds >= lease_seconds
        ):
            raise ValueError("invalid scheduled delivery worker timing")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._db = db
        self._sender = sender
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._send_timeout = send_timeout_seconds
        self._running = False
        self._wake_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("ScheduledTaskDeliveryWorker started")
        try:
            while self._running:
                processed = await self._run_safely()
                if processed:
                    continue
                await self._wait_for_next_poll()
        finally:
            logger.info("ScheduledTaskDeliveryWorker stopped")

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()

    async def run_once(self) -> bool:
        result = await self._rpc(
            "claim_scheduled_task_delivery",
            {
                "p_lease_seconds": self._lease_seconds,
                "p_max_attempts": self._max_attempts,
            },
        )
        if result.get("outcome") == "empty":
            return False
        await self._process(ScheduledTaskDeliveryClaim.from_result(result))
        return True

    async def _run_safely(self) -> bool:
        try:
            return await self.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "scheduled_task_delivery_scan_failed | "
                f"error={type(error).__name__}"
            )
            return False

    async def _wait_for_next_poll(self) -> None:
        self._wake_event.clear()
        try:
            await asyncio.wait_for(
                self._wake_event.wait(), timeout=self._poll_interval,
            )
        except asyncio.TimeoutError:
            pass

    async def _process(self, claim: ScheduledTaskDeliveryClaim) -> None:
        try:
            sent = await asyncio.wait_for(
                self._sender.send(
                    {
                        "org_id": claim.org_id,
                        "transport": "smart_robot",
                        "chatid": str(claim.target_context["chatid"]),
                    },
                    WecomDeliveryItem(
                        key=f"scheduled:{claim.delivery_id}",
                        kind="text",
                        content=_format_payload(claim.payload),
                    ),
                ),
                timeout=self._send_timeout,
            )
            if not sent:
                raise RuntimeError("WECOM_WS_UNAVAILABLE")
            await self._complete(claim)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._fail(claim, error)

    async def _complete(self, claim: ScheduledTaskDeliveryClaim) -> None:
        result = await self._rpc(
            "complete_scheduled_task_delivery",
            {
                "p_delivery_id": claim.delivery_id,
                "p_lease_token": claim.lease_token,
            },
        )
        if result.get("outcome") in {"delivered", "already_delivered"}:
            return
        if result.get("outcome") in _OWNERSHIP_LOST:
            logger.warning(
                "scheduled_task_delivery_ownership_lost | "
                f"delivery_id={claim.delivery_id}"
            )
            return
        raise RuntimeError("SCHEDULED_TASK_DELIVERY_COMPLETE_INVALID")

    async def _fail(
        self, claim: ScheduledTaskDeliveryClaim, error: Exception,
    ) -> None:
        try:
            result = await self._rpc(
                "fail_scheduled_task_delivery",
                {
                    "p_delivery_id": claim.delivery_id,
                    "p_lease_token": claim.lease_token,
                    "p_error": f"{type(error).__name__}: {error}",
                    "p_max_attempts": self._max_attempts,
                },
            )
        except Exception as fail_error:
            logger.error(
                "scheduled_task_delivery_fail_recording_failed | "
                f"delivery_id={claim.delivery_id} | "
                f"error={type(fail_error).__name__}"
            )
            return
        outcome = result.get("outcome")
        if outcome in _OWNERSHIP_LOST:
            logger.warning(
                "scheduled_task_delivery_ownership_lost | "
                f"delivery_id={claim.delivery_id}"
            )
            return
        level = logger.critical if outcome == "dead" else logger.warning
        level(
            "scheduled_task_delivery_failed | "
            f"delivery_id={claim.delivery_id} | run_id={claim.run_id} | "
            f"org_id={claim.org_id} | outcome={outcome} | "
            f"error={type(error).__name__}"
        )

    async def _rpc(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await self._db.rpc(name, params).execute()
        if not response or not isinstance(response.data, dict):
            raise RuntimeError(f"SCHEDULED_TASK_DELIVERY_RPC_INVALID:{name}")
        return response.data


def _format_payload(payload: Mapping[str, Any]) -> str:
    text = str(payload.get("text") or "")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return text
    lines = [text, "", "📎 **附件：**"]
    for file in files:
        if not isinstance(file, Mapping):
            continue
        lines.append(
            f"- [{file.get('name', '附件')}]({file.get('url', '')})"
        )
    return "\n".join(lines)
