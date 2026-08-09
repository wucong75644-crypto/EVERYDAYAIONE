"""PostgreSQL ports for non-model AR-17.3 facts and narrow RPCs."""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.executors.specialist_contracts import CostReservation
from services.agent.runtime.executors.contracts import canonical_json
from services.agent.runtime.scheduler_cas import (
    PostgresSchedulerControlStore,
    scheduler_control_result,
)


class SpecialistRpcError(RuntimeError):
    """A durable RPC did not return a recognized, safe outcome."""

    def __init__(self, rpc: str, outcome: object, payload: object) -> None:
        super().__init__(f"{rpc}: invalid outcome {outcome!r}")
        self.rpc = rpc
        self.outcome = outcome
        self.payload = payload


class SpecialistRpcConflict(SpecialistRpcError):
    """The durable store rejected an idempotency or terminal conflict."""


class PostgresSpecialistRepository:
    """Worker-scoped adapter; no business table is reachable from this class."""

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database
        self._scheduler_control = PostgresSchedulerControlStore(database)

    async def _rpc(self, name: str, params: Mapping[str, object], *, allowed: set[str]) -> Mapping[str, object]:
        payload = (await self._database.rpc(name, dict(params)).execute()).data
        if not isinstance(payload, Mapping):
            raise SpecialistRpcError(name, None, payload)
        outcome = payload.get("outcome")
        if outcome in {"fenced", "dispatch_contract_missing", "terminal_conflict", "ownership_lost", "stale_version", "request_hash_conflict", "receipt_conflict", "idempotency_conflict"}:
            raise SpecialistRpcConflict(name, outcome, payload)
        if outcome == "duplicate":
            raise SpecialistRpcConflict(name, outcome, payload)
        if not isinstance(outcome, str) or outcome not in allowed:
            raise SpecialistRpcError(name, outcome, payload)
        return dict(payload)

    async def cost(self, operation: str, item: CostReservation, **extra: object) -> object:
        names = {"reserve": "reserve", "settle": "settle", "release": "release", "refund": "refund", "adjustment": "adjustment"}
        if operation not in names:
            raise ValueError("SPECIALIST_COST_OPERATION_INVALID")
        actual = int(extra.get("actual_amount", 0)) if operation != "reserve" else 0
        reason = str(extra.get("reason_code", "runtime"))
        receipt_hash = extra.get("provider_receipt_hash")
        return await self._rpc("record_agent_action_cost_strict", {
            "p_action_id": item.action_id, "p_attempt_id": item.attempt_id,
            "p_kind": names[operation], "p_reserved_amount": item.reserved_amount,
            "p_actual_amount": actual, "p_currency": item.currency,
            "p_reason_code": reason, "p_provider_receipt_hash": receipt_hash,
        }, allowed={"applied", "idempotent_readback"})

    async def callback(self, *, provider: str, event_id: str, correlation: str, payload_hash: str, payload_redacted: Mapping[str, object], action_id: str, attempt_id: str) -> object:
        return await self._rpc("record_agent_action_callback_strict", {"p_provider": provider, "p_provider_event_id": event_id, "p_callback_correlation": correlation, "p_payload_hash": payload_hash, "p_payload_redacted": dict(payload_redacted), "p_action_id": action_id, "p_attempt_id": attempt_id}, allowed={"accepted", "idempotent_readback"})

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
        }, allowed={"accepted"})

    async def provider_unknown(
        self, *, attempt_id: str, execution_token: str, request_hash: str,
        ambiguity_evidence: Mapping[str, object],
    ) -> object:
        return await self._rpc("record_agent_action_unknown_v2", {
            "p_attempt_id": attempt_id, "p_execution_token": execution_token,
            "p_request_hash": request_hash,
            "p_ambiguity_evidence": dict(ambiguity_evidence),
        }, allowed={"unknown"})

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
        }, allowed={"completed", "failed", "cancelled", "unknown"})

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
        }, allowed={resolution})

    async def still_accepted(self, **params: object) -> object:
        return await self._rpc("record_agent_action_provider_still_accepted", params, allowed={"still_accepted"})

    async def still_unknown(self, **params: object) -> object:
        return await self._rpc("record_agent_action_provider_still_unknown", params, allowed={"still_unknown"})

    async def link_artifact(self, **params: object) -> object:
        return await self._rpc("link_agent_action_artifact", params, allowed={"linked"})

    async def checkpoint_materialization(self, **params: object) -> object:
        return await self._rpc("checkpoint_agent_artifact_materialization", params, allowed={"checkpointed"})

    async def create_child_run(self, **params: object) -> object:
        return await self._rpc(
            "create_agent_child_run_strict_v2", params,
            allowed={"created", "already_exists", "cancel_fenced"},
        )

    async def read_child_run(self, *, child_run_id: str | None, parent_run_id: str, parent_action_id: str,
                             parent_attempt_id: str, parent_request_hash: str,
                             ownership_token: str, expected_state_version: int,
                             child_ordinal: int | None = None) -> object:
        del child_ordinal
        return await self._rpc("read_agent_child_run_binding_v3", {
            "p_child_run_id": child_run_id, "p_parent_run_id": parent_run_id,
            "p_parent_action_id": parent_action_id, "p_parent_attempt_id": parent_attempt_id,
            "p_parent_request_hash": parent_request_hash, "p_ownership_token": ownership_token,
            "p_expected_state_version": expected_state_version,
        }, allowed={"readback", "not_found"})

    async def complete_child_run(self, **params: object) -> object:
        return await self._rpc(
            "aggregate_agent_child_run_strict_v2", params,
            allowed={"completed", "cancel_pending"},
        )

    async def cancel_child_run(self, **params: object) -> object:
        return await self._rpc(
            "read_agent_child_run_cancel_intent_v1", params,
            allowed={"confirmed", "pending", "not_found"},
        )

    async def mutate_resource(self, operation: str, **params: object) -> object:
        if operation == "manage_scheduled_task":
            attempt = params.pop("_attempt", None)
            if attempt is None:
                raise ValueError("SCHEDULER_ATTEMPT_REQUIRED")
            result = await self.mutate_scheduler_task(
                attempt=attempt,
                task_id=str(params["p_task_id"]),
                expected_version=int(params["p_expected_state_version"]),
                operation=str(params["p_operation"]),
                payload=dict(params.get("p_payload", {})),
                dispatch_intent_id=str(params["p_dispatch_intent_id"]),
                attempt_state_version=int(params["p_attempt_state_version"]),
            )
            return result
        names = {"file_delete": "runtime_delete_workspace_resource", "restore_file": "runtime_restore_workspace_resource"}
        if operation not in names:
            raise ValueError("SPECIALIST_RESOURCE_OPERATION_INVALID")
        if "p_execution_token" not in params:
            raise ValueError("SPECIALIST_FENCING_TOKEN_REQUIRED")
        return await self._rpc(names[operation], params, allowed={"bound", "updated"})

    async def mutate_scheduler_task(
        self, *, attempt: object, task_id: str, expected_version: int,
        operation: str, payload: Mapping[str, object], dispatch_intent_id: str,
        attempt_state_version: int,
    ) -> Mapping[str, object]:
        response = await self._scheduler_control.mutate(
            attempt=attempt, task_id=task_id, expected_version=expected_version,
            operation=operation, payload=payload,
            dispatch_intent_id=dispatch_intent_id,
            attempt_state_version=attempt_state_version,
        )
        return scheduler_control_result(response)

    async def readback_scheduler_task(
        self, *, attempt: object, ownership_token: str,
        expected_state_version: int,
    ) -> Mapping[str, object]:
        return scheduler_control_result(await self._scheduler_control.readback(
            attempt=attempt, idempotency_key=str(attempt.idempotency_key),
            ownership_token=ownership_token,
            expected_state_version=expected_state_version,
        ))

    async def cancel_scheduler_task(
        self, *, attempt: object, reason: str, ownership_token: str,
        expected_state_version: int,
    ) -> Mapping[str, object]:
        return scheduler_control_result(await self._scheduler_control.cancel(
            attempt=attempt, idempotency_key=str(attempt.idempotency_key), reason=reason,
            ownership_token=ownership_token,
            expected_state_version=expected_state_version,
        ))

    async def reconcile_scheduler_task(
        self, *, attempt: object, ownership_token: str,
        expected_state_version: int,
    ) -> Mapping[str, object]:
        return scheduler_control_result(await self._scheduler_control.reconcile(
            attempt=attempt, idempotency_key=str(attempt.idempotency_key),
            ownership_token=ownership_token,
            expected_state_version=expected_state_version,
        ))

    async def finalize(
        self, *, attempt_id: str, execution_token: str | None,
        reconciliation_token: str | None, expected_state_version: int,
        request_hash: str, terminal_state: str,
        provider_receipt: Mapping[str, object], result: Mapping[str, object],
        cost_kind: str | None, reserved_amount: int = 0, actual_amount: int = 0,
        currency: str = "credits", reason_code: str = "runtime",
        provider_receipt_hash: str | None = None,
    ) -> Mapping[str, object]:
        return await self._rpc("finalize_agent_action_provider_v2", {
            "p_attempt_id": attempt_id, "p_execution_token": execution_token,
            "p_reconciliation_token": reconciliation_token,
            "p_expected_state_version": expected_state_version,
            "p_request_hash": request_hash, "p_terminal_state": terminal_state,
            "p_provider_receipt": dict(provider_receipt), "p_result": dict(result),
            "p_cost_kind": cost_kind, "p_reserved_amount": reserved_amount,
            "p_actual_amount": actual_amount, "p_currency": currency,
            "p_reason_code": reason_code, "p_provider_receipt_hash": provider_receipt_hash or hashlib.sha256(canonical_json(provider_receipt).encode("utf-8")).hexdigest(),
        }, allowed={terminal_state})

    async def sync_phase(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("record_agent_sync_phase_v3", params, allowed={"recorded"})

    async def read_sync_facts(self, **params: object) -> Mapping[str, object]:
        result = await self._rpc("read_agent_sync_phase_facts", params, allowed={"readback"})
        facts = result.get("facts", {})
        if not isinstance(facts, Mapping):
            raise SpecialistRpcError("read_agent_sync_phase_facts", "facts_invalid", result)
        return dict(facts)

    async def create_or_get_sync_submission(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("create_or_get_agent_sync_submission", params, allowed={"created", "readback"})

    async def record_sync_submission_result(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("record_agent_sync_submission_result", params, allowed={"recorded"})

    async def recover_sync_submission(self, **params: object) -> Mapping[str, object]:
        return await self._rpc("recover_agent_sync_submission", params, allowed={"found", "proven_not_submitted", "unknown"})


__all__ = ["PostgresSpecialistRepository", "SpecialistRpcConflict", "SpecialistRpcError"]
