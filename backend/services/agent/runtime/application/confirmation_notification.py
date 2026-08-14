"""Deliver durable Authorization Interactions as Tool Confirmation V3 challenges."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from config.tool_safety import get_safety_level
from schemas.websocket_builders import build_tool_confirm_request
from services.tool_confirmation import ToolConfirmationService
from services.tool_confirmation.canonical import canonical_arguments_hash


class ToolConfirmationNotificationWorker:
    def __init__(
        self, *, database: Any, service: ToolConfirmationService,
        websocket_manager: Any, worker_id: str,
    ) -> None:
        self._database = database
        self._service = service
        self._websocket = websocket_manager
        self._worker_id = worker_id

    async def run_once(self) -> bool:
        response = await self._database.rpc(
            "claim_agent_tool_batch_confirmation_v1", {
                "p_worker_id": self._worker_id, "p_lease_seconds": 60,
            },
        ).execute()
        claim = _mapping(response.data)
        if claim.get("outcome") == "not_found":
            return False
        if claim.get("outcome") != "claimed":
            raise RuntimeError(
                f"TOOL_CONFIRMATION_NOTIFICATION_{claim.get('outcome')}",
            )
        delivered = False
        request = None
        try:
            arguments = _mapping(claim.get("arguments"))
            if canonical_arguments_hash(arguments) != _text(
                claim, "arguments_hash",
            ):
                raise RuntimeError("TOOL_CONFIRMATION_ARGUMENTS_HASH_MISMATCH")
            expires_at = _datetime(claim.get("authorization_expires_at"))
            request = await self._service.create(
                action_id=_text(claim, "action_id"),
                interaction_id=_text(claim, "interaction_id"),
                interaction_version=_integer(claim, "interaction_version"),
                authorization_expires_at=expires_at,
                task_id=_text(claim, "task_id"),
                tool_call_id=_text(claim, "tool_call_id"),
                tool_name=_text(claim, "tool_name"),
                arguments=arguments,
                user_id=_text(claim, "user_id"),
                org_id=_optional_text(claim.get("org_id")),
                safety_level=get_safety_level(
                    _text(claim, "tool_name"),
                ).value,
                confirmation_group_hash=_optional_text(
                    claim.get("confirmation_group_hash"),
                ) or "",
                confirmation_group_size=_group_size(claim),
            )
            delivered = await self._websocket.send_tool_confirmation(
                request.binding.task_id, request.binding.user_id,
                build_tool_confirm_request(
                    task_id=request.binding.task_id,
                    conversation_id=_text(claim, "conversation_id"),
                    message_id="",
                    confirmation_id=request.confirmation_id,
                    tool_name=request.binding.tool_name,
                    confirmation_summary=dict(request.summary),
                    safety_level=request.safety_level,
                    timeout=60,
                ),
                org_id=request.binding.org_id or None,
            )
            if not delivered:
                await self._service.reject_unavailable(request)
        finally:
            completion = await self._database.rpc(
                "complete_agent_tool_confirmation_notification", {
                    "p_interaction_id": _text(claim, "interaction_id"),
                    "p_notification_token": _text(
                        claim, "notification_token",
                    ),
                    "p_delivered": delivered,
                },
            ).execute()
            outcome = _mapping(completion.data).get("outcome")
            if outcome not in {"completed", "released"}:
                raise RuntimeError(
                    f"TOOL_CONFIRMATION_NOTIFICATION_{outcome}",
                )
        return True


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError("TOOL_CONFIRMATION_NOTIFICATION_OBJECT_REQUIRED")
    return value


def _text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} required")
    return item


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: Mapping[str, Any], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return item


def _group_size(value: Mapping[str, Any]) -> int:
    group_hash = _optional_text(value.get("confirmation_group_hash"))
    if group_hash is None:
        return 1
    size = _integer(value, "confirmation_group_size")
    if size not in range(2, 11):
        raise ValueError("confirmation_group_size must be between 2 and 10")
    return size


def _datetime(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value
