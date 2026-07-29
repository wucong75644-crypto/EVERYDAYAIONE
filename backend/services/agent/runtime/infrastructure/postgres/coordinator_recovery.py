"""PostgreSQL adapter for AR-14 private recovery RPCs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from psycopg import InterfaceError, OperationalError

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    PersistenceContractError,
)
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
    ActionRecoveryClaim,
    CoordinatorRecoveryPort,
    ModelResultDraft,
    RecoveryOutcome,
    RunAggregateSnapshot,
    RunRecoveryClaim,
)


class PostgresCoordinatorRecoveryRepository(CoordinatorRecoveryPort):
    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def _rpc(self, name: str, params: dict[str, object]) -> object:
        response = await self._database.rpc(name, params).execute()
        return response.data

    async def claim_next_run(
        self, *, worker_id: str, lease_seconds: int = 90,
        max_attempts: int = 3,
    ) -> RunRecoveryClaim:
        try:
            raw = await self._rpc("claim_next_agent_run", {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
                "p_max_attempts": max_attempts,
            })
        except (OperationalError, InterfaceError):
            raw = await self._rpc("get_claimed_agent_run", {
                "p_worker_id": worker_id,
            })
        row = _mapping(raw, "Run claim")
        outcome = _outcome(row, {
            "claimed", "found", "not_found", "attempts_exhausted",
        })
        return RunRecoveryClaim(
            outcome=(
                RecoveryOutcome.CLAIMED
                if outcome == "found" else RecoveryOutcome(outcome)
            ),
            run_id=_uuid(row.get("entity_id")),
            execution_token=_uuid(row.get("execution_token")),
            state_version=_integer(row.get("state_version")),
        )

    async def get_run_aggregate(
        self, *, run_id: str, worker_id: str, execution_token: str,
    ) -> RunAggregateSnapshot:
        row = _mapping(await self._rpc("get_agent_run_aggregate", {
            "p_run_id": run_id,
            "p_worker_id": worker_id,
            "p_execution_token": execution_token,
        }), "Run aggregate")
        outcome = _outcome(row, {"found", "not_found", "ownership_lost"})
        if outcome == "ownership_lost":
            raise FencingTokenMismatchError(outcome)
        if outcome == "not_found":
            raise PersistenceContractError("claimed Run aggregate not found")
        _require_fields(
            row.get("run"), "Run",
            ("id", "status", "state_version", "execution_token"),
        )
        return RunAggregateSnapshot(
            run=_mapping(row.get("run"), "Run"),
            latest_model_step=_optional_mapping(
                row.get("latest_model_step"), "ModelStep",
            ),
            unresolved_model_attempt=_optional_mapping(
                row.get("unresolved_model_attempt"), "ModelAttempt",
            ),
            latest_model_result=_optional_mapping(
                row.get("latest_model_result"), "ModelResult",
            ),
            model_steps=_mapping_tuple(row.get("model_steps"), "ModelSteps"),
            actions=_mapping_tuple(row.get("actions"), "Actions"),
        )

    async def complete_model_with_result(
        self, *, attempt_id: str, run_execution_token: str,
        expected_attempt_version: int, expected_step_version: int,
        request_hash: str, response_receipt: Mapping[str, object],
        response_hash: str, stop_reason: str,
        provider_stop_reason: str | None, usage: Mapping[str, object],
        actual_credits: int, result: ModelResultDraft,
    ) -> RecoveryOutcome:
        row = _mapping(await self._rpc(
            "complete_model_attempt_with_result", {
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
                "p_output_kind": result.output_kind,
                "p_text_content": result.text_content,
                "p_structured_content": (
                    result.structured_content
                ),
                "p_schema_revision": result.schema_revision,
                "p_content_hash": result.content_hash,
            },
        ), "Model result terminal")
        return RecoveryOutcome(_outcome(
            row, {
                "completed", "already_completed",
                "run_cancelled_use_late_receipt",
            },
        ))

    async def renew_model_attempt(
        self, *, attempt_id: str, run_execution_token: str,
        attempt_execution_token: str, expected_state_version: int,
        lease_seconds: int = 120,
    ) -> int:
        row = _mapping(await self._rpc(
            "renew_model_attempt_execution", {
                "p_attempt_id": attempt_id,
                "p_run_execution_token": run_execution_token,
                "p_attempt_execution_token": attempt_execution_token,
                "p_expected_state_version": expected_state_version,
                "p_lease_seconds": lease_seconds,
            },
        ), "ModelAttempt renewal")
        _outcome(row, {"renewed"})
        version = _integer(row.get("state_version"))
        if version is None:
            raise PersistenceContractError("renewal state_version required")
        return version

    async def claim_action_dispatch(
        self, *, worker_id: str, claim_request_id: str,
        batch_size: int = 10, lease_seconds: int = 120,
    ) -> tuple[ActionDispatchSnapshot, ...]:
        try:
            raw = await self._rpc("claim_ready_agent_action_snapshots", {
                "p_worker_id": worker_id,
                "p_claim_request_id": claim_request_id,
                "p_batch_size": batch_size,
                "p_lease_seconds": lease_seconds,
            })
        except (OperationalError, InterfaceError):
            return await self.get_action_dispatch_batch(
                worker_id=worker_id, claim_request_id=claim_request_id,
            )
        return _snapshot_batch(raw, {"claimed"})

    async def get_action_dispatch_batch(
        self, *, worker_id: str, claim_request_id: str,
    ) -> tuple[ActionDispatchSnapshot, ...]:
        return _snapshot_batch(
            await self._rpc("get_agent_action_snapshot_batch", {
                "p_worker_id": worker_id,
                "p_claim_request_id": claim_request_id,
            }),
            {"found"},
        )

    async def claim_action_reconciliation(
        self, *, worker_id: str, lease_seconds: int = 120,
    ) -> ActionRecoveryClaim:
        try:
            raw = await self._rpc(
                "claim_next_agent_action_reconciliation", {
                    "p_worker_id": worker_id,
                    "p_lease_seconds": lease_seconds,
                },
            )
        except (OperationalError, InterfaceError):
            raw = await self._rpc(
                "get_claimed_agent_action_reconciliation", {
                    "p_worker_id": worker_id,
                },
            )
        row = _mapping(raw, "Action reconciliation")
        name = _outcome(row, {"claimed", "found", "not_found"})
        outcome = (
            RecoveryOutcome.CLAIMED
            if name in {"claimed", "found"} else RecoveryOutcome.NOT_FOUND
        )
        return ActionRecoveryClaim(
            outcome=outcome,
            attempt_id=_uuid(row.get("attempt_id")),
            execution_token=_uuid(row.get("execution_token")),
            state_version=_integer(row.get("state_version")),
            lease_expires_at=_time(row.get("lease_expires_at")),
            snapshot=(
                _snapshot(row.get("snapshot"))
                if row.get("snapshot") is not None else None
            ),
        )


def _snapshot_batch(
    value: object, allowed: set[str],
) -> tuple[ActionDispatchSnapshot, ...]:
    row = _mapping(value, "Action snapshot batch")
    name = _outcome(row, allowed | {"not_found"})
    if name == "not_found":
        return ()
    snapshots = row.get("snapshots")
    if not isinstance(snapshots, list):
        raise PersistenceContractError("Action snapshots array required")
    return tuple(_snapshot(item) for item in snapshots)


def _snapshot(value: object) -> ActionDispatchSnapshot:
    attempt = _mapping(value, "Action dispatch snapshot")
    action = _mapping(attempt.get("action"), "Action")
    for field in ("id", "action_id", "execution_token", "request_hash"):
        if field not in attempt:
            raise PersistenceContractError(f"ActionAttempt {field} required")
    _require_fields(
        action, "Action", (
            "id", "run_id", "session_id", "tool_name", "arguments",
            "request_hash", "policy_decision", "retry_disposition",
        ),
    )
    return ActionDispatchSnapshot(attempt=attempt, action=action)


def _require_fields(
    value: object, name: str, fields: tuple[str, ...],
) -> Mapping[str, object]:
    row = _mapping(value, name)
    missing = [field for field in fields if field not in row]
    if missing:
        raise PersistenceContractError(
            f"{name} fields required: {','.join(missing)}",
        )
    return row


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PersistenceContractError(f"{name} object required")
    return value


def _optional_mapping(
    value: object, name: str,
) -> Mapping[str, object] | None:
    return None if value is None else _mapping(value, name)


def _mapping_tuple(
    value: object, name: str,
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise PersistenceContractError(f"{name} array required")
    return tuple(_mapping(item, name) for item in value)


def _outcome(row: Mapping[str, object], allowed: set[str]) -> str:
    value = row.get("outcome")
    if value not in allowed:
        if value == "ownership_lost":
            raise FencingTokenMismatchError(str(value))
        raise PersistenceContractError(f"unexpected RPC outcome: {value}")
    return str(value)


def _uuid(value: object) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except ValueError as error:
        raise PersistenceContractError("UUID required") from error


def _integer(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PersistenceContractError("nonnegative integer required")
    return value


def _time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PersistenceContractError("aware timestamp required")
    return value
