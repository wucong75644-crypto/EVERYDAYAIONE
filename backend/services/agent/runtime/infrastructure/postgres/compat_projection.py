"""PostgreSQL adapter for atomic Runtime compatibility projection."""

from __future__ import annotations

from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome,
    require_list,
    require_mapping,
)
from services.agent.runtime.infrastructure.postgres.projection_outbox import (
    PostgresProjectionOutbox,
)
from services.agent.runtime.ports.projection import ProjectionClaim


class PostgresCompatibilityProjection:
    """Claims and atomically applies compatibility projection outbox rows."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.PROJECTION:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database
        self._resolver = PostgresProjectionOutbox(database)

    async def claim(
        self, batch_size: int = 50, lease_seconds: int = 60,
    ) -> tuple[ProjectionClaim, ...]:
        response = await self._database.rpc(
            "claim_agent_compat_projection_outbox",
            {
                "p_batch_size": batch_size,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()
        rows = require_list(response.data, "compat projection claim")
        return tuple(
            [
                await self._resolver._resolve_claim(
                    require_mapping(row, "projection outbox"),
                )
                for row in rows
            ],
        )

    async def apply(
        self, claim: ProjectionClaim, action: str,
    ) -> Mapping[str, object]:
        response = await self._database.rpc(
            "apply_agent_compat_projection",
            {
                "p_outbox_id": claim.outbox_id,
                "p_lease_token": claim.lease_token,
                "p_action": action,
            },
        ).execute()
        result = require_mapping(response.data, "apply compat projection")
        outcome(result, {"applied", "already_applied"})
        return result

    async def readback(
        self, claim: ProjectionClaim,
    ) -> Mapping[str, object] | None:
        response = await self._database.rpc(
            "get_agent_compat_projection_result",
            {"p_outbox_id": claim.outbox_id},
        ).execute()
        result = require_mapping(response.data, "compat projection readback")
        result_outcome = outcome(result, {"found", "not_found"})
        return result if result_outcome == "found" else None

    async def fail(
        self, claim: ProjectionClaim, error_code: str,
    ) -> None:
        await self._resolver.fail(claim, error_code)
