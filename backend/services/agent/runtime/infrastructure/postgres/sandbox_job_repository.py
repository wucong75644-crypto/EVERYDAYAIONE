"""Scoped PostgreSQL adapter for Sandbox Job Controller RPCs."""

from __future__ import annotations

from typing import Any, Mapping

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.infrastructure.postgres.sandbox_job_parsing import (
    parse_sandbox_job_receipt,
)
from services.agent.runtime.ports.sandbox_job import SandboxJobReceipt


class PostgresSandboxJobRepository:
    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind not in {
            DatabaseAccessKind.RUNTIME,
            DatabaseAccessKind.SANDBOX_WORKER,
        }:
            raise ValueError("SANDBOX_JOB_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database
        self._access_kind = scope.access_kind

    async def _rpc(
        self, name: str, params: dict[str, object],
        required: DatabaseAccessKind,
    ) -> SandboxJobReceipt:
        if self._access_kind is not required:
            raise PermissionError("SANDBOX_JOB_ADAPTER_SCOPE_MISMATCH")
        response = await self._database.rpc(name, params).execute()
        return parse_sandbox_job_receipt(response.data)

    async def create_or_get(self, **values: object) -> SandboxJobReceipt:
        names = (
            "action_id", "attempt_id", "dispatch_intent_id",
            "expected_action_version", "expected_attempt_version",
            "external_idempotency_key", "request_hash", "executor_type",
            "executor_revision", "runtime_revision", "workspace_scope_ref",
            "code_sha256", "input_manifest", "resource_limits",
        )
        return await self._runtime("create_or_get_sandbox_job", values, names)

    async def get(self, *, job_id: str) -> SandboxJobReceipt:
        response = await self._database.rpc(
            "get_sandbox_job", {"p_job_id": job_id},
        ).execute()
        return parse_sandbox_job_receipt(response.data)

    async def get_owned(self, **values: object) -> SandboxJobReceipt:
        return await self._worker_named(
            "get_owned_sandbox_job", values,
            ("job_id", "worker_id", "claim_token", "fencing_token"),
        )

    async def readback_by_binding(self, **values: object) -> SandboxJobReceipt:
        names = (
            "external_idempotency_key", "action_id", "attempt_id",
            "dispatch_intent_id", "request_hash", "org_id", "user_id",
            "session_id", "run_id", "executor_type", "executor_revision",
            "runtime_revision",
        )
        return await self._runtime(
            "get_sandbox_job_by_binding", values, names,
        )

    async def claim(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> SandboxJobReceipt:
        return await self._worker("claim_next_sandbox_job", {
            "p_worker_id": worker_id, "p_lease_seconds": lease_seconds,
        })

    async def claim_recoverable(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> SandboxJobReceipt:
        return await self._worker(
            "claim_next_recoverable_sandbox_job", {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )

    async def claim_next_reconciliation(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> SandboxJobReceipt:
        return await self._worker(
            "claim_next_sandbox_job_reconciliation", {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )

    async def renew(self, **values: object) -> SandboxJobReceipt:
        return await self._worker_values(
            "renew_sandbox_job_lease", values, lease=True,
        )

    async def mark_started(self, **values: object) -> SandboxJobReceipt:
        return await self._worker_values(
            "mark_sandbox_job_started", values, extra=("phase",),
        )

    async def recover_expired(
        self, *, job_id: str, expected_version: int,
    ) -> SandboxJobReceipt:
        return await self._worker("recover_expired_sandbox_job", {
            "p_job_id": job_id, "p_expected_version": expected_version,
        })

    async def request_cancel(
        self, *, job_id: str, expected_version: int,
    ) -> SandboxJobReceipt:
        return await self._rpc("request_sandbox_job_cancel", {
            "p_job_id": job_id, "p_expected_version": expected_version,
        }, DatabaseAccessKind.RUNTIME)

    async def record_cancel_signal(self, **values: object) -> SandboxJobReceipt:
        return await self._worker_values(
            "record_sandbox_cancel_signal", values, extra=("signal_state",),
        )

    async def finish(self, **values: object) -> SandboxJobReceipt:
        names = (
            "job_id", "claim_token", "fencing_token", "expected_version",
            "terminal_status", "terminal_reason", "receipt_hash", "receipt",
        )
        return await self._worker_named("finish_sandbox_job", values, names)

    async def record_unknown(self, **values: object) -> SandboxJobReceipt:
        params = dict(values)
        params.setdefault("cleanup_deadline_at", None)
        return await self._worker_values(
            "record_sandbox_job_unknown", params,
            extra=(
                "ambiguity_evidence", "partial_effects",
                "cleanup_deadline_at",
            ),
        )

    async def claim_reconciliation(self, **values: object) -> SandboxJobReceipt:
        names = (
            "job_id", "expected_version", "worker_id", "lease_seconds",
        )
        return await self._worker_named(
            "claim_sandbox_job_reconciliation", values, names,
        )

    async def renew_reconciliation(self, **values: object) -> SandboxJobReceipt:
        names = (
            "job_id", "reconciliation_token", "expected_version",
            "lease_seconds",
        )
        return await self._worker_named(
            "renew_sandbox_job_reconciliation", values, names,
        )

    async def resolve_reconciliation(self, **values: object) -> SandboxJobReceipt:
        names = (
            "job_id", "reconciliation_token", "expected_version",
            "resolution", "terminal_reason", "receipt_hash", "receipt",
        )
        return await self._worker_named(
            "resolve_sandbox_job_reconciliation", values, names,
        )

    async def record_cleanup(self, **values: object) -> SandboxJobReceipt:
        names = (
            "job_id", "reconciliation_token", "expected_version",
            "cleanup_status", "cleanup_evidence",
        )
        return await self._worker_named(
            "record_sandbox_job_cleanup", values, names,
        )

    async def record_reconciled_partials(
        self, **values: object,
    ) -> SandboxJobReceipt:
        names = (
            "job_id", "reconciliation_token", "expected_version",
            "partial_effects",
        )
        return await self._worker_named(
            "record_reconciled_sandbox_partials", values, names,
        )

    async def _runtime(
        self, name: str, values: Mapping[str, object], names: tuple[str, ...],
    ) -> SandboxJobReceipt:
        return await self._named(
            name, values, names, DatabaseAccessKind.RUNTIME,
        )

    async def _worker(
        self, name: str, params: dict[str, object],
    ) -> SandboxJobReceipt:
        return await self._rpc(name, params, DatabaseAccessKind.SANDBOX_WORKER)

    async def _worker_named(
        self, name: str, values: Mapping[str, object], names: tuple[str, ...],
    ) -> SandboxJobReceipt:
        return await self._named(
            name, values, names, DatabaseAccessKind.SANDBOX_WORKER,
        )

    async def _worker_values(
        self, name: str, values: Mapping[str, object], *,
        lease: bool = False, extra: tuple[str, ...] = (),
    ) -> SandboxJobReceipt:
        names = (
            "job_id", "claim_token", "fencing_token", "expected_version",
            *(("lease_seconds",) if lease else ()), *extra,
        )
        return await self._worker_named(name, values, names)

    async def _named(
        self, name: str, values: Mapping[str, object],
        names: tuple[str, ...], required: DatabaseAccessKind,
    ) -> SandboxJobReceipt:
        missing = [item for item in names if item not in values]
        if missing:
            raise ValueError(f"missing Sandbox Job arguments: {missing}")
        params = {
            f"p_{item}": (
                dict(values[item])
                if isinstance(values[item], Mapping) else values[item]
            )
            for item in names
        }
        return await self._rpc(name, params, required)
