"""Scoped PostgreSQL adapter for Command claim coordination."""

from __future__ import annotations

from typing import Any, Mapping

from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain import FencingToken, RunId, SessionId
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.domain.identity import require_stable_value
from services.agent.runtime.infrastructure.postgres.parsing import (
    outcome,
    require_datetime,
    require_int,
    require_mapping,
    require_text,
    require_uuid,
)
from services.agent.runtime.ports.command_claim import (
    CommandClaim,
    CommandClaimOutcome,
    CommandClaimReceipt,
)


_CLAIM_OUTCOMES = {
    CommandClaimOutcome.CLAIMED,
    CommandClaimOutcome.NOT_FOUND,
    CommandClaimOutcome.ALREADY_CLAIMED,
    CommandClaimOutcome.ATTEMPTS_EXHAUSTED,
    CommandClaimOutcome.SCOPE_REJECTED,
    CommandClaimOutcome.IDEMPOTENCY_CONFLICT,
    CommandClaimOutcome.TERMINAL_CONFLICT,
    CommandClaimOutcome.ALREADY_PROCESSED,
    CommandClaimOutcome.ASSOCIATION_REJECTED,
}


class PostgresCommandClaimRepository:
    """Maps typed receipts to the migration 219 Worker-only RPCs."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.WORKER:
            raise ValueError("WORKER_DATABASE_SCOPE_REQUIRED")
        self._database = database

    async def _rpc(self, name: str, params: dict[str, object]) -> object:
        response = await self._database.rpc(name, params).execute()
        return response.data

    async def claim_next(
        self, worker_id: str, *, lease_seconds: int = 90,
        max_attempts: int = 3,
    ) -> CommandClaimReceipt:
        require_stable_value(worker_id, "worker_id")
        try:
            raw = await self._rpc(
                "claim_pending_agent_command_and_ensure_run", {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
                "p_max_attempts": max_attempts,
            })
        except (OperationalError, InterfaceError):
            recovered = await self._recover_worker_claim(worker_id)
            if recovered.claim is None:
                raise
            return await self.get_claim(recovered.claim.command_id, worker_id)
        return self._claim_receipt(raw, _CLAIM_OUTCOMES)

    async def get_claim(
        self, command_id: str, worker_id: str,
    ) -> CommandClaimReceipt:
        require_stable_value(command_id, "command_id")
        require_stable_value(worker_id, "worker_id")
        raw = await self._rpc("get_agent_command_run_claim", {
            "p_command_id": command_id,
            "p_worker_id": worker_id,
        })
        return self._claim_receipt(raw, {
            CommandClaimOutcome.FOUND,
            CommandClaimOutcome.NOT_FOUND,
        })

    async def renew(
        self, claim: CommandClaim, *, lease_seconds: int = 90,
    ) -> CommandClaimReceipt:
        try:
            raw = await self._rpc("renew_agent_command_claim", {
                "p_command_id": claim.command_id,
                "p_fencing_token": claim.fencing_token,
                "p_lease_seconds": lease_seconds,
            })
        except (OperationalError, InterfaceError):
            recovered = await self.get_claim(claim.command_id, claim.worker_id)
            if (
                recovered.claim is None
                or recovered.claim.fencing_token != claim.fencing_token
            ):
                raise
            return recovered
        return self._claim_receipt(raw, {CommandClaimOutcome.RENEWED})

    async def finish(
        self, claim: CommandClaim, outcome_name: CommandClaimOutcome,
        *, error_class: str | None = None,
    ) -> CommandClaimReceipt:
        if outcome_name not in {
            CommandClaimOutcome.COMPLETED,
            CommandClaimOutcome.FAILED,
        }:
            raise ValueError("COMMAND_CLAIM_TERMINAL_OUTCOME_REQUIRED")
        raw = await self._rpc("finish_agent_command_claim", {
            "p_command_id": claim.command_id,
            "p_fencing_token": claim.fencing_token,
            "p_outcome": outcome_name.value,
            "p_error_class": error_class,
        })
        return self._claim_receipt(raw, {outcome_name})

    async def _recover_worker_claim(
        self, worker_id: str,
    ) -> CommandClaimReceipt:
        raw = await self._rpc("get_agent_command_run_claim", {
            "p_command_id": None,
            "p_worker_id": worker_id,
        })
        return self._claim_receipt(raw, {
            CommandClaimOutcome.FOUND,
            CommandClaimOutcome.NOT_FOUND,
        })

    @staticmethod
    def _claim_receipt(
        raw: object, allowed: set[CommandClaimOutcome],
    ) -> CommandClaimReceipt:
        row = require_mapping(raw, "Command claim")
        name = outcome(row, {item.value for item in allowed})
        typed = CommandClaimOutcome(name)
        if typed in {
            CommandClaimOutcome.NOT_FOUND,
            CommandClaimOutcome.ALREADY_CLAIMED,
            CommandClaimOutcome.ATTEMPTS_EXHAUSTED,
            CommandClaimOutcome.SCOPE_REJECTED,
            CommandClaimOutcome.IDEMPOTENCY_CONFLICT,
            CommandClaimOutcome.TERMINAL_CONFLICT,
            CommandClaimOutcome.ALREADY_PROCESSED,
            CommandClaimOutcome.ASSOCIATION_REJECTED,
            CommandClaimOutcome.RENEWED,
            CommandClaimOutcome.COMPLETED,
            CommandClaimOutcome.FAILED,
        }:
            return CommandClaimReceipt(typed)
        return CommandClaimReceipt(typed, _parse_claim(row))


def _parse_claim(row: Mapping[str, Any]) -> CommandClaim:
    run_id = require_uuid(row, "run_id", optional=True)
    if run_id is None:
        raise PersistenceContractError("run_id: UUID required for active claim")
    return CommandClaim(
        command_id=require_uuid(row, "command_id"),
        session_id=SessionId(require_uuid(row, "session_id")),
        run_id=RunId(run_id),
        worker_id=require_text(row, "worker_id"),
        fencing_token=FencingToken(require_uuid(row, "fencing_token")),
        lease_expires_at=require_datetime(row, "lease_expires_at"),
        attempt_number=require_int(row, "attempt_number", minimum=1),
        command_type=require_text(row, "command_type"),
    )
