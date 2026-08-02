"""PostgreSQL ports for non-model AR-17.3 facts and narrow RPCs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.executors.specialist_contracts import CostReservation


class PostgresSpecialistRepository:
    """Worker-scoped adapter; no business table is reachable from this class."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def _rpc(self, name: str, params: Mapping[str, object]) -> object:
        return (await self._database.rpc(name, dict(params)).execute()).data

    async def cost(self, operation: str, item: CostReservation, **extra: object) -> object:
        names = {"reserve": "reserve_agent_action_cost", "settle": "settle_agent_action_cost", "release": "release_agent_action_cost", "refund": "refund_agent_action_cost", "adjustment": "adjust_agent_action_cost"}
        if operation not in names:
            raise ValueError("SPECIALIST_COST_OPERATION_INVALID")
        if operation == "reserve":
            params = {"p_action_id": item.action_id, "p_attempt_id": item.attempt_id, "p_reserved_amount": item.reserved_amount, "p_currency": item.currency}
        elif operation == "settle":
            params = {"p_action_id": item.action_id, "p_attempt_id": item.attempt_id, "p_actual_amount": int(extra["actual_amount"]), "p_currency": item.currency, "p_provider_receipt_hash": extra.get("provider_receipt_hash")}
        elif operation in {"release", "refund"}:
            params = {"p_action_id": item.action_id, "p_attempt_id": item.attempt_id, "p_reason_code": extra.get("reason_code", "runtime")}
        else:
            params = {"p_action_id": item.action_id, "p_attempt_id": item.attempt_id, "p_actual_amount": item.reserved_amount, "p_currency": item.currency, "p_provider_receipt_hash": extra["provider_receipt_hash"]}
        return await self._rpc(names[operation], params)

    async def callback(self, *, provider: str, event_id: str, correlation: str, payload_hash: str, payload_redacted: Mapping[str, object], action_id: str, attempt_id: str) -> object:
        return await self._rpc("record_agent_action_callback", {"p_provider": provider, "p_provider_event_id": event_id, "p_callback_correlation": correlation, "p_payload_hash": payload_hash, "p_payload_redacted": dict(payload_redacted), "p_action_id": action_id, "p_attempt_id": attempt_id})

    async def provider_submission(
        self, *, attempt_id: str, execution_token: str, request_hash: str,
        provider: str, provider_task_ref: str, status_locator: str | None,
        callback_correlation: str | None, provider_idempotency_key: str,
        provider_request_hash: str, next_reconcile_at: datetime | None = None,
        external_receipt: Mapping[str, object] | None = None,
    ) -> object:
        return await self._rpc("record_agent_action_provider_submission", {
            "p_attempt_id": attempt_id, "p_execution_token": execution_token,
            "p_request_hash": request_hash, "p_provider": provider,
            "p_provider_task_ref": provider_task_ref,
            "p_status_locator": status_locator,
            "p_callback_correlation": callback_correlation,
            "p_provider_idempotency_key": provider_idempotency_key,
            "p_provider_request_hash": provider_request_hash,
            "p_next_reconcile_at": next_reconcile_at,
            "p_external_receipt": dict(external_receipt or {}),
        })

    async def provider_unknown(
        self, *, attempt_id: str, execution_token: str, request_hash: str,
        ambiguity_evidence: Mapping[str, object],
    ) -> object:
        return await self._rpc("record_agent_action_unknown", {
            "p_attempt_id": attempt_id, "p_execution_token": execution_token,
            "p_request_hash": request_hash,
            "p_ambiguity_evidence": dict(ambiguity_evidence),
        })

    async def provider_terminal(
        self, *, attempt_id: str, execution_token: str, request_hash: str,
        state: str, result: Mapping[str, object] | None = None,
        ambiguity_evidence: Mapping[str, object] | None = None,
    ) -> object:
        if state not in {"completed", "failed", "cancelled", "unknown"}:
            raise ValueError("SPECIALIST_TERMINAL_STATE_INVALID")
        return await self._rpc("record_agent_action_provider_terminal", {
            "p_attempt_id": attempt_id, "p_execution_token": execution_token,
            "p_request_hash": request_hash, "p_state": state,
            "p_result": dict(result or {}),
            "p_ambiguity_evidence": dict(ambiguity_evidence or {}),
        })

    async def provider_reconcile(
        self, *, attempt_id: str, reconciliation_token: str,
        request_hash: str, resolution: str, result: Mapping[str, object] | None = None,
        ambiguity_evidence: Mapping[str, object] | None = None,
    ) -> object:
        return await self._rpc("resolve_agent_action_provider_reconciliation", {
            "p_attempt_id": attempt_id,
            "p_reconciliation_token": reconciliation_token,
            "p_request_hash": request_hash, "p_resolution": resolution,
            "p_result": dict(result) if result is not None else None,
            "p_ambiguity_evidence": dict(ambiguity_evidence or {}),
        })

    async def link_artifact(self, **params: object) -> object:
        return await self._rpc("link_agent_action_artifact", params)

    async def checkpoint_materialization(self, **params: object) -> object:
        return await self._rpc("checkpoint_agent_artifact_materialization", params)

    async def create_child_run(self, **params: object) -> object:
        return await self._rpc("create_agent_child_run", params)

    async def read_child_run(self, *, child_run_id: str, parent_run_id: str) -> object:
        return await self._rpc("read_agent_child_run", {
            "p_child_run_id": child_run_id, "p_parent_run_id": parent_run_id,
        })

    async def complete_child_run(self, **params: object) -> object:
        return await self._rpc("complete_agent_child_run", params)

    async def cancel_child_run(self, **params: object) -> object:
        return await self._rpc("cancel_agent_child_run", params)

    async def mutate_resource(self, operation: str, **params: object) -> object:
        names = {"file_delete": "runtime_delete_workspace_resource", "restore_file": "runtime_restore_workspace_resource", "manage_scheduled_task": "runtime_mutate_scheduled_task"}
        if operation not in names:
            raise ValueError("SPECIALIST_RESOURCE_OPERATION_INVALID")
        if "p_execution_token" not in params:
            raise ValueError("SPECIALIST_FENCING_TOKEN_REQUIRED")
        return await self._rpc(names[operation], params)


__all__ = ["PostgresSpecialistRepository"]
