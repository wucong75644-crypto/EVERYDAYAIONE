"""Application service that owns creation, waiting, verification and claim."""

from __future__ import annotations

import secrets
import time
import inspect
from datetime import datetime, timezone
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
        "confirmation_id": confirmation_id,
        "action_id": binding.action_id,
        "interaction_id": binding.interaction_id,
        "interaction_version": str(binding.interaction_version),
        "task_id": binding.task_id,
        "tool_call_id": binding.tool_call_id, "tool_name": binding.tool_name,
        "arguments_hash": binding.arguments_hash, "user_id": binding.user_id,
        "org_id": binding.org_id,
        "authorization_expires_at": binding.expires_at.isoformat(),
    }
    if binding.confirmation_group_hash:
        expected.update({
            "confirmation_group_hash": binding.confirmation_group_hash,
            "confirmation_group_size": str(binding.confirmation_group_size),
        })
    return all(record.get(key) == value for key, value in expected.items())


class ToolConfirmationService:
    def __init__(self, store: ToolConfirmationRedisStore | None = None) -> None:
        self.store = store or ToolConfirmationRedisStore()
        self._available = False

    def set_available(self, available: bool) -> None:
        self._available = available

    async def create(
        self, *, action_id: str, interaction_id: str,
        interaction_version: int, authorization_expires_at: datetime,
        task_id: str, tool_call_id: str, tool_name: str,
        arguments: Mapping[str, Any], user_id: str,
        org_id: str | None, safety_level: str,
        confirmation_group_hash: str = "", confirmation_group_size: int = 1,
    ) -> ConfirmationRequest:
        if not self._available:
            raise RuntimeError("TOOL_CONFIRMATION_V3_DISABLED")
        if not all((
            action_id, interaction_id, task_id, tool_call_id, tool_name, user_id,
        )):
            raise ValueError("missing confirmation scope")
        if (
            interaction_version < 0
            or authorization_expires_at.utcoffset() is None
            or authorization_expires_at <= datetime.now(timezone.utc)
        ):
            raise ValueError("invalid authorization binding")
        if (
            (confirmation_group_hash and (
                len(confirmation_group_hash) != 64
                or any(char not in "0123456789abcdef"
                       for char in confirmation_group_hash)
                or confirmation_group_size not in range(2, 11)
            ))
            or (not confirmation_group_hash and confirmation_group_size != 1)
        ):
            raise ValueError("invalid confirmation group binding")
        binding = ConfirmationBinding(
            action_id, interaction_id, interaction_version,
            task_id, tool_call_id, tool_name,
            canonical_arguments_hash(arguments), user_id, org_id or "",
            authorization_expires_at, confirmation_group_hash,
            confirmation_group_size,
        )
        summary = dict(build_confirmation_summary(tool_name, arguments))
        if confirmation_group_hash:
            summary["batch_size"] = confirmation_group_size
        request = ConfirmationRequest(
            secrets.token_urlsafe(32), secrets.token_urlsafe(32), binding,
            summary, safety_level,
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
        if result == "CREATE_CONFLICT":
            existing = await self.store.find(binding)
            if existing is None or not _matches(
                existing[1], binding, existing[0],
            ):
                raise RuntimeError("confirmation identity conflict")
            if existing[1].get("state") != "PENDING":
                raise RuntimeError("confirmation is no longer pending")
            return ConfirmationRequest(
                existing[0], "", binding,
                summary,
                safety_level,
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
                ConfirmationOutcome.REJECTED,
                "POSTGRES_AUTHORIZATION_REQUIRED",
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
        org_id: str | None, approved: bool, database: Any,
    ) -> str:
        # Resolve the immutable binding from the challenge hash, then enforce actor scope in Lua.
        if not self._available:
            return "TOOL_CONFIRMATION_V3_DISABLED"
        if not confirmation_id:
            return "MALFORMED_RESPONSE"
        client = await RedisClient.get_client()
        key = f"ws:tool-confirm:{{tool-confirm}}:challenge:{confirmation_id}"
        record = await client.hgetall(key)
        required = {
            "action_id", "interaction_id", "interaction_version", "task_id",
            "tool_call_id", "tool_name", "arguments_hash", "user_id",
            "org_id", "confirmation_id", "authorization_expires_at",
        }
        if not isinstance(record, dict) or not required.issubset(record):
            return "NOT_FOUND"
        binding = ConfirmationBinding(
            record["action_id"], record["interaction_id"],
            int(record["interaction_version"]), record["task_id"],
            record["tool_call_id"], record["tool_name"],
            record["arguments_hash"], record["user_id"], record["org_id"],
            datetime.fromisoformat(record["authorization_expires_at"]),
            record.get("confirmation_group_hash", ""),
            int(record.get("confirmation_group_size", "1")),
        )
        redis_result = await self.store.consume(
            confirmation_id, binding, user_id, org_id or "", approved,
        )
        if not (
            redis_result.startswith("WON:")
            or redis_result in {
                "ALREADY_TERMINAL:APPROVED", "ALREADY_TERMINAL:DENIED",
            }
        ):
            return redis_result
        rpc_name = (
            "resolve_agent_tool_batch_confirmation_v1"
            if binding.confirmation_group_hash
            else "resolve_agent_tool_confirmation_v3"
        )
        params = {
                "p_confirmation_id": confirmation_id,
                "p_interaction_id": binding.interaction_id,
                "p_action_id": binding.action_id,
                "p_expected_interaction_version": binding.interaction_version,
                "p_user_id": binding.user_id,
                "p_org_id": binding.org_id or None,
                "p_arguments_hash": binding.arguments_hash,
                "p_expires_at": binding.expires_at,
                "p_approved": approved,
        }
        if binding.confirmation_group_hash:
            params["p_confirmation_group_hash"] = (
                binding.confirmation_group_hash
            )
        persisted = database.rpc(rpc_name, params).execute()
        if inspect.isawaitable(persisted):
            persisted = await persisted
        outcome = getattr(persisted, "data", None)
        if not isinstance(outcome, dict) or outcome.get("outcome") not in {
            "resolved", "already_resolved",
        }:
            code = str((outcome or {}).get("outcome", "unavailable")).upper()
            return f"AUTHORIZATION_{code}"
        return "WON:PERSISTED"


tool_confirmation_service = ToolConfirmationService()
