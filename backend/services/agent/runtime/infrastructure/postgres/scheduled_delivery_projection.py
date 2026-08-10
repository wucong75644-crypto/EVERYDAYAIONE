"""Scoped PostgreSQL adapter for durable Scheduled Runtime Web projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome,
    require_int,
    require_mapping,
    require_text,
    require_uuid,
)


@dataclass(frozen=True)
class ScheduledWebProjectionClaim:
    intent_id: str
    scheduled_run_id: str
    runtime_run_id: str
    task_id: str
    org_id: str
    user_id: str
    target_hash: str
    content_identity_hash: str
    terminal_status: str
    scheduled_run_status: str
    task_status: str
    summary: str | None
    reason_code: str | None
    next_run_at: str | None
    consecutive_failures: int
    claim_request_id: str
    claim_token: str
    state_version: int
    projected: bool


class PostgresScheduledDeliveryProjection:
    """Uses only projection-scoped RPCs; no delivery table direct access."""

    production_ready = False

    def __init__(self, database: Any, worker_id: str) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.PROJECTION:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        if not worker_id.strip():
            raise ValueError("WORKER_ID_REQUIRED")
        self._database = database
        self._worker_id = worker_id[:128]

    async def claim(self, lease_seconds: int = 60) -> ScheduledWebProjectionClaim | None:
        request_id = str(uuid4())
        try:
            response = await self._database.rpc(
                "claim_agent_runtime_scheduled_web_projection_v1",
                {
                    "p_worker_id": self._worker_id,
                    "p_request_id": request_id,
                    "p_lease_seconds": lease_seconds,
                },
            ).execute()
        except (OperationalError, InterfaceError) as error:
            if not _transport_outcome_uncertain(error):
                raise
            response = await self._database.rpc(
                "read_agent_runtime_scheduled_web_projection_claim_v1",
                {"p_request_id": request_id},
            ).execute()
            recovered = require_mapping(
                response.data, "scheduled web projection claim readback",
            )
            if recovered.get("outcome") == "not_found":
                raise error
            row = recovered
        else:
            row = require_mapping(
                response.data, "scheduled web projection claim",
            )
        result = outcome(row, {"claimed", "not_found", "unavailable", "fenced"})
        if result in {"not_found", "unavailable", "fenced"}:
            return None
        claim = _claim(row)
        if claim.claim_request_id != request_id:
            raise PersistenceContractError("claim request identity mismatch")
        return claim

    async def apply(
        self, claim: ScheduledWebProjectionClaim,
    ) -> ScheduledWebProjectionClaim:
        if claim.projected:
            return claim
        try:
            response = await self._database.rpc(
                "apply_agent_runtime_scheduled_web_projection_v1",
                {
                    "p_intent_id": claim.intent_id,
                    "p_claim_token": claim.claim_token,
                    "p_expected_state_version": claim.state_version,
                },
            ).execute()
            row = require_mapping(response.data, "scheduled web projection apply")
            outcome(row, {"projected", "already_projected"})
        except (OperationalError, InterfaceError) as error:
            if not _transport_outcome_uncertain(error):
                raise
            row = await self.readback(claim.intent_id)
            if row is None or not _same_projected_claim(claim, row):
                raise error
        return _claim(row, projected=True)

    async def readback(self, intent_id: str) -> Mapping[str, Any] | None:
        response = await self._database.rpc(
            "get_agent_runtime_scheduled_web_projection_v1",
            {"p_intent_id": intent_id},
        ).execute()
        row = require_mapping(response.data, "scheduled web projection readback")
        result = outcome(
            row,
            {"not_found", "pending", "claimed", "projected", "completed", "unavailable"},
        )
        return None if result == "not_found" else row

    async def complete_wakeup(
        self, claim: ScheduledWebProjectionClaim, *, delivered: bool,
        error_code: str | None,
    ) -> Mapping[str, Any]:
        try:
            response = await self._database.rpc(
                "complete_agent_runtime_scheduled_web_wakeup_v1",
                {
                    "p_intent_id": claim.intent_id,
                    "p_claim_token": claim.claim_token,
                    "p_expected_state_version": claim.state_version,
                    "p_delivered": delivered,
                    "p_error_code": error_code,
                },
            ).execute()
            row = require_mapping(response.data, "scheduled web wakeup completion")
        except (OperationalError, InterfaceError) as error:
            if not _transport_outcome_uncertain(error):
                raise
            row = await self.readback(claim.intent_id)
            if row is None or not _same_completed_wakeup(
                claim, row, delivered=delivered, error_code=error_code,
            ):
                raise error
            return row
        outcome(row, {"completed", "already_completed"})
        return row


def _claim(
    row: Mapping[str, Any], *, projected: bool | None = None,
) -> ScheduledWebProjectionClaim:
    terminal = require_text(row, "terminal_status")
    run_status = require_text(row, "scheduled_run_status")
    task_status = require_text(row, "task_status")
    if terminal not in {"completed", "failed", "cancelled"}:
        raise PersistenceContractError("unknown scheduled terminal status")
    if run_status not in {"success", "failed", "skipped"}:
        raise PersistenceContractError("unknown scheduled run status")
    if task_status not in {"active", "paused", "error", "running"}:
        raise PersistenceContractError("unknown scheduled task status")
    summary = _optional_text(row, "summary", 500)
    reason = _optional_text(row, "reason_code", 80)
    target_hash = _hash(row, "target_hash")
    content_hash = _hash(row, "content_identity_hash")
    return ScheduledWebProjectionClaim(
        intent_id=require_uuid(row, "intent_id"),
        scheduled_run_id=require_uuid(row, "scheduled_run_id"),
        runtime_run_id=require_uuid(row, "runtime_run_id"),
        task_id=require_uuid(row, "task_id"),
        org_id=require_uuid(row, "org_id"),
        user_id=require_uuid(row, "user_id"),
        target_hash=target_hash,
        content_identity_hash=content_hash,
        terminal_status=terminal,
        scheduled_run_status=run_status,
        task_status=task_status,
        summary=summary,
        reason_code=reason,
        next_run_at=_optional_text(row, "next_run_at", 80),
        consecutive_failures=require_int(row, "consecutive_failures"),
        claim_request_id=require_uuid(row, "claim_request_id"),
        claim_token=require_uuid(row, "claim_token"),
        state_version=require_int(row, "state_version"),
        projected=(row.get("projected_at") is not None if projected is None else projected),
    )


def _hash(row: Mapping[str, Any], field: str) -> str:
    value = require_text(row, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PersistenceContractError(f"{field}: sha256 required")
    return value


def _optional_text(row: Mapping[str, Any], field: str, limit: int) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise PersistenceContractError(f"{field}: bounded text required")
    return value


def _transport_outcome_uncertain(error: BaseException) -> bool:
    if isinstance(error, InterfaceError):
        return True
    sqlstate = getattr(error, "sqlstate", None)
    return isinstance(error, OperationalError) and (
        sqlstate is None or str(sqlstate).startswith("08")
    )


def _same_projected_claim(
    claim: ScheduledWebProjectionClaim, row: Mapping[str, Any],
) -> bool:
    try:
        return (
            row.get("outcome") == "projected"
            and require_uuid(row, "intent_id") == claim.intent_id
            and require_uuid(row, "claim_request_id") == claim.claim_request_id
            and require_uuid(row, "claim_token") == claim.claim_token
            and require_int(row, "state_version") == claim.state_version
            and _hash(row, "projection_receipt_hash") is not None
            and row.get("projected_at") is not None
        )
    except PersistenceContractError:
        return False


def _same_completed_wakeup(
    claim: ScheduledWebProjectionClaim, row: Mapping[str, Any], *,
    delivered: bool, error_code: str | None,
) -> bool:
    try:
        expected_result = "sent" if delivered else "failed"
        return (
            row.get("outcome") == "projected"
            and row.get("projection_state") == "completed"
            and require_uuid(row, "intent_id") == claim.intent_id
            and require_uuid(row, "claim_request_id") == claim.claim_request_id
            and row.get("claim_token") is None
            and require_int(row, "state_version") == claim.state_version + 1
            and row.get("wakeup_result") == expected_result
            and row.get("wakeup_error_code") == error_code
            and row.get("wakeup_attempted_at") is not None
        )
    except PersistenceContractError:
        return False
