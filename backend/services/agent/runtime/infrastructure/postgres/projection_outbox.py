"""Scoped PostgreSQL adapter for Projection Outbox delivery."""

from __future__ import annotations

from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.event_store import (
    event_from_row,
)
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome,
    require_datetime,
    require_int,
    require_json_object,
    require_list,
    require_mapping,
    require_text,
    require_uuid,
)
from services.agent.runtime.ports.projection import ProjectionClaim


class PostgresProjectionOutbox:
    """Claims Outbox rows and resolves each full Event envelope by lease."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.WORKER:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def claim(
        self, batch_size: int = 50, lease_seconds: int = 60,
    ) -> tuple[ProjectionClaim, ...]:
        response = await self._database.rpc(
            "claim_agent_projection_outbox",
            {
                "p_batch_size": batch_size,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()
        rows = require_list(response.data, "projection claim")
        claims = []
        for value in rows:
            outbox = require_mapping(value, "projection outbox")
            claims.append(await self._resolve_claim(outbox))
        return tuple(claims)

    async def complete(
        self, claim: ProjectionClaim,
        checkpoint: Mapping[str, object],
    ) -> None:
        response = await self._database.rpc(
            "complete_agent_projection_outbox",
            {
                "p_outbox_id": claim.outbox_id,
                "p_lease_token": claim.lease_token,
                "p_checkpoint": dict(checkpoint),
            },
        ).execute()
        result = require_mapping(response.data, "complete projection")
        outcome(result, {"completed", "already_completed"})

    async def fail(
        self, claim: ProjectionClaim, error_code: str,
    ) -> None:
        response = await self._database.rpc(
            "fail_agent_projection_outbox",
            {
                "p_outbox_id": claim.outbox_id,
                "p_lease_token": claim.lease_token,
                "p_error_code": error_code,
            },
        ).execute()
        result = require_mapping(response.data, "fail projection")
        outcome(result, {"failed"})

    async def _resolve_claim(
        self, outbox: Mapping[str, Any],
    ) -> ProjectionClaim:
        outbox_id = require_uuid(outbox, "id")
        lease_token = require_uuid(outbox, "lease_token")
        response = await self._database.rpc(
            "get_claimed_agent_projection_event",
            {
                "p_outbox_id": outbox_id,
                "p_lease_token": lease_token,
            },
        ).execute()
        result = require_mapping(response.data, "projection event")
        outcome(result, {"found"})
        returned = require_mapping(result.get("outbox"), "projection outbox")
        if require_uuid(returned, "id") != outbox_id:
            raise PersistenceContractError("projection outbox identity mismatch")
        event = event_from_row(
            require_mapping(result.get("event"), "projection event"),
        )
        if require_uuid(returned, "event_id") != event.event_id:
            raise PersistenceContractError("projection event identity mismatch")
        return ProjectionClaim(
            outbox_id=outbox_id,
            projection_kind=require_text(
                returned, "projection_kind",
            ),
            lease_token=require_uuid(returned, "lease_token"),
            lease_expires_at=require_datetime(
                returned, "lease_expires_at",
            ),
            attempt_count=require_int(
                returned, "attempt_count", minimum=1,
            ),
            checkpoint=require_json_object(returned, "checkpoint"),
            event=event,
        )
