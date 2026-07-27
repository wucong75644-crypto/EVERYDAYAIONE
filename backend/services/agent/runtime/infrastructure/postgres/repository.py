"""Scoped PostgreSQL implementation of the landed Runtime repository RPCs."""

from __future__ import annotations

from typing import Any, Mapping

from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain import (
    FencingToken,
    Lease,
    ModelStepId,
    RunAttempt,
    RunId,
    RuntimeScope,
    ScopeKind,
    SessionCommand,
    SessionId,
)
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.domain.identity import require_stable_value
from services.agent.runtime.infrastructure.postgres.parsing import (
    mutation_receipt,
    outcome,
    require_datetime,
    require_enum,
    require_int,
    require_json_object,
    require_mapping,
    require_text,
    require_uuid,
)
from services.agent.runtime.ports.repository import (
    ClaimOutcome,
    MutationOutcome,
    MutationReceipt,
    RunClaim,
    SessionSnapshot,
)


_CREATE = {MutationOutcome.CREATED, MutationOutcome.ALREADY_EXISTS}
_COMPLETE_RUN = {
    MutationOutcome.COMPLETED,
    MutationOutcome.ALREADY_COMPLETED,
    MutationOutcome.NOT_READY,
}
_FAIL = {
    MutationOutcome.FAILED,
    MutationOutcome.ALREADY_FAILED,
}
_CANCEL = {
    MutationOutcome.CANCELLED,
    MutationOutcome.ALREADY_CANCELLED,
}


class PostgresRuntimeRepository:
    """Maps typed Runtime operations to scoped migrations 213～216 RPCs."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None:
            raise ValueError("SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database
        self._access_kind = scope.access_kind

    async def _rpc(
        self, name: str, params: dict[str, object],
    ) -> object:
        response = await self._database.rpc(name, params).execute()
        return response.data

    async def submit_command(
        self, command: SessionCommand,
    ) -> MutationReceipt:
        self._require_access(DatabaseAccessKind.RUNTIME)
        return mutation_receipt(
            await self._rpc("submit_session_command", {
                "p_session_id": command.session_id,
                "p_command_type": command.command_type.value,
                "p_idempotency_key": command.idempotency_key,
                "p_payload": dict(command.payload),
            }),
            _CREATE,
        )

    async def ensure_session(
        self, conversation_id: str, scope: RuntimeScope,
        created_by_user_id: str, agent_definition_id: str,
        agent_definition_revision: str,
    ) -> MutationReceipt:
        self._require_access(DatabaseAccessKind.RUNTIME)
        return mutation_receipt(
            await self._rpc("ensure_agent_runtime_session", {
                "p_conversation_id": conversation_id,
                "p_org_id": scope.org_id,
                "p_user_id": scope.user_id,
                "p_scope_kind": scope.kind.value,
                "p_scope_id": scope.scope_id,
                "p_created_by_user_id": created_by_user_id,
                "p_agent_definition_id": agent_definition_id,
                "p_agent_definition_revision": agent_definition_revision,
            }),
            _CREATE,
        )

    async def get_session(
        self, session_id: SessionId,
    ) -> SessionSnapshot | None:
        result = require_mapping(
            await self._rpc("get_agent_runtime_session", {
                "p_session_id": session_id,
            }),
            "get session",
        )
        name = outcome(result, {"found", "not_found"})
        if name == "not_found":
            return None
        row = require_mapping(result.get("session"), "session")
        return SessionSnapshot(
            session_id=SessionId(require_uuid(row, "id")),
            conversation_id=require_uuid(row, "conversation_id"),
            scope=_scope(row),
            created_by_user_id=require_uuid(
                row, "created_by_user_id", optional=True,
            ),
            agent_definition_id=require_text(row, "agent_definition_id"),
            agent_definition_revision=require_text(
                row, "agent_definition_revision",
            ),
            next_event_sequence=require_int(
                row, "next_event_sequence", minimum=1,
            ),
            state_version=require_int(row, "state_version"),
        )

    async def create_run(
        self, session_id: SessionId, command_id: str,
        idempotency_key: str, run_kind: str,
        context_receipt: Mapping[str, object],
        config_snapshot: Mapping[str, object],
        capability_snapshot: Mapping[str, object],
    ) -> MutationReceipt:
        self._require_access(DatabaseAccessKind.WORKER)
        return mutation_receipt(
            await self._rpc("create_agent_run", {
                "p_session_id": session_id,
                "p_command_id": command_id,
                "p_idempotency_key": idempotency_key,
                "p_run_kind": run_kind,
                "p_context_receipt": dict(context_receipt),
                "p_config_snapshot": dict(config_snapshot),
                "p_capability_snapshot": dict(capability_snapshot),
            }),
            _CREATE,
        )

    async def claim_run(
        self, run_id: RunId, worker_id: str,
    ) -> RunClaim:
        self._require_access(DatabaseAccessKind.WORKER)
        require_stable_value(worker_id, "worker_id")
        try:
            raw = await self._rpc("claim_agent_run", {
                "p_run_id": run_id,
                "p_worker_id": worker_id,
                "p_lease_seconds": 90,
                "p_max_attempts": 3,
            })
        except (OperationalError, InterfaceError):
            recovered = await self._read_claim(run_id, worker_id)
            if recovered is None:
                raise
            return recovered
        row = require_mapping(raw, "claim run")
        name = outcome(row, {item.value for item in ClaimOutcome})
        if name != ClaimOutcome.CLAIMED:
            return RunClaim(
                outcome=ClaimOutcome(name),
                state_version=require_int(
                    row, "state_version", optional=True,
                ),
            )
        recovered = await self._read_claim(run_id, worker_id)
        if recovered is None:
            raise PersistenceContractError("claimed RunAttempt is missing")
        if recovered.state_version != require_int(row, "state_version"):
            raise PersistenceContractError("claim state version mismatch")
        return RunClaim(
            ClaimOutcome.CLAIMED,
            recovered.attempt,
            state_version=recovered.state_version,
            event_sequence=recovered.event_sequence,
        )

    async def renew_run(
        self, run_id: RunId, token: FencingToken,
        lease_seconds: int = 90,
    ) -> MutationReceipt:
        return mutation_receipt(
            await self._rpc("renew_agent_run", {
                "p_run_id": run_id,
                "p_execution_token": token,
                "p_lease_seconds": lease_seconds,
            }),
            {MutationOutcome.RENEWED},
        )

    async def set_run_waiting(
        self, run_id: RunId, token: FencingToken,
        state_version: int, waiting_status: str,
    ) -> MutationReceipt:
        return await self._run_mutation("set_agent_run_waiting", {
            "p_run_id": run_id,
            "p_execution_token": token,
            "p_expected_state_version": state_version,
            "p_waiting_status": waiting_status,
        }, {MutationOutcome.TRANSITIONED})

    async def wake_run(
        self, run_id: RunId, state_version: int,
    ) -> MutationReceipt:
        return await self._run_mutation("wake_agent_run", {
            "p_run_id": run_id,
            "p_expected_state_version": state_version,
        }, {MutationOutcome.TRANSITIONED, MutationOutcome.NOT_READY})

    async def complete_run(
        self, run_id: RunId, token: FencingToken,
        state_version: int, result_hash: str,
    ) -> MutationReceipt:
        return await self._run_mutation("complete_agent_run", {
            "p_run_id": run_id,
            "p_execution_token": token,
            "p_expected_state_version": state_version,
            "p_result_hash": result_hash,
        }, _COMPLETE_RUN)

    async def fail_run(
        self, run_id: RunId, token: FencingToken,
        state_version: int, error_code: str,
    ) -> MutationReceipt:
        return await self._run_mutation("fail_agent_run", {
            "p_run_id": run_id,
            "p_execution_token": token,
            "p_expected_state_version": state_version,
            "p_error_code": error_code,
        }, _FAIL)

    async def cancel_run(
        self, run_id: RunId, state_version: int, reason: str,
    ) -> MutationReceipt:
        if self._access_kind not in {
            DatabaseAccessKind.RUNTIME,
            DatabaseAccessKind.WORKER,
        }:
            raise ValueError("RUNTIME_OR_WORKER_DATABASE_SCOPE_REQUIRED")
        return mutation_receipt(
            await self._rpc("cancel_agent_run", {
                "p_run_id": run_id,
                "p_expected_state_version": state_version,
                "p_reason": reason,
            }),
            _CANCEL,
        )

    async def create_model_step(
        self, run_id: RunId, token: FencingToken, *,
        model_id: str, provider: str, model_revision: str,
        prompt_revision: str, tool_catalog_revision: str,
        request_receipt: Mapping[str, object],
    ) -> MutationReceipt:
        return await self._run_mutation("create_model_step", {
            "p_run_id": run_id, "p_execution_token": token,
            "p_model_id": model_id, "p_provider": provider,
            "p_model_revision": model_revision,
            "p_prompt_revision": prompt_revision,
            "p_tool_catalog_revision": tool_catalog_revision,
            "p_request_receipt": dict(request_receipt),
        }, {MutationOutcome.CREATED})

    async def complete_model_step(
        self, model_step_id: ModelStepId, token: FencingToken,
        state_version: int, *, response_receipt: Mapping[str, object],
        stop_reason: str, provider_stop_reason: str | None,
        input_tokens: int, output_tokens: int, reasoning_tokens: int,
    ) -> MutationReceipt:
        return await self._run_mutation("complete_model_step", {
            "p_step_id": model_step_id, "p_execution_token": token,
            "p_expected_state_version": state_version,
            "p_response_receipt": dict(response_receipt),
            "p_stop_reason": stop_reason,
            "p_provider_stop_reason": provider_stop_reason,
            "p_input_tokens": input_tokens, "p_output_tokens": output_tokens,
            "p_reasoning_tokens": reasoning_tokens,
        }, {
            MutationOutcome.COMPLETED,
            MutationOutcome.ALREADY_COMPLETED,
        })

    async def fail_model_step(
        self, model_step_id: ModelStepId, token: FencingToken,
        state_version: int, error_code: str,
    ) -> MutationReceipt:
        return await self._run_mutation("fail_model_step", {
            "p_step_id": model_step_id, "p_execution_token": token,
            "p_expected_state_version": state_version,
            "p_error_code": error_code,
        }, _FAIL)

    async def _run_mutation(
        self, name: str, params: dict[str, object],
        allowed: set[MutationOutcome],
    ) -> MutationReceipt:
        self._require_access(DatabaseAccessKind.WORKER)
        return mutation_receipt(await self._rpc(name, params), allowed)

    async def _read_claim(
        self, run_id: RunId, worker_id: str,
    ) -> RunClaim | None:
        result = require_mapping(
            await self._rpc("get_agent_runtime_run_claim", {
                "p_run_id": run_id, "p_worker_id": worker_id,
            }),
            "read RunAttempt",
        )
        if outcome(result, {"found", "not_found"}) == "not_found":
            return None
        row = require_mapping(result.get("attempt"), "RunAttempt")
        return RunClaim(
            outcome=ClaimOutcome.CLAIMED,
            attempt=RunAttempt(
                run_id=RunId(require_uuid(row, "run_id")),
                scope=_scope(row),
                attempt_number=require_int(row, "attempt_number", minimum=1),
                worker_id=require_text(row, "worker_id"),
                lease=Lease(
                    FencingToken(require_uuid(row, "execution_token")),
                    require_datetime(row, "lease_expires_at"),
                ),
                claimed_at=require_datetime(row, "claimed_at"),
            ),
            state_version=require_int(result, "state_version"),
            event_sequence=require_int(
                result, "event_sequence", minimum=1,
            ),
        )

    def _require_access(self, expected: DatabaseAccessKind) -> None:
        if self._access_kind is not expected:
            raise ValueError(f"{expected.value.upper()}_DATABASE_SCOPE_REQUIRED")


def _scope(row: Mapping[str, Any]) -> RuntimeScope:
    return RuntimeScope(
        kind=require_enum(row, "scope_kind", ScopeKind),
        scope_id=require_text(row, "scope_id"),
        user_id=require_uuid(row, "user_id", optional=True),
        org_id=require_uuid(row, "org_id", optional=True),
    )
