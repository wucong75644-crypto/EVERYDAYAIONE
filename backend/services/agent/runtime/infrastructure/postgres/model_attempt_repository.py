"""Scoped PostgreSQL adapter for migration 217 ModelAttempt RPCs."""

from __future__ import annotations

from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.infrastructure.postgres.model_attempt_parsing import (
    parse_attempt_receipt,
    parse_attempt_snapshot,
)
from services.agent.runtime.ports.model import ModelResponseStartObserver
from services.agent.runtime.ports.model_attempt import (
    ModelAttemptOutcome,
    ModelAttemptReceipt,
    ModelAttemptSnapshot,
)


class PostgresModelAttemptRepository:
    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def _rpc(self, name: str, params: dict[str, object]) -> object:
        response = await self._database.rpc(name, params).execute()
        return response.data

    async def prepare(
        self, *, model_step_id: str, run_execution_token: str,
        expected_step_version: int, worker_id: str, request_hash: str,
        idempotency_key: str, provider: str,
        request_receipt: Mapping[str, object], reserved_credits: int,
        lease_seconds: int = 120,
    ) -> ModelAttemptReceipt:
        return parse_attempt_receipt(
            await self._rpc("prepare_model_attempt", {
                "p_step_id": model_step_id,
                "p_run_execution_token": run_execution_token,
                "p_expected_step_version": expected_step_version,
                "p_worker_id": worker_id,
                "p_request_hash": request_hash,
                "p_idempotency_key": idempotency_key,
                "p_provider": provider,
                "p_request_receipt": dict(request_receipt),
                "p_reserved_credits": reserved_credits,
                "p_lease_seconds": lease_seconds,
            }),
            {
                ModelAttemptOutcome.PREPARED,
                ModelAttemptOutcome.ALREADY_PREPARED,
                ModelAttemptOutcome.UNRESOLVED_ATTEMPT,
                ModelAttemptOutcome.INSUFFICIENT_CREDITS,
                ModelAttemptOutcome.BUDGET_EXHAUSTED,
                ModelAttemptOutcome.NOT_FOUND,
            },
        )

    async def start_dispatch(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, request_hash: str,
    ) -> ModelAttemptReceipt:
        return await self._mutation(
            "start_model_attempt_dispatch_v2", {
                "p_attempt_id": attempt_id,
                "p_run_execution_token": run_execution_token,
                "p_expected_attempt_version": expected_attempt_version,
                "p_request_hash": request_hash,
            }, {
                ModelAttemptOutcome.FENCED,
                ModelAttemptOutcome.DISPATCHING,
                ModelAttemptOutcome.ALREADY_DISPATCHING,
                ModelAttemptOutcome.NOT_FOUND,
            },
        )

    async def mark_response_started(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, request_hash: str,
        provider_request_id: str | None,
    ) -> ModelAttemptReceipt:
        return await self._mutation(
            "mark_model_attempt_response_started", {
                "p_attempt_id": attempt_id,
                "p_run_execution_token": run_execution_token,
                "p_expected_attempt_version": expected_attempt_version,
                "p_request_hash": request_hash,
                "p_provider_request_id": provider_request_id,
            }, {
                ModelAttemptOutcome.RESPONSE_STARTED,
                ModelAttemptOutcome.ALREADY_STARTED,
                ModelAttemptOutcome.RECEIPT_CONFLICT,
                ModelAttemptOutcome.NOT_FOUND,
            },
        )

    async def record_unknown(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, request_hash: str,
        dispatch_phase: str, retry_disposition: str,
        ambiguity_evidence: Mapping[str, object],
    ) -> ModelAttemptReceipt:
        return await self._mutation("record_model_attempt_unknown", {
            "p_attempt_id": attempt_id,
            "p_run_execution_token": run_execution_token,
            "p_expected_attempt_version": expected_attempt_version,
            "p_request_hash": request_hash,
            "p_dispatch_phase": dispatch_phase,
            "p_retry_disposition": retry_disposition,
            "p_ambiguity_evidence": dict(ambiguity_evidence),
        }, {
            ModelAttemptOutcome.UNKNOWN,
            ModelAttemptOutcome.ALREADY_UNKNOWN,
            ModelAttemptOutcome.NOT_FOUND,
        })

    async def complete_without_actions(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, response_receipt: Mapping[str, object],
        response_hash: str, stop_reason: str,
        provider_stop_reason: str | None, usage: Mapping[str, object],
        actual_credits: int,
    ) -> ModelAttemptReceipt:
        return await self._mutation(
            "complete_model_attempt_without_actions", {
                "p_attempt_id": attempt_id,
                "p_run_execution_token": run_execution_token,
                "p_expected_attempt_version": expected_attempt_version,
                "p_expected_step_version": expected_step_version,
                "p_request_hash": request_hash,
                "p_response_receipt": dict(response_receipt),
                "p_response_hash": response_hash,
                "p_stop_reason": stop_reason,
                "p_provider_stop_reason": provider_stop_reason,
                "p_usage": dict(usage),
                "p_actual_credits": actual_credits,
            }, {
                ModelAttemptOutcome.COMPLETED,
                ModelAttemptOutcome.ALREADY_COMPLETED,
                ModelAttemptOutcome.HANDOFF_TOOL_CALLS,
                ModelAttemptOutcome.RUN_CANCELLED_USE_LATE_RECEIPT,
                ModelAttemptOutcome.NOT_FOUND,
            },
        )

    async def fail(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, error_code: str,
        retry_disposition: str = "forbidden",
    ) -> ModelAttemptReceipt:
        return await self._mutation("fail_model_attempt_and_step", {
            "p_attempt_id": attempt_id,
            "p_run_execution_token": run_execution_token,
            "p_expected_attempt_version": expected_attempt_version,
            "p_expected_step_version": expected_step_version,
            "p_request_hash": request_hash,
            "p_error_code": error_code,
            "p_retry_disposition": retry_disposition,
        }, {
            ModelAttemptOutcome.FAILED,
            ModelAttemptOutcome.ALREADY_FAILED,
            ModelAttemptOutcome.RUN_CANCELLED_USE_LATE_RECEIPT,
            ModelAttemptOutcome.NOT_FOUND,
        })

    async def get_attempt(
        self, attempt_id: str,
    ) -> ModelAttemptSnapshot | None:
        return parse_attempt_snapshot(
            await self._rpc("get_model_attempt", {
                "p_attempt_id": attempt_id,
            })
        )

    async def claim_reconciliation(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, worker_id: str,
        lease_seconds: int = 120,
    ) -> ModelAttemptReceipt:
        return await self._mutation(
            "claim_model_attempt_reconciliation", {
                "p_attempt_id": attempt_id,
                "p_run_execution_token": run_execution_token,
                "p_expected_attempt_version": expected_attempt_version,
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            }, {
                ModelAttemptOutcome.CLAIMED,
                ModelAttemptOutcome.BUSY,
                ModelAttemptOutcome.NOT_FOUND,
                ModelAttemptOutcome.NOT_RECONCILABLE,
            },
        )

    async def renew_reconciliation(
        self, *, attempt_id: str, run_execution_token: str,
        reconciliation_token: str, lease_seconds: int = 120,
    ) -> ModelAttemptReceipt:
        return await self._mutation(
            "renew_model_attempt_reconciliation", {
                "p_attempt_id": attempt_id,
                "p_run_execution_token": run_execution_token,
                "p_reconciliation_token": reconciliation_token,
                "p_lease_seconds": lease_seconds,
            }, {
                ModelAttemptOutcome.RENEWED,
                ModelAttemptOutcome.NOT_FOUND,
                ModelAttemptOutcome.NOT_RECONCILABLE,
            },
        )

    async def resolve(
        self, *, attempt_id: str, run_execution_token: str,
        reconciliation_token: str, expected_attempt_version: int,
        expected_step_version: int, resolution: str, request_hash: str,
        response_receipt: Mapping[str, object] | None = None,
        response_hash: str | None = None, stop_reason: str | None = None,
        provider_stop_reason: str | None = None,
        usage: Mapping[str, object] | None = None, actual_credits: int = 0,
        error_code: str | None = None,
        ambiguity_evidence: Mapping[str, object] | None = None,
    ) -> ModelAttemptReceipt:
        return await self._mutation("resolve_model_attempt", {
            "p_attempt_id": attempt_id,
            "p_run_execution_token": run_execution_token,
            "p_reconciliation_token": reconciliation_token,
            "p_expected_attempt_version": expected_attempt_version,
            "p_expected_step_version": expected_step_version,
            "p_resolution": resolution,
            "p_request_hash": request_hash,
            "p_response_receipt": (
                dict(response_receipt) if response_receipt is not None else None
            ),
            "p_response_hash": response_hash,
            "p_stop_reason": stop_reason,
            "p_provider_stop_reason": provider_stop_reason,
            "p_usage": dict(usage or {}),
            "p_actual_credits": actual_credits,
            "p_error_code": error_code,
            "p_ambiguity_evidence": dict(ambiguity_evidence or {}),
        }, {
            ModelAttemptOutcome.COMPLETED,
            ModelAttemptOutcome.ALREADY_COMPLETED,
            ModelAttemptOutcome.FAILED,
            ModelAttemptOutcome.ALREADY_FAILED,
            ModelAttemptOutcome.STILL_UNKNOWN,
            ModelAttemptOutcome.HANDOFF_TOOL_CALLS,
            ModelAttemptOutcome.NOT_FOUND,
        })

    async def record_late_receipt(
        self, *, attempt_id: str, provider_request_id: str | None,
        response_receipt: Mapping[str, object], response_hash: str,
        usage: Mapping[str, object], late_outcome: str,
        ambiguity_evidence: Mapping[str, object], actual_credits: int,
    ) -> ModelAttemptReceipt:
        return await self._mutation("record_late_model_receipt", {
            "p_attempt_id": attempt_id,
            "p_provider_request_id": provider_request_id,
            "p_response_receipt": dict(response_receipt),
            "p_response_hash": response_hash,
            "p_usage": dict(usage),
            "p_late_outcome": late_outcome,
            "p_ambiguity_evidence": dict(ambiguity_evidence),
            "p_actual_credits": actual_credits,
        }, {
            ModelAttemptOutcome.RECORDED,
            ModelAttemptOutcome.ALREADY_RECORDED,
            ModelAttemptOutcome.RECEIPT_CONFLICT,
            ModelAttemptOutcome.ADJUSTMENT_PENDING,
            ModelAttemptOutcome.NOT_FOUND,
        })

    async def _mutation(
        self, name: str, params: dict[str, object],
        allowed: set[ModelAttemptOutcome],
    ) -> ModelAttemptReceipt:
        return parse_attempt_receipt(await self._rpc(name, params), allowed)


class ModelAttemptResponseStartObserver(ModelResponseStartObserver):
    """Binds the model observer to one fenced persistent Attempt."""

    def __init__(
        self, repository: PostgresModelAttemptRepository, *,
        attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, request_hash: str,
    ) -> None:
        self._repository = repository
        self._attempt_id = attempt_id
        self._run_execution_token = run_execution_token
        self._expected_attempt_version = expected_attempt_version
        self._request_hash = request_hash
        self.receipt: ModelAttemptReceipt | None = None

    async def response_started(
        self, *, provider: str, provider_request_id: str | None,
    ) -> None:
        del provider
        self.receipt = await self._repository.mark_response_started(
            attempt_id=self._attempt_id,
            run_execution_token=self._run_execution_token,
            expected_attempt_version=self._expected_attempt_version,
            request_hash=self._request_hash,
            provider_request_id=provider_request_id,
        )
