"""WORKER-scoped PostgreSQL adapter for migration 218 Action RPCs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.infrastructure.postgres.action_parsing import (
    parse_action_receipt,
)
from services.agent.runtime.ports.action_repository import ActionMutationReceipt


class PostgresActionRepository:
    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.WORKER:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def _rpc(
        self, name: str, params: dict[str, object],
    ) -> ActionMutationReceipt:
        response = await self._database.rpc(name, params).execute()
        return parse_action_receipt(response.data)

    async def complete_tool_calls(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, response_receipt: Mapping[str, object],
        response_hash: str, provider_stop_reason: str | None,
        usage: Mapping[str, object], actual_credits: int,
        batch_hash: str, actions: Sequence[Mapping[str, object]],
    ) -> ActionMutationReceipt:
        return await self._rpc(
            "complete_model_attempt_step_and_create_actions", {
                "p_attempt_id": attempt_id,
                "p_run_execution_token": run_execution_token,
                "p_expected_attempt_version": expected_attempt_version,
                "p_expected_step_version": expected_step_version,
                "p_request_hash": request_hash,
                "p_response_receipt": dict(response_receipt),
                "p_response_hash": response_hash,
                "p_provider_stop_reason": provider_stop_reason,
                "p_usage": dict(usage),
                "p_actual_credits": actual_credits,
                "p_batch_hash": batch_hash,
                "p_actions": [dict(action) for action in actions],
            },
        )

    async def claim_ready(
        self, *, worker_id: str, claim_request_id: str,
        batch_size: int = 10,
        lease_seconds: int = 120,
    ) -> ActionMutationReceipt:
        return await self._rpc("claim_ready_agent_actions", {
            "p_worker_id": worker_id,
            "p_claim_request_id": claim_request_id,
            "p_batch_size": batch_size,
            "p_lease_seconds": lease_seconds,
        })

    async def get_claim_batch(
        self, *, worker_id: str, claim_request_id: str,
    ) -> ActionMutationReceipt:
        return await self._rpc("get_agent_action_claim_batch", {
            "p_worker_id": worker_id,
            "p_claim_request_id": claim_request_id,
        })

    async def get_action(self, *, action_id: str) -> ActionMutationReceipt:
        return await self._rpc("get_agent_action", {"p_action_id": action_id})

    async def renew(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, lease_seconds: int = 120,
    ) -> ActionMutationReceipt:
        return await self._rpc("renew_agent_action_attempt", {
            "p_attempt_id": attempt_id,
            "p_execution_token": execution_token,
            "p_expected_state_version": expected_state_version,
            "p_lease_seconds": lease_seconds,
        })

    async def mark_dispatching(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, request_hash: str,
    ) -> ActionMutationReceipt:
        return await self._rpc("mark_agent_action_dispatching", {
            "p_attempt_id": attempt_id,
            "p_execution_token": execution_token,
            "p_expected_state_version": expected_state_version,
            "p_request_hash": request_hash,
        })

    async def recover_expired(
        self, *, attempt_id: str, expected_state_version: int,
        worker_id: str, lease_seconds: int = 120,
    ) -> ActionMutationReceipt:
        return await self._rpc("recover_expired_agent_action_attempt", {
            "p_attempt_id": attempt_id,
            "p_expected_state_version": expected_state_version,
            "p_worker_id": worker_id,
            "p_lease_seconds": lease_seconds,
        })

    async def mark_accepted(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, request_hash: str,
        external_receipt: Mapping[str, object],
    ) -> ActionMutationReceipt:
        return await self._rpc("mark_agent_action_accepted", {
            "p_attempt_id": attempt_id,
            "p_execution_token": execution_token,
            "p_expected_state_version": expected_state_version,
            "p_request_hash": request_hash,
            "p_external_receipt": dict(external_receipt),
        })

    async def record_unknown(
        self, *, attempt_id: str, execution_token: str,
        expected_state_version: int, request_hash: str,
        ambiguity_evidence: Mapping[str, object],
    ) -> ActionMutationReceipt:
        return await self._rpc("record_agent_action_unknown", {
            "p_attempt_id": attempt_id,
            "p_execution_token": execution_token,
            "p_expected_state_version": expected_state_version,
            "p_request_hash": request_hash,
            "p_ambiguity_evidence": dict(ambiguity_evidence),
        })

    async def fail_before_dispatch(
        self, *, attempt_id: str, execution_token: str,
        expected_attempt_version: int, request_hash: str, error_code: str,
    ) -> ActionMutationReceipt:
        return await self._rpc("fail_claimed_agent_action", {
            "p_attempt_id": attempt_id,
            "p_execution_token": execution_token,
            "p_expected_attempt_version": expected_attempt_version,
            "p_request_hash": request_hash,
            "p_error_code": error_code,
        })

    async def complete(
        self, *, attempt_id: str, execution_token: str,
        expected_attempt_version: int, request_hash: str,
        result: Mapping[str, object],
    ) -> ActionMutationReceipt:
        return await self._terminal(
            "complete_agent_action", attempt_id, execution_token,
            expected_attempt_version, request_hash, result,
        )

    async def fail(
        self, *, attempt_id: str, execution_token: str,
        expected_attempt_version: int, request_hash: str,
        result: Mapping[str, object],
    ) -> ActionMutationReceipt:
        return await self._terminal(
            "fail_agent_action", attempt_id, execution_token,
            expected_attempt_version, request_hash, result,
        )

    async def _terminal(
        self, name: str, attempt_id: str, execution_token: str,
        expected_attempt_version: int, request_hash: str,
        result: Mapping[str, object],
    ) -> ActionMutationReceipt:
        return await self._rpc(name, {
            "p_attempt_id": attempt_id,
            "p_execution_token": execution_token,
            "p_expected_attempt_version": expected_attempt_version,
            "p_request_hash": request_hash,
            "p_result": dict(result),
        })

    async def claim_reconciliation(
        self, *, attempt_id: str, expected_state_version: int,
        worker_id: str, lease_seconds: int = 120,
    ) -> ActionMutationReceipt:
        return await self._rpc("claim_agent_action_reconciliation", {
            "p_attempt_id": attempt_id,
            "p_expected_state_version": expected_state_version,
            "p_worker_id": worker_id,
            "p_lease_seconds": lease_seconds,
        })

    async def renew_reconciliation(
        self, *, attempt_id: str, reconciliation_token: str,
        expected_state_version: int, lease_seconds: int = 120,
    ) -> ActionMutationReceipt:
        return await self._rpc("renew_agent_action_reconciliation", {
            "p_attempt_id": attempt_id,
            "p_reconciliation_token": reconciliation_token,
            "p_expected_state_version": expected_state_version,
            "p_lease_seconds": lease_seconds,
        })

    async def resolve_reconciliation(
        self, *, attempt_id: str, reconciliation_token: str,
        expected_state_version: int, request_hash: str, resolution: str,
        result: Mapping[str, object] | None = None,
        ambiguity_evidence: Mapping[str, object] | None = None,
    ) -> ActionMutationReceipt:
        return await self._rpc("resolve_agent_action_reconciliation", {
            "p_attempt_id": attempt_id,
            "p_reconciliation_token": reconciliation_token,
            "p_expected_state_version": expected_state_version,
            "p_request_hash": request_hash,
            "p_resolution": resolution,
            "p_result": dict(result) if result is not None else None,
            "p_ambiguity_evidence": dict(ambiguity_evidence or {}),
        })
