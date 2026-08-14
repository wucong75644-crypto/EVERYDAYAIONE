"""PostgreSQL adapter for Runtime-owned scheduled finalization RPCs."""

from __future__ import annotations

from typing import Any, Mapping

from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome,
    require_datetime,
    require_int,
    require_mapping,
    require_text,
    require_uuid,
)
from services.agent.runtime.ports.scheduled_finalization import (
    ScheduledFinalizationClaim,
    ScheduledFinalizationContext,
    ScheduledFinalizationOutcome,
    ScheduledFinalizationProjection,
    ScheduledFinalizationReceipt,
    ScheduledTerminalStatus,
)


class PostgresScheduledFinalizationRepository:
    """Uses only the Worker-scoped 227_31/227_33 RPC surface."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def _rpc(self, name: str, params: dict[str, object]) -> object:
        response = await self._database.rpc(name, params).execute()
        return response.data

    async def claim_next(
        self, worker_id: str, *, lease_seconds: int = 90,
    ) -> ScheduledFinalizationClaim | None:
        row = require_mapping(await self._rpc(
            "claim_next_agent_runtime_scheduled_finalization_v1", {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        ), "Scheduled finalization claim")
        name = outcome(row, {"claimed", "not_found", "claim_conflict"})
        if name in {"not_found", "claim_conflict"}:
            return None
        intent = require_mapping(row.get("intent"), "Scheduled finalization intent")
        return ScheduledFinalizationClaim(
            scheduled_run_id=require_uuid(intent, "scheduled_run_id"),
            claim_token=require_uuid(intent, "claim_token"),
            intent_state_version=require_int(intent, "state_version"),
            claim_lease_expires_at=require_datetime(
                intent, "claim_lease_expires_at",
            ),
        )

    async def read_context(
        self, claim: ScheduledFinalizationClaim,
    ) -> ScheduledFinalizationContext:
        row = require_mapping(await self._rpc(
            "read_agent_runtime_scheduled_finalization_context_v1", {
                "p_scheduled_run_id": claim.scheduled_run_id,
                "p_claim_token": claim.claim_token,
            },
        ), "Scheduled finalization context")
        name = outcome(row, {"found", "not_found", "fenced", "applied"})
        if name != "found":
            raise PersistenceContractError(
                f"scheduled finalization context unavailable: {name}",
            )
        context = require_mapping(row.get("context"), "Scheduled context")
        typed = ScheduledFinalizationContext(
            scheduled_run_id=require_uuid(context, "scheduled_run_id"),
            terminal_status=_terminal_status(context),
            terminal_baseline=require_datetime(context, "terminal_baseline"),
            intent_state_version=require_int(context, "intent_state_version"),
            task_state_version=require_int(context, "task_state_version"),
            schedule_hash=_hash(context, "schedule_hash"),
            schedule_type=require_text(context, "schedule_type"),
            cron_expr=require_text(context, "cron_expr", optional=True),
            timezone=require_text(context, "timezone"),
            retry_count=require_int(context, "retry_count"),
            consecutive_failures=require_int(context, "consecutive_failures"),
        )
        if (
            typed.scheduled_run_id != claim.scheduled_run_id
            or typed.intent_state_version != claim.intent_state_version
        ):
            raise PersistenceContractError("scheduled finalization claim changed")
        if typed.schedule_type != "once" and typed.cron_expr is None:
            raise PersistenceContractError("periodic schedule requires cron_expr")
        return typed

    async def apply(
        self, claim: ScheduledFinalizationClaim,
        context: ScheduledFinalizationContext,
        projection: ScheduledFinalizationProjection,
    ) -> ScheduledFinalizationReceipt:
        params = {
            "p_scheduled_run_id": claim.scheduled_run_id,
            "p_claim_token": claim.claim_token,
            "p_expected_intent_version": context.intent_state_version,
            "p_expected_task_version": context.task_state_version,
            "p_schedule_hash": context.schedule_hash,
            "p_request_id": projection.request_id,
            "p_reason": projection.reason,
            "p_next_run_at": projection.next_run_at,
        }
        try:
            raw = await self._rpc(
                "apply_agent_runtime_scheduled_finalization_v2", params,
            )
        except (OperationalError, InterfaceError):
            await self._read_after_lost_response(claim)
            raw = await self._rpc(
                "apply_agent_runtime_scheduled_finalization_v2", params,
            )
        receipt = _receipt(raw)
        if (
            receipt.scheduled_run_id != claim.scheduled_run_id
            or receipt.terminal_status is not context.terminal_status
        ):
            raise PersistenceContractError(
                "scheduled finalization apply receipt changed identity",
            )
        return receipt

    async def _read_after_lost_response(
        self, claim: ScheduledFinalizationClaim,
    ) -> None:
        row = require_mapping(await self._rpc(
            "read_agent_runtime_scheduled_finalization_v1", {
                "p_scheduled_run_id": claim.scheduled_run_id,
                "p_claim_token": claim.claim_token,
            },
        ), "Scheduled finalization recovery readback")
        outcome(row, {"found", "not_found", "fenced"})
        intent = row.get("intent")
        if not isinstance(intent, Mapping) or intent.get("status") != "applied":
            raise OperationalError("SCHEDULED_FINALIZATION_APPLY_UNCONFIRMED")


def _terminal_status(row: Mapping[str, Any]) -> ScheduledTerminalStatus:
    value = require_text(row, "terminal_status")
    try:
        return ScheduledTerminalStatus(value)
    except ValueError as exc:
        raise PersistenceContractError("unknown scheduled terminal status") from exc


def _hash(row: Mapping[str, Any], field: str) -> str:
    value = require_text(row, field)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PersistenceContractError(f"{field}: sha256 required")
    return value


def _receipt(raw: object) -> ScheduledFinalizationReceipt:
    row = require_mapping(raw, "Scheduled finalization apply")
    name = outcome(row, {"applied", "already_applied"})
    return ScheduledFinalizationReceipt(
        outcome=ScheduledFinalizationOutcome(name),
        scheduled_run_id=require_uuid(row, "scheduled_run_id"),
        scheduled_task_id=require_uuid(row, "scheduled_task_id"),
        terminal_status=_terminal_status(row),
        scheduled_run_status=require_text(row, "scheduled_run_status"),
        task_status=require_text(row, "task_status"),
        task_state_version=require_int(row, "task_state_version", minimum=1),
    )
