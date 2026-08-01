"""Runtime-scoped PostgreSQL adapter for audited dead Projection recovery."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome,
    require_datetime,
    require_int,
    require_list,
    require_mapping,
    require_uuid,
)
from services.agent.runtime.ports.projection_recovery import (
    ProjectionDeadRecoveryReceipt,
)


class PostgresProjectionDeadRecovery:
    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if (
            scope is None
            or scope.access_kind is not DatabaseAccessKind.RUNTIME
            or scope.actor_user_id is None
        ):
            raise ValueError("RUNTIME_SCOPED_ACTOR_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def list_dead(
        self, *, limit: int = 50,
    ) -> tuple[Mapping[str, object], ...]:
        response = await self._database.rpc(
            "list_agent_projection_dead_items", {"p_limit": limit},
        ).execute()
        result = require_mapping(response.data, "dead projection list")
        outcome(result, {"found"})
        return tuple(
            require_mapping(item, "dead projection item")
            for item in require_list(result.get("items"), "dead items")
        )

    async def get_dead(
        self, *, outbox_id: str,
    ) -> Mapping[str, object] | None:
        response = await self._database.rpc(
            "get_agent_projection_dead_item",
            {"p_outbox_id": outbox_id},
        ).execute()
        result = require_mapping(response.data, "dead projection item")
        result_outcome = outcome(result, {"found", "not_found"})
        return result if result_outcome == "found" else None

    async def requeue(
        self, *, outbox_id: str,
        expected_recovery_version: int,
        expected_attempt_count: int,
        recovery_request_id: str,
        reason: str,
        not_before: datetime,
    ) -> ProjectionDeadRecoveryReceipt:
        response = await self._database.rpc(
            "requeue_agent_projection_dead", {
                "p_outbox_id": outbox_id,
                "p_expected_status": "dead",
                "p_expected_recovery_version": expected_recovery_version,
                "p_expected_attempt_count": expected_attempt_count,
                "p_recovery_request_id": recovery_request_id,
                "p_reason": reason,
                "p_not_before": not_before.isoformat(),
            },
        ).execute()
        result = require_mapping(response.data, "dead projection requeue")
        result_outcome = outcome(
            result, {"requeued", "already_requeued"},
        )
        receipt = ProjectionDeadRecoveryReceipt(
            outcome=result_outcome,
            outbox_id=require_uuid(result, "outbox_id"),
            audit_id=require_uuid(result, "audit_id"),
            recovery_version=require_int(
                result, "recovery_version", minimum=1,
            ),
            recovery_count=require_int(
                result, "recovery_count", minimum=1,
            ),
            attempt_count=require_int(
                result, "attempt_count", minimum=8,
            ),
            next_attempt_at=require_datetime(
                result, "next_attempt_at",
            ),
        )
        if receipt.outbox_id != outbox_id:
            raise PersistenceContractError(
                "projection recovery outbox identity mismatch",
            )
        return receipt
