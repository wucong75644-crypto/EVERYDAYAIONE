"""Runtime-owned tenant-scoped scheduler CAS boundary.

This module deliberately does not adapt the legacy scheduler worker store.  The
included store is an isolated test contract; a production store requires a
separate additive facts/RPC lane and therefore remains not ready.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from core.db_scope import DatabaseAccessKind, database_scope_from_client
from services.agent.runtime.domain import ActionAttempt
from services.agent.runtime.executors.specialist_contracts import validate_public_request


class SchedulerCasError(RuntimeError):
    """Failure-closed scheduler CAS error."""


class TenantScopedSchedulerCasStore(Protocol):
    production_ready: bool

    async def cas(self, *, attempt: ActionAttempt, task_id: str, expected_version: int,
                  operation: str, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class SchedulerControlStore(Protocol):
    """Runtime-owned five-operation control plane for scheduled_tasks."""

    production_ready: bool

    async def mutate(
        self, *, attempt: ActionAttempt, task_id: str, expected_version: int,
        operation: str, payload: Mapping[str, object], dispatch_intent_id: str,
        attempt_state_version: int,
    ) -> Mapping[str, object]: ...

    async def readback(
        self, *, attempt: ActionAttempt, idempotency_key: str,
    ) -> Mapping[str, object]: ...

    async def cancel(
        self, *, attempt: ActionAttempt, idempotency_key: str,
        reason: str = "runtime_cancel", ownership_token: str | None = None,
        expected_state_version: int | None = None,
    ) -> Mapping[str, object]: ...

    async def reconcile(
        self, *, attempt: ActionAttempt, idempotency_key: str,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, kw_only=True)
class SchedulerCasReadiness:
    service_wiring_ready: bool
    facts_ready: bool
    tenant_scoped_store_ready: bool
    production_ready: bool
    error_code: str | None = None

    @property
    def ready(self) -> bool:
        return all((self.service_wiring_ready, self.facts_ready,
                    self.tenant_scoped_store_ready, self.production_ready))


class RuntimeSchedulerCasBridge:
    """Binds scheduler mutation to Runtime facts and execution fencing."""

    def __init__(self, *, facts: object, store: TenantScopedSchedulerCasStore) -> None:
        if not getattr(store, "tenant_scoped", False):
            raise RuntimeError("SCHEDULER_TENANT_SCOPED_STORE_REQUIRED")
        if getattr(store, "production_ready", True):
            raise RuntimeError("SCHEDULER_PRODUCTION_STORE_FORBIDDEN_IN_A5")
        self.facts = facts
        self.store = store

    @property
    def readiness(self) -> SchedulerCasReadiness:
        return SchedulerCasReadiness(
            service_wiring_ready=True, facts_ready=False,
            tenant_scoped_store_ready=True, production_ready=False,
            error_code="SCHEDULER_FACTS_STORE_NOT_READY",
        )

    async def mutate(self, *, attempt: ActionAttempt, task_id: str, expected_version: int,
                     operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        self._validate(attempt, task_id, expected_version, operation, payload)
        if not hasattr(self.facts, "mutate_resource"):
            raise SchedulerCasError("SCHEDULER_FACTS_STORE_NOT_READY")
        bound = await self.facts.mutate_resource(
            "manage_scheduled_task", **_facts_params(attempt, task_id, expected_version, payload),
        )
        if not isinstance(bound, Mapping) or bound.get("outcome") not in {"bound", "updated", "already_exists"}:
            raise SchedulerCasError("SCHEDULER_RESOURCE_INTENT_UNPROVEN")
        return await self.store.cas(attempt=attempt, task_id=task_id,
                                    expected_version=expected_version,
                                    operation=operation, payload=payload)

    @staticmethod
    def _validate(attempt: ActionAttempt, task_id: str, expected_version: int,
                  operation: str, payload: Mapping[str, object]) -> None:
        if not getattr(attempt, "run_id", None) or not getattr(attempt, "action_id", None) or not getattr(attempt, "attempt_id", None):
            raise SchedulerCasError("SCHEDULER_RUN_CONTEXT_REQUIRED")
        if not getattr(attempt, "scope", None) or not getattr(attempt.scope, "scope_id", None):
            raise SchedulerCasError("SCHEDULER_SCOPE_REQUIRED")
        if not isinstance(task_id, str) or not task_id.strip():
            raise SchedulerCasError("SCHEDULER_TASK_ID_REQUIRED")
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise SchedulerCasError("SCHEDULER_VERSION_INVALID")
        if operation not in {"create", "update", "delete", "pause", "resume"}:
            raise SchedulerCasError("SCHEDULER_OPERATION_INVALID")
        validate_public_request(payload)


class MockTenantScopedSchedulerCasStore:
    """Concurrent, tenant-bound CAS store for isolated contract tests only."""

    tenant_scoped = True
    production_ready = False

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def cas(self, *, attempt: ActionAttempt, task_id: str, expected_version: int,
                  operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            scope = _scope_key(attempt)
            current = self._rows.get(task_id)
            if current is not None and current["scope"] != scope:
                raise SchedulerCasError("SCHEDULER_TENANT_SCOPE_MISMATCH")
            if current is None:
                if expected_version != 0 or operation != "create":
                    raise SchedulerCasError("SCHEDULER_VERSION_CONFLICT")
                row = {"task_id": task_id, "scope": scope, "version": 1,
                       "operation": operation, "payload": dict(payload),
                       "idempotency_key": _idempotency_key(attempt)}
                self._rows[task_id] = row
                return {"outcome": "created", **row}
            if current["idempotency_key"] == _idempotency_key(attempt) and current["version"] == expected_version + 1:
                return {"outcome": "already_applied", **current}
            if current["version"] != expected_version:
                raise SchedulerCasError("SCHEDULER_VERSION_CONFLICT")
            updated = {**current, "version": expected_version + 1,
                        "operation": operation, "payload": dict(payload),
                        "idempotency_key": _idempotency_key(attempt)}
            self._rows[task_id] = updated
            return {"outcome": "updated", **updated}


class PostgresTenantScopedSchedulerCasStore:
    """Worker-scoped adapter for the additive 227.05 CAS RPC lane."""

    tenant_scoped = True
    production_ready = False
    non_production_ready = True

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def cas(self, *, attempt: ActionAttempt, task_id: str, expected_version: int,
                  operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        params = {
            "p_attempt_id": str(attempt.attempt_id), "p_action_id": str(attempt.action_id),
            "p_run_id": str(attempt.run_id), "p_org_id": _uuid_or_none(attempt.scope.org_id),
            "p_user_id": _uuid_or_none(attempt.scope.user_id),
            "p_scope_kind": attempt.scope.kind.value, "p_scope_id": str(attempt.scope.scope_id),
            "p_task_id": task_id, "p_expected_version": expected_version,
            "p_operation": operation, "p_payload": dict(payload),
            "p_request_hash": str(attempt.request_hash),
            "p_execution_token": str(attempt.lease.fencing_token),
            "p_idempotency_key": _idempotency_key(attempt),
        }
        response = (await self._database.rpc(
            "mutate_agent_runtime_scheduler_cas", params,
        ).execute()).data
        if not isinstance(response, Mapping):
            raise SchedulerCasError("SCHEDULER_CAS_RPC_INVALID")
        outcome = response.get("outcome")
        if outcome in {"cas_conflict", "fenced"}:
            raise SchedulerCasError(
                "SCHEDULER_VERSION_CONFLICT" if outcome == "cas_conflict" else "SCHEDULER_FENCED",
            )
        if outcome not in {"created", "updated", "already_applied"}:
            raise SchedulerCasError("SCHEDULER_CAS_RPC_REJECTED")
        return dict(response)

    async def recover(self, *, attempt: ActionAttempt, task_id: str,
                      expected_version: int) -> Mapping[str, object]:
        response = (await self._database.rpc("recover_agent_runtime_scheduler_cas", {
            "p_attempt_id": str(attempt.attempt_id), "p_action_id": str(attempt.action_id),
            "p_run_id": str(attempt.run_id), "p_org_id": _uuid_or_none(attempt.scope.org_id),
            "p_user_id": _uuid_or_none(attempt.scope.user_id),
            "p_scope_kind": attempt.scope.kind.value, "p_scope_id": str(attempt.scope.scope_id),
            "p_task_id": task_id, "p_expected_version": expected_version,
            "p_execution_token": str(attempt.lease.fencing_token),
            "p_request_hash": str(attempt.request_hash),
        }).execute()).data
        if not isinstance(response, Mapping):
            raise SchedulerCasError("SCHEDULER_RECOVERY_RPC_INVALID")
        if response.get("outcome") != "recovered":
            raise SchedulerCasError("SCHEDULER_RECOVERY_NOT_CONFIRMED")
        return dict(response)


class PostgresSchedulerControlStore:
    """Worker-scoped adapter for the additive 227.28 control-plane RPCs."""

    tenant_scoped = True
    production_ready = False
    non_production_ready = True

    def __init__(self, database: Any) -> None:
        scope = database_scope_from_client(database)
        if scope is None or scope.access_kind is not DatabaseAccessKind.AGENT_RUNTIME:
            raise ValueError("WORKER_SCOPED_DATABASE_CLIENT_REQUIRED")
        self._database = database

    async def mutate(
        self, *, attempt: ActionAttempt, task_id: str, expected_version: int,
        operation: str, payload: Mapping[str, object], dispatch_intent_id: str,
        attempt_state_version: int,
    ) -> Mapping[str, object]:
        if operation not in {"create", "update", "pause", "resume", "delete"}:
            raise SchedulerCasError("SCHEDULER_OPERATION_INVALID")
        if not dispatch_intent_id.strip():
            raise SchedulerCasError("SCHEDULER_DISPATCH_INTENT_REQUIRED")
        from services.agent.runtime.scheduler_control_payload import (
            normalize_scheduler_control_payload,
        )
        normalized_payload = normalize_scheduler_control_payload(operation, payload)
        response = await self._rpc(
            "mutate_agent_runtime_scheduled_task_control_v1", {
                **_control_identity(attempt),
                "p_task_id": task_id,
                "p_operation": operation,
                "p_expected_state_version": expected_version,
                "p_attempt_state_version": attempt_state_version,
                "p_request_hash": str(attempt.request_hash),
                "p_execution_token": str(attempt.lease.fencing_token),
                "p_idempotency_key": _idempotency_key(attempt),
                "p_dispatch_intent_id": dispatch_intent_id,
                "p_payload": normalized_payload,
            },
        )
        if response.get("outcome") in {"committed", "readback", "cas_conflict", "reconcile_required"}:
            return dict(response)
        raise SchedulerCasError(
            "SCHEDULER_KILL_FENCED"
            if response.get("error_code") == "RUNTIME_KILL_EPOCH_FENCED"
            else "SCHEDULER_CONTROL_RPC_REJECTED",
        )

    async def readback(
        self, *, attempt: ActionAttempt, idempotency_key: str,
        ownership_token: str, expected_state_version: int,
    ) -> Mapping[str, object]:
        return await self._rpc("read_agent_runtime_scheduled_task_control_v1", {
            "p_attempt_id": str(attempt.attempt_id),
            "p_request_hash": str(attempt.request_hash),
            "p_execution_token": ownership_token,
            "p_expected_state_version": expected_state_version,
            "p_idempotency_key": idempotency_key,
        })

    async def cancel(
        self, *, attempt: ActionAttempt, idempotency_key: str,
        reason: str = "runtime_cancel", ownership_token: str | None = None,
        expected_state_version: int | None = None,
    ) -> Mapping[str, object]:
        if ownership_token is None or expected_state_version is None:
            raise SchedulerCasError("SCHEDULER_CANCEL_OWNERSHIP_REQUIRED")
        return await self._rpc("cancel_agent_runtime_scheduled_task_control_v1", {
            "p_attempt_id": str(attempt.attempt_id),
            "p_request_hash": str(attempt.request_hash),
            "p_execution_token": ownership_token,
            "p_expected_state_version": expected_state_version,
            "p_idempotency_key": idempotency_key,
            "p_reason": reason,
        })

    async def reconcile(
        self, *, attempt: ActionAttempt, idempotency_key: str,
        ownership_token: str, expected_state_version: int,
    ) -> Mapping[str, object]:
        return await self._rpc("reconcile_agent_runtime_scheduled_task_control_v1", {
            "p_attempt_id": str(attempt.attempt_id),
            "p_request_hash": str(attempt.request_hash),
            "p_execution_token": ownership_token,
            "p_expected_state_version": expected_state_version,
            "p_idempotency_key": idempotency_key,
        })

    async def _rpc(self, name: str, params: Mapping[str, object]) -> Mapping[str, object]:
        data = (await self._database.rpc(name, dict(params)).execute()).data
        if not isinstance(data, Mapping):
            raise SchedulerCasError("SCHEDULER_CONTROL_RPC_INVALID")
        return dict(data)


class MockSchedulerControlStore:
    """Disposable five-operation scheduler store; never production-ready."""

    tenant_scoped = True
    production_ready = False
    non_production_ready = True

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, object]] = {}
        self._receipts: dict[str, dict[str, object]] = {}
        self._intent_fingerprints: dict[str, tuple[str, str, str, str] | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def mutate(
        self, *, attempt: ActionAttempt, task_id: str, expected_version: int,
        operation: str, payload: Mapping[str, object], dispatch_intent_id: str,
        attempt_state_version: int,
    ) -> Mapping[str, object]:
        del dispatch_intent_id, attempt_state_version
        if operation not in {"create", "update", "pause", "resume", "delete"}:
            raise SchedulerCasError("SCHEDULER_OPERATION_INVALID")
        from services.agent.runtime.scheduler_control_payload import (
            normalize_scheduler_control_payload,
        )
        payload = normalize_scheduler_control_payload(operation, payload)
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            key = _idempotency_key(attempt)
            fingerprint = (
                task_id, operation, str(attempt.request_hash),
                json.dumps(dict(payload), sort_keys=True, separators=(',', ':'), default=str),
            )
            if key in self._receipts:
                if self._intent_fingerprints.get(key) not in (None, fingerprint):
                    raise SchedulerCasError("SCHEDULER_IDEMPOTENCY_CONFLICT")
                return {"outcome": "readback", **self._receipts[key]}
            self._intent_fingerprints[key] = fingerprint
            scope = _scope_key(attempt)
            current = self._rows.get(task_id)
            if current is not None and current["scope"] != scope:
                raise SchedulerCasError("SCHEDULER_TENANT_SCOPE_MISMATCH")
            if operation == "create":
                if current is not None or expected_version != 0:
                    return self._receipt(key, task_id, expected_version, "cas_conflict")
                row = {"task_id": task_id, "scope": scope, "version": 1,
                       "status": "active", **dict(payload)}
                self._rows[task_id] = row
                return self._receipt(key, task_id, 1, "committed", row)
            if current is None or current["version"] != expected_version:
                return self._receipt(key, task_id, int(current["version"]) if current else 0, "cas_conflict")
            if operation == "delete":
                del self._rows[task_id]
                return self._receipt(key, task_id, expected_version + 1, "committed", {"task_id": task_id, "deleted": True})
            updated = dict(current)
            if operation == "pause":
                updated["status"] = "paused"
            elif operation == "resume":
                updated["status"] = "active"
            else:
                updated.update(payload)
            updated["version"] = expected_version + 1
            self._rows[task_id] = updated
            return self._receipt(key, task_id, int(updated["version"]), "committed", updated)

    async def readback(
        self, *, attempt: ActionAttempt, idempotency_key: str,
        ownership_token: str, expected_state_version: int,
    ) -> Mapping[str, object]:
        del ownership_token, expected_state_version
        del attempt
        receipt = self._receipts.get(idempotency_key)
        return {"outcome": "readback", **receipt} if receipt else {"outcome": "not_found"}

    async def cancel(self, *, attempt: ActionAttempt, idempotency_key: str,
                     reason: str = "runtime_cancel", ownership_token: str | None = None,
                     expected_state_version: int | None = None) -> Mapping[str, object]:
        del ownership_token, expected_state_version
        del attempt
        receipt = self._receipts.get(idempotency_key)
        if receipt:
            return {"outcome": "committed_readback", **receipt}
        self._intent_fingerprints[idempotency_key] = None
        return self._receipt(idempotency_key, "unknown", 0, "cancelled", {"cancelled": True, "reason": reason})

    async def reconcile(
        self, *, attempt: ActionAttempt, idempotency_key: str,
        ownership_token: str, expected_state_version: int,
    ) -> Mapping[str, object]:
        return await self.readback(
            attempt=attempt, idempotency_key=idempotency_key,
            ownership_token=ownership_token,
            expected_state_version=expected_state_version,
        )

    def _receipt(self, key: str, task_id: str, version: int, outcome: str, task: Mapping[str, object] | None = None) -> Mapping[str, object]:
        value = {
            "intent_id": key, "task_id": task_id, "state_version": version,
            "task": dict(task or {}), "receipt_outcome": outcome,
        }
        self._receipts[key] = value
        return {"outcome": outcome, **value}


def scheduler_control_state(response: Mapping[str, object]) -> str:
    """Map durable control outcomes to the provider state machine."""
    outcome = response.get("outcome")
    if (
        outcome in {"readback", "committed_readback"}
        and response.get("receipt_outcome") == "cas_conflict"
    ):
        return "failed"
    if outcome in {"committed", "readback", "committed_readback"}:
        return "completed"
    if outcome == "cancelled":
        return "cancelled"
    if outcome in {"cas_conflict", "fenced"}:
        return "failed"
    return "unknown"


def scheduler_control_result(response: Mapping[str, object]) -> dict[str, object]:
    """Return a secret-free provider result with an explicit terminal mapping."""
    return {
        **dict(response),
        "state": scheduler_control_state(response),
        "evidence": {
            "control_outcome": response.get("outcome"),
            "receipt_id": response.get("receipt_id"),
            "intent_id": response.get("intent_id"),
            "cancel_confirmed": response.get("cancel_confirmed") is True,
            "proof_hash": response.get("proof_hash"),
        },
    }


def _scope_key(attempt: ActionAttempt) -> tuple[str, str]:
    return (attempt.scope.kind.value, str(attempt.scope.scope_id))


def _uuid_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def _idempotency_key(attempt: ActionAttempt) -> str:
    key = getattr(attempt, "idempotency_key", None)
    if not isinstance(key, str) or not key.strip():
        raise SchedulerCasError("SCHEDULER_IDEMPOTENCY_KEY_REQUIRED")
    return key


def _facts_params(attempt: ActionAttempt, task_id: str, expected_version: int,
                  payload: Mapping[str, object]) -> dict[str, object]:
    return {
        "p_action_id": str(attempt.action_id), "p_attempt_id": str(attempt.attempt_id),
        "p_request_hash": str(attempt.request_hash),
        "p_idempotency_key": _idempotency_key(attempt),
        "p_execution_token": str(attempt.lease.fencing_token),
        "p_task_id": task_id, "p_expected_state_version": expected_version,
        "p_payload": dict(payload),
    }


def _control_identity(attempt: ActionAttempt) -> dict[str, object]:
    scope = attempt.scope
    return {
        "p_attempt_id": str(attempt.attempt_id),
        "p_action_id": str(attempt.action_id),
        "p_run_id": str(attempt.run_id),
        "p_org_id": _uuid_or_none(scope.org_id),
        "p_user_id": _uuid_or_none(scope.user_id),
        "p_scope_kind": scope.kind.value,
        "p_scope_id": str(scope.scope_id),
    }


__all__ = [
    "MockTenantScopedSchedulerCasStore", "PostgresSchedulerControlStore",
    "PostgresTenantScopedSchedulerCasStore", "RuntimeSchedulerCasBridge",
    "SchedulerCasError", "SchedulerCasReadiness", "SchedulerControlStore",
    "TenantScopedSchedulerCasStore", "scheduler_control_result",
    "scheduler_control_state",
]
