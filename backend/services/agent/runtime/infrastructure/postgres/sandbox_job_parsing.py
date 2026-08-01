"""Fail-closed parser for Sandbox Job RPC receipts."""

from __future__ import annotations

from typing import Mapping

from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    IdempotencyConflictError,
    InvalidTransitionError,
    PersistenceContractError,
    StaleVersionError,
    TerminalConflictError,
)
from services.agent.runtime.domain.sandbox_job import (
    SandboxCleanupStatus,
    SandboxJobSnapshot,
    SandboxJobStatus,
    SandboxMaterializationStatus,
)
from services.agent.runtime.infrastructure.postgres.parsing import (
    require_datetime,
    require_enum,
    require_int,
    require_json_object,
    require_mapping,
    require_text,
    require_uuid,
)
from services.agent.runtime.ports.sandbox_job import (
    SandboxJobOutcome,
    SandboxJobReceipt,
)


_ERRORS = {
    "idempotency_conflict": IdempotencyConflictError,
    "ownership_lost": FencingTokenMismatchError,
    "stale_version": StaleVersionError,
    "invalid_transition": InvalidTransitionError,
    "dispatch_intent_invalid": InvalidTransitionError,
    "scope_binding_invalid": InvalidTransitionError,
    "terminal_guard_failed": InvalidTransitionError,
    "terminal_conflict": TerminalConflictError,
    "malformed_receipt": PersistenceContractError,
    "receipt_hash_conflict": PersistenceContractError,
}


def parse_sandbox_job_receipt(value: object) -> SandboxJobReceipt:
    row = require_mapping(value, "Sandbox Job RPC")
    name = require_text(row, "outcome")
    error = _ERRORS.get(name)
    if error is not None:
        raise error(name)
    try:
        outcome = SandboxJobOutcome(name)
    except ValueError as exc:
        raise PersistenceContractError(
            f"unknown Sandbox Job outcome: {name}",
        ) from exc
    raw_job = row.get("job")
    return SandboxJobReceipt(
        outcome=outcome,
        job=_snapshot(require_mapping(raw_job, "Sandbox Job"))
        if raw_job is not None else None,
    )


def _snapshot(row: Mapping[str, object]) -> SandboxJobSnapshot:
    return SandboxJobSnapshot(
        job_id=require_uuid(row, "id"),
        action_id=require_uuid(row, "action_id"),
        attempt_id=require_uuid(row, "attempt_id"),
        dispatch_intent_id=require_uuid(row, "dispatch_intent_id"),
        external_idempotency_key=require_text(
            row, "external_idempotency_key",
        ),
        request_hash=require_text(row, "request_hash"),
        code_sha256=require_text(row, "code_sha256"),
        resource_limits=require_json_object(row, "resource_limits"),
        input_manifest=require_json_object(row, "input_manifest"),
        status=require_enum(row, "status", SandboxJobStatus),
        state_version=require_int(row, "state_version"),
        fencing_token=require_int(row, "fencing_token"),
        cleanup_status=require_enum(
            row, "cleanup_status", SandboxCleanupStatus,
        ),
        materialization_status=require_enum(
            row, "materialization_status", SandboxMaterializationStatus,
        ),
        queued_at=require_datetime(row, "queued_at"),
        claim_token=require_uuid(row, "claim_token", optional=True),
        lease_expires_at=_optional_time(row, "lease_expires_at"),
        reconciliation_token=require_uuid(
            row, "reconciliation_token", optional=True,
        ),
        reconciliation_lease_expires_at=_optional_time(
            row, "reconciliation_lease_expires_at",
        ),
        terminal_at=_optional_time(row, "terminal_at"),
        artifact_manifest=require_json_object(row, "artifact_manifest"),
        partial_effects=require_json_object(row, "partial_effects"),
        terminal_reason=(
            require_text(row, "terminal_reason")
            if row.get("terminal_reason") is not None else None
        ),
        stdout_summary=(
            str(row["stdout_summary"])
            if row.get("stdout_summary") is not None else None
        ),
        stderr_summary=(
            str(row["stderr_summary"])
            if row.get("stderr_summary") is not None else None
        ),
        cleanup_deadline_at=_optional_time(row, "cleanup_deadline_at"),
    )


def _optional_time(
    row: Mapping[str, object], field: str,
):
    if row.get(field) is None:
        return None
    return require_datetime(row, field)
