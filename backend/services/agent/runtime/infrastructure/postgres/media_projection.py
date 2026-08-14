"""Projection-role adapter for the Runtime media projection RPC lane."""

from __future__ import annotations

from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.event_store import event_from_row
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome, require_datetime, require_int, require_json_object, require_list,
    require_mapping, require_text, require_uuid,
)
from services.agent.runtime.ports.media_projection import MediaProjectionAssetRequest
from services.agent.runtime.ports.projection import ProjectionClaim


class PostgresMediaProjection:
    """Claims and applies media projection without core-table privileges."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.PROJECTION:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def claim(
        self, batch_size: int = 50, lease_seconds: int = 60,
    ) -> tuple[ProjectionClaim, ...]:
        response = await self._database.rpc(
            "claim_agent_runtime_media_projection_v1",
            {"p_batch_size": batch_size, "p_lease_seconds": lease_seconds},
        ).execute()
        rows = require_list(response.data, "media projection claim")
        claims = []
        for row in rows:
            claims.append(await self._read_claim(require_mapping(row, "claim")))
        return tuple(claims)

    async def read(
        self, claim: ProjectionClaim,
    ) -> Mapping[str, object] | None:
        response = await self._database.rpc(
            "read_agent_runtime_media_projection_v1",
            {"p_outbox_id": claim.outbox_id, "p_lease_token": claim.lease_token},
        ).execute()
        result = require_mapping(response.data, "media projection read")
        result_outcome = outcome(result, {"found", "already_applied"})
        return result if result_outcome in {"found", "already_applied"} else None

    async def apply(
        self, claim: ProjectionClaim, action: str,
        content_part: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        response = await self._database.rpc(
            "apply_agent_runtime_media_projection_v1",
            {
                "p_outbox_id": claim.outbox_id,
                "p_lease_token": claim.lease_token,
                "p_action": action,
                "p_content_part": dict(content_part) if content_part else None,
            },
        ).execute()
        result = require_mapping(response.data, "media projection apply")
        outcome(result, {"applied", "already_applied", "ownership_lost", "lease_expired"})
        return result

    async def fail(self, claim: ProjectionClaim, error_code: str) -> None:
        response = await self._database.rpc(
            "fail_agent_runtime_media_projection_v1",
            {
                "p_outbox_id": claim.outbox_id,
                "p_lease_token": claim.lease_token,
                "p_error_code": error_code,
            },
        ).execute()
        result = require_mapping(response.data, "media projection fail")
        outcome(result, {"failed", "ownership_lost", "not_found"})

    async def read_result(self, claim: ProjectionClaim) -> Mapping[str, object] | None:
        response = await self._database.rpc(
            "read_agent_runtime_media_projection_result_v1",
            {"p_outbox_id": claim.outbox_id},
        ).execute()
        result = require_mapping(response.data, "media projection result read")
        return result if outcome(result, {"found", "not_found"}) == "found" else None

    async def _read_claim(
        self, row: Mapping[str, Any],
    ) -> ProjectionClaim:
        outbox_id = require_uuid(row, "id")
        lease_token = require_uuid(row, "lease_token")
        response = await self._database.rpc(
            "read_agent_runtime_media_projection_v1",
            {"p_outbox_id": outbox_id, "p_lease_token": lease_token},
        ).execute()
        result = require_mapping(response.data, "media projection claim read")
        if outcome(result, {"found"}) != "found":
            raise PersistenceContractError("media projection claim ownership lost")
        outbox = require_mapping(result.get("outbox"), "media projection outbox")
        if require_uuid(outbox, "id") != outbox_id:
            raise PersistenceContractError("media projection outbox identity mismatch")
        event = event_from_row(require_mapping(result.get("event"), "media projection event"))
        if require_uuid(outbox, "event_id") != event.event_id:
            raise PersistenceContractError("media projection event identity mismatch")
        return ProjectionClaim(
            outbox_id=outbox_id,
            projection_kind=require_text(outbox, "projection_kind"),
            lease_token=require_uuid(outbox, "lease_token"),
            lease_expires_at=require_datetime(outbox, "lease_expires_at"),
            attempt_count=require_int(outbox, "attempt_count", minimum=1),
            checkpoint=require_json_object(outbox, "checkpoint"),
            event=event,
        )


def asset_request_from_readback(
    claim: ProjectionClaim, readback: Mapping[str, object],
) -> MediaProjectionAssetRequest:
    """Parse only server-returned binding/result facts for persistence."""
    facts = require_mapping(readback.get("action_facts"), "action facts")
    binding = require_mapping(facts.get("binding"), "media binding")
    task = require_mapping(facts.get("task"), "media task")
    media_kind = _media_kind(facts.get("media_kind"))
    urls = facts.get("result_urls")
    if not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], str):
        raise PersistenceContractError("exactly one authoritative media URL required")
    request_params = task.get("request_params")
    if not isinstance(request_params, Mapping):
        request_params = {}
    return MediaProjectionAssetRequest(
        action_id=_required_text(binding.get("action_id"), "action_id"),
        slot_id=_required_text(
            binding.get("slot_id") or binding.get("action_id"), "slot_id",
        ),
        slot_index=(
            _required_int(binding.get("action_index"), "action_index")
            if binding.get("action_index") is not None else 0
        ),
        source_url=urls[0],
        user_id=_required_text(binding.get("user_id"), "user_id"),
        org_id=_optional_text(binding.get("org_id")),
        conversation_id=_required_text(binding.get("conversation_id"), "conversation_id"),
        message_id=_required_text(binding.get("output_message_id"), "output_message_id"),
        task_id=_required_text(binding.get("task_id"), "task_id"),
        model_id=_optional_text(binding.get("pricing_model_id")),
        prompt=str(request_params.get("prompt") or ""),
        aspect_ratio=str(request_params.get("aspect_ratio") or "1:1"),
        resolution=_optional_text(request_params.get("resolution")),
        media_kind=media_kind,
    )


def _media_kind(value: object) -> str:
    if value not in {"image", "video"}:
        raise PersistenceContractError("media kind required")
    return str(value)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersistenceContractError(f"{name} required")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _required_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PersistenceContractError(f"{name} required")
    return value


__all__ = ["PostgresMediaProjection", "asset_request_from_readback"]
