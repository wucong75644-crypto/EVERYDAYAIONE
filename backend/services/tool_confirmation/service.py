"""Application service that owns creation, waiting, verification and claim."""

from __future__ import annotations

import secrets
import time
from typing import Any, Callable, Mapping

from loguru import logger

from core.redis import RedisClient
from services.tool_confirmation.canonical import canonical_arguments_hash
from services.tool_confirmation.preview import build_confirmation_summary
from services.tool_confirmation.redis_store import (
    ToolConfirmationRedisStore, hash_waiter_token,
)
from services.tool_confirmation.types import (
    ConfirmationBinding, ConfirmationDecision, ConfirmationOutcome,
    ConfirmationRequest,
)


def _matches(
    record: Mapping[str, str],
    binding: ConfirmationBinding,
    confirmation_id: str,
) -> bool:
    expected = {
        "confirmation_id": confirmation_id, "task_id": binding.task_id,
        "tool_call_id": binding.tool_call_id, "tool_name": binding.tool_name,
        "arguments_hash": binding.arguments_hash, "user_id": binding.user_id,
        "org_id": binding.org_id,
    }
    return all(record.get(key) == value for key, value in expected.items())


class ToolConfirmationService:
    def __init__(self, store: ToolConfirmationRedisStore | None = None) -> None:
        self.store = store or ToolConfirmationRedisStore()

    async def create(
        self, *, task_id: str, tool_call_id: str, tool_name: str,
        arguments: Mapping[str, Any], user_id: str,
        org_id: str | None, safety_level: str,
    ) -> ConfirmationRequest:
        if not all((task_id, tool_call_id, tool_name, user_id)):
            raise ValueError("missing confirmation scope")
        binding = ConfirmationBinding(
            task_id, tool_call_id, tool_name,
            canonical_arguments_hash(arguments), user_id, org_id or "",
        )
        request = ConfirmationRequest(
            secrets.token_urlsafe(32), secrets.token_urlsafe(32), binding,
            build_confirmation_summary(tool_name, arguments), safety_level,
        )
        waiter_hash = hash_waiter_token(request.waiter_token)
        try:
            result = await self.store.create(
                request.confirmation_id, binding, waiter_hash,
            )
        except Exception:
            result = await self.store.create(
                request.confirmation_id, binding, waiter_hash,
            )
        if result not in {"CREATED:PENDING", "IDEMPOTENT:PENDING"}:
            raise RuntimeError("confirmation create rejected")
        return request

    async def reject_unavailable(self, request: ConfirmationRequest) -> None:
        """Best-effort terminal close after notification transport failure."""
        try:
            await self.store.consume(
                request.confirmation_id, request.binding,
                request.binding.user_id, request.binding.org_id, False,
            )
        except Exception as exc:
            logger.warning(
                "tool_confirmation_close_failed | "
                "error_code=CONFIRMATION_UNAVAILABLE | exception_type={}",
                type(exc).__name__,
            )

    async def _cancel(
        self, request: ConfirmationRequest,
    ) -> ConfirmationDecision:
        await self.store.consume(
            request.confirmation_id, request.binding,
            request.binding.user_id, request.binding.org_id, False,
        )
        return ConfirmationDecision(
            ConfirmationOutcome.CANCELLED, "TASK_CANCELLED",
        )

    async def _wait_for_terminal(
        self, request: ConfirmationRequest,
        is_cancelled: Callable[[], bool] | None,
    ) -> Mapping[str, str] | ConfirmationDecision:
        deadline = time.monotonic() + 60
        record: Mapping[str, str] = {}
        while time.monotonic() < deadline:
            if is_cancelled and is_cancelled():
                return await self._cancel(request)
            record = await self.store.read(
                request.confirmation_id, request.binding,
            )
            if record.get("state") != "PENDING":
                break
            await self.store.wait_signal(
                request.confirmation_id, request.binding, timeout=1,
            )
        if not record or record.get("state") == "PENDING":
            await self.store.expire(request.confirmation_id, request.binding)
            record = await self.store.read(
                request.confirmation_id, request.binding,
            )
        return record

    async def await_and_claim(
        self, request: ConfirmationRequest,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ConfirmationDecision:
        try:
            record = await self._wait_for_terminal(request, is_cancelled)
            if isinstance(record, ConfirmationDecision):
                return record
            if not _matches(record, request.binding, request.confirmation_id):
                return ConfirmationDecision(
                    ConfirmationOutcome.REJECTED, "BINDING_MISMATCH",
                )
            if record.get("state") != "APPROVED":
                outcome = (
                    ConfirmationOutcome.TIMED_OUT
                    if record.get("state") == "EXPIRED"
                    else ConfirmationOutcome.DENIED
                )
                return ConfirmationDecision(
                    outcome, f"TERMINAL_{record.get('state', 'UNKNOWN')}",
                )
            if is_cancelled and is_cancelled():
                return await self._cancel(request)
            claim = await self.store.claim(
                request.confirmation_id, request.binding,
                hash_waiter_token(request.waiter_token),
            )
            if claim != "WON:EXECUTION_CLAIMED":
                return ConfirmationDecision(
                    ConfirmationOutcome.REJECTED, "CLAIM_REJECTED",
                )
            final = await self.store.read(request.confirmation_id, request.binding)
            if is_cancelled and is_cancelled():
                return ConfirmationDecision(
                    ConfirmationOutcome.CANCELLED, "TASK_CANCELLED",
                )
            if (
                not _matches(final, request.binding, request.confirmation_id)
                or final.get("state") != "EXECUTION_CLAIMED"
            ):
                return ConfirmationDecision(
                    ConfirmationOutcome.REJECTED, "CLAIM_READBACK_FAILED",
                )
            return ConfirmationDecision(
                ConfirmationOutcome.APPROVED, "EXECUTION_CLAIMED",
            )
        except Exception as exc:
            logger.warning(
                "tool_confirmation_failed | "
                "error_code=CONFIRMATION_UNAVAILABLE | exception_type={}",
                type(exc).__name__,
            )
            return ConfirmationDecision(
                ConfirmationOutcome.UNAVAILABLE, "CONFIRMATION_UNAVAILABLE",
            )

    async def consume_response(
        self, *, confirmation_id: str, user_id: str,
        org_id: str | None, approved: bool,
    ) -> str:
        # Resolve the immutable binding from the challenge hash, then enforce actor scope in Lua.
        if not confirmation_id:
            return "MALFORMED_RESPONSE"
        client = await RedisClient.get_client()
        key = f"ws:tool-confirm:{{tool-confirm}}:challenge:{confirmation_id}"
        record = await client.hgetall(key)
        required = {
            "task_id", "tool_call_id", "tool_name", "arguments_hash",
            "user_id", "org_id", "confirmation_id",
        }
        if not isinstance(record, dict) or not required.issubset(record):
            return "NOT_FOUND"
        binding = ConfirmationBinding(
            record["task_id"], record["tool_call_id"], record["tool_name"],
            record["arguments_hash"], record["user_id"], record["org_id"],
        )
        return await self.store.consume(
            confirmation_id, binding, user_id, org_id or "", approved,
        )


tool_confirmation_service = ToolConfirmationService()
