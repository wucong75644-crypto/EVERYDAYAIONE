"""Conversation Actor 企微 Transactional Outbox 投递 Worker。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from loguru import logger
from psycopg.types.json import Jsonb

from services.wecom.delivery_sender import WecomDeliverySender


_OWNERSHIP_LOST = {"ownership_lost", "lease_expired"}


@dataclass(frozen=True)
class WecomDeliveryClaim:
    delivery_id: str
    task_id: str
    delivery_kind: str
    lease_token: str
    target_context: Mapping[str, Any]
    delivered_items: frozenset[str]

    @classmethod
    def from_result(cls, result: Mapping[str, Any]) -> "WecomDeliveryClaim":
        required = ("delivery_id", "task_id", "lease_token")
        if result.get("outcome") != "claimed" or any(
            not result.get(key) for key in required
        ):
            raise RuntimeError("WECOM_DELIVERY_CLAIM_INVALID")
        context = result.get("target_context")
        items = result.get("delivered_items")
        if not isinstance(context, Mapping) or not isinstance(items, list):
            raise RuntimeError("WECOM_DELIVERY_CLAIM_INVALID")
        delivery_kind = str(
            result.get("delivery_kind") or "assistant_terminal"
        )
        if delivery_kind not in {"assistant_terminal", "web_user_message"}:
            raise RuntimeError("WECOM_DELIVERY_CLAIM_INVALID")
        return cls(
            delivery_id=str(result["delivery_id"]),
            task_id=str(result["task_id"]),
            delivery_kind=delivery_kind,
            lease_token=str(result["lease_token"]),
            target_context=context,
            delivered_items=frozenset(str(item) for item in items),
        )


class WecomDeliveryWorker:
    """单进程有界轮询 Outbox；数据库租约负责崩溃恢复和 fencing。"""

    def __init__(
        self,
        db: Any,
        sender: WecomDeliverySender,
        *,
        poll_interval_seconds: float = 2,
        lease_seconds: int = 120,
        max_attempts: int = 8,
    ) -> None:
        if poll_interval_seconds <= 0 or not 15 <= lease_seconds <= 300:
            raise ValueError("invalid delivery worker timing")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._db = db
        self._sender = sender
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._running = False
        self._wake_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("WecomDeliveryWorker started")
        try:
            while self._running:
                processed = await self._run_safely()
                if processed:
                    continue
                await self._wait_for_next_poll()
        finally:
            logger.info("WecomDeliveryWorker stopped")

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()

    async def run_once(self) -> bool:
        result = await self._rpc(
            "claim_conversation_delivery",
            {
                "p_lease_seconds": self._lease_seconds,
                "p_max_attempts": self._max_attempts,
            },
        )
        if result.get("outcome") == "empty":
            return False
        claim = WecomDeliveryClaim.from_result(result)
        await self._process(claim)
        return True

    async def _run_safely(self) -> bool:
        try:
            return await self.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "wecom_delivery_scan_failed | "
                f"error={type(error).__name__}"
            )
            return False

    async def _wait_for_next_poll(self) -> None:
        self._wake_event.clear()
        try:
            await asyncio.wait_for(
                self._wake_event.wait(),
                timeout=self._poll_interval,
            )
        except asyncio.TimeoutError:
            pass

    async def _process(self, claim: WecomDeliveryClaim) -> None:
        checkpoints = set(claim.delivered_items)
        try:
            task = await self._load_row("tasks", claim.task_id)
            message = await self._load_message(task, claim.delivery_kind)
            for item in self._sender.build_items(
                task,
                message,
                claim.target_context,
                delivery_kind=claim.delivery_kind,
            ):
                if item.key in checkpoints:
                    continue
                if not await self._send_with_lease(claim, item):
                    raise RuntimeError(f"WECOM_SEND_FAILED:{item.key}")
                checkpoints.add(item.key)
                await self._checkpoint(claim, checkpoints)
            await self._complete(claim, checkpoints)
        except _DeliveryOwnershipLost:
            logger.warning(
                f"wecom_delivery_ownership_lost | delivery_id={claim.delivery_id}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._fail(claim, checkpoints, error)

    async def _send_with_lease(
        self,
        claim: WecomDeliveryClaim,
        item: Any,
    ) -> bool:
        send_task = asyncio.create_task(
            self._sender.send(claim.target_context, item)
        )
        try:
            while True:
                done, _ = await asyncio.wait(
                    {send_task},
                    timeout=self._lease_seconds / 3,
                )
                if done:
                    return bool(await send_task)
                await self._renew(claim)
        finally:
            if not send_task.done():
                send_task.cancel()
                await asyncio.gather(send_task, return_exceptions=True)

    async def _renew(self, claim: WecomDeliveryClaim) -> None:
        result = await self._rpc(
            "renew_conversation_delivery",
            {
                "p_delivery_id": claim.delivery_id,
                "p_lease_token": claim.lease_token,
                "p_lease_seconds": self._lease_seconds,
                "p_delivered_items": None,
            },
        )
        if result.get("outcome") in _OWNERSHIP_LOST:
            raise _DeliveryOwnershipLost
        if result.get("outcome") != "renewed":
            raise RuntimeError("WECOM_DELIVERY_RENEW_INVALID")

    async def _checkpoint(
        self,
        claim: WecomDeliveryClaim,
        checkpoints: set[str],
    ) -> None:
        result = await self._rpc(
            "renew_conversation_delivery",
            {
                "p_delivery_id": claim.delivery_id,
                "p_lease_token": claim.lease_token,
                "p_lease_seconds": self._lease_seconds,
                "p_delivered_items": Jsonb(sorted(checkpoints)),
            },
        )
        if result.get("outcome") in _OWNERSHIP_LOST:
            raise _DeliveryOwnershipLost
        if result.get("outcome") != "renewed":
            raise RuntimeError("WECOM_DELIVERY_RENEW_INVALID")

    async def _complete(
        self,
        claim: WecomDeliveryClaim,
        checkpoints: set[str],
    ) -> None:
        result = await self._rpc(
            "complete_conversation_delivery",
            {
                "p_delivery_id": claim.delivery_id,
                "p_lease_token": claim.lease_token,
                "p_delivered_items": Jsonb(sorted(checkpoints)),
            },
        )
        if result.get("outcome") in _OWNERSHIP_LOST:
            raise _DeliveryOwnershipLost
        if result.get("outcome") not in {"delivered", "already_delivered"}:
            raise RuntimeError("WECOM_DELIVERY_COMPLETE_INVALID")

    async def _fail(
        self,
        claim: WecomDeliveryClaim,
        checkpoints: set[str],
        error: Exception,
    ) -> None:
        result = await self._rpc(
            "fail_conversation_delivery",
            {
                "p_delivery_id": claim.delivery_id,
                "p_lease_token": claim.lease_token,
                "p_error": f"{type(error).__name__}: {error}",
                "p_delivered_items": Jsonb(sorted(checkpoints)),
                "p_max_attempts": self._max_attempts,
            },
        )
        outcome = result.get("outcome")
        level = logger.critical if outcome == "dead" else logger.warning
        level(
            "wecom_delivery_failed | "
            f"delivery_id={claim.delivery_id} | task_id={claim.task_id} | "
            f"org_id={claim.target_context.get('org_id')} | outcome={outcome} | "
            f"error={type(error).__name__}"
        )

    async def _load_message(
        self,
        task: Mapping[str, Any],
        delivery_kind: str,
    ) -> Mapping[str, Any] | None:
        if (
            delivery_kind == "assistant_terminal"
            and task.get("status") == "failed"
        ):
            return None
        message_field = (
            "input_message_id"
            if delivery_kind == "web_user_message"
            else "assistant_message_id"
        )
        message_id = task.get(message_field)
        if not message_id:
            raise RuntimeError("WECOM_DELIVERY_MESSAGE_ID_MISSING")
        return await self._load_row("messages", str(message_id))

    async def _load_row(self, table: str, row_id: str) -> dict[str, Any]:
        result = await (
            self._db.table(table)
            .select("*")
            .eq("id", row_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise RuntimeError(f"WECOM_DELIVERY_{table.upper()}_MISSING")
        return dict(result.data)

    async def _rpc(
        self,
        name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._db.rpc(name, params).execute()
        if not response or not isinstance(response.data, dict):
            raise RuntimeError(f"WECOM_DELIVERY_RPC_INVALID:{name}")
        return response.data


class _DeliveryOwnershipLost(Exception):
    pass
