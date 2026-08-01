"""Injected Action dispatch and reconciliation loops for AR-14."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from typing import Mapping
from uuid import uuid4

from services.agent.runtime.domain.errors import (
    DomainContractError,
    FencingTokenMismatchError,
    LeaseExpiredError,
    StaleVersionError,
)
from services.agent.runtime.domain import ActionAttempt, ActionAttemptStatus
from services.agent.runtime.executors.resolver import ActionExecutorResolver
from services.agent.runtime.ports.authorization import (
    ActionAuthorizationPort,
    DispatchGateDenied,
)
from services.agent.runtime.ports.action_repository import ActionRepositoryPort
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
    CoordinatorRecoveryPort,
    RecoveryOutcome,
)
from services.agent.runtime.ports.executor import (
    DispatchCapabilityIssuerPort,
    ExecutionOutcome,
    ExecutionReceipt,
    ExecutorDispatchUnknown,
)


class ActionLoopDriver:
    def __init__(
        self, *, recovery_repository: CoordinatorRecoveryPort,
        action_repository: ActionRepositoryPort,
        authorization_repository: ActionAuthorizationPort,
        resolver: ActionExecutorResolver, worker_id: str,
        lease_seconds: int = 120, renew_interval: float = 40.0,
        capability_issuer: DispatchCapabilityIssuerPort | None = None,
    ) -> None:
        if renew_interval <= 0:
            raise ValueError("ACTION_RENEW_INTERVAL_MUST_BE_POSITIVE")
        self._recovery = recovery_repository
        self._actions = action_repository
        self._authorization = authorization_repository
        self._resolver = resolver
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._renew_interval = renew_interval
        self._capability_issuer = capability_issuer

    async def dispatch_once(self) -> bool:
        request_id = f"{self._worker_id}:{uuid4()}"
        snapshots = await self._recovery.claim_action_dispatch(
            worker_id=self._worker_id, claim_request_id=request_id,
        )
        if not snapshots:
            return False
        for snapshot in snapshots:
            try:
                await self._dispatch(snapshot)
            except (
                FencingTokenMismatchError,
                LeaseExpiredError,
                StaleVersionError,
            ):
                continue
        return True

    async def reconcile_once(self) -> bool:
        claim = await self._recovery.claim_action_reconciliation(
            worker_id=self._worker_id,
        )
        if claim.outcome is RecoveryOutcome.NOT_FOUND:
            return False
        if claim.snapshot is None:
            raise RuntimeError("ACTION_RECONCILIATION_SNAPSHOT_REQUIRED")
        lease = _ActionLease(
            repository=self._actions,
            attempt_id=_required(claim.attempt_id, "attempt_id"),
            token=_required(claim.execution_token, "reconciliation token"),
            state_version=_required_int(
                claim.state_version, "reconciliation state version",
            ),
            lease_seconds=self._lease_seconds,
            renew_interval=self._renew_interval,
            reconciliation=True,
        )
        try:
            resolved = self._resolver.resolve(claim.snapshot)
            reconciled_attempt = self._with_capabilities(
                resolved.attempt, resolved.descriptor, "reconcile",
            )
            receipt = await lease.run(
                resolved.executor.reconcile(reconciled_attempt),
            )
            await self._apply(
                claim.snapshot, receipt,
                token=_required(
                    claim.execution_token, "reconciliation token",
                ),
                state_version=lease.state_version,
                reconciliation=True,
            )
        except (
            FencingTokenMismatchError,
            LeaseExpiredError,
            StaleVersionError,
        ):
            pass
        return True

    async def _dispatch(self, snapshot: ActionDispatchSnapshot) -> None:
        attempt = snapshot.attempt
        token = str(attempt["execution_token"])
        request_hash = str(attempt["request_hash"])
        resolved = self._resolver.resolve(snapshot)
        try:
            gate = await self._authorization.gate(
                snapshot=snapshot, descriptor=resolved.descriptor,
            )
        except DispatchGateDenied:
            return
        lease: _ActionLease | None = None
        try:
            lease = _ActionLease(
                repository=self._actions,
                attempt_id=str(attempt["id"]), token=token,
                state_version=gate.state_version,
                lease_seconds=self._lease_seconds,
                renew_interval=self._renew_interval,
                reconciliation=False,
            )
            request = dict(resolved.request)
            request["external_idempotency_key"] = (
                gate.external_idempotency_key
            )
            request["_dispatch_context"] = {
                "dispatch_intent_id": gate.intent_id,
                "expected_action_version": _int(
                    snapshot.action, "state_version",
                ),
                "expected_attempt_version": gate.state_version,
            }
            dispatching_attempt = (
                replace(
                    resolved.attempt,
                    status=ActionAttemptStatus.DISPATCHING,
                )
                if isinstance(resolved.attempt, ActionAttempt)
                else resolved.attempt
            )
            dispatching_attempt = self._with_capabilities(
                dispatching_attempt, resolved.descriptor, "dispatch",
                dispatch_gate=gate,
            )
            receipt = await lease.run(
                resolved.executor.dispatch(dispatching_attempt, request),
            )
        except ExecutorDispatchUnknown as error:
            await self._actions.record_unknown(
                attempt_id=str(attempt["id"]), execution_token=token,
                expected_state_version=(
                    lease.state_version
                    if lease is not None else gate.state_version
                ),
                request_hash=request_hash,
                ambiguity_evidence=error.evidence,
            )
            return
        except DomainContractError:
            raise
        except Exception:
            await self._actions.record_unknown(
                attempt_id=str(attempt["id"]), execution_token=token,
                expected_state_version=(
                    lease.state_version
                    if lease is not None else gate.state_version
                ),
                request_hash=request_hash,
                ambiguity_evidence={"kind": "executor_dispatch_exception"},
            )
            return
        await self._apply(
            snapshot, receipt, token=token,
            state_version=(
                lease.state_version
                if lease is not None else gate.state_version
            ),
            reconciliation=False,
        )

    def _with_capabilities(
        self, attempt, descriptor, phase: str, *, dispatch_gate=None,
    ):
        if self._capability_issuer is None:
            return attempt
        if not isinstance(attempt, ActionAttempt):
            raise TypeError("ACTION_ATTEMPT_CAPABILITY_BINDING_REQUIRED")
        issued = self._capability_issuer.issue(
            attempt=attempt, descriptor=descriptor, phase=phase,
            dispatch_gate=dispatch_gate,
        )
        return replace(attempt, capabilities=dict(issued))

    async def _apply(
        self, snapshot: ActionDispatchSnapshot, receipt: ExecutionReceipt,
        *, token: str, state_version: int, reconciliation: bool,
    ) -> None:
        attempt_id = str(snapshot.attempt["id"])
        request_hash = str(snapshot.attempt["request_hash"])
        if receipt.request_hash != request_hash:
            raise RuntimeError("EXECUTOR_REQUEST_HASH_CONFLICT")
        if reconciliation:
            resolution = (
                "completed"
                if receipt.outcome is ExecutionOutcome.COMPLETED else
                "failed"
                if receipt.outcome is ExecutionOutcome.FAILED else
                "still_unknown"
            )
            await self._actions.resolve_reconciliation(
                attempt_id=attempt_id, reconciliation_token=token,
                expected_state_version=state_version,
                request_hash=request_hash, resolution=resolution,
                result=_result(receipt),
                ambiguity_evidence=(
                    receipt.ambiguity_evidence
                    or {"kind": f"reconcile_{receipt.outcome.value}"}
                ),
            )
            return
        if receipt.outcome is ExecutionOutcome.COMPLETED:
            await self._actions.complete(
                attempt_id=attempt_id, execution_token=token,
                expected_attempt_version=state_version,
                request_hash=request_hash,
                result=_required_result(receipt),
            )
        elif receipt.outcome is ExecutionOutcome.FAILED:
            await self._actions.fail(
                attempt_id=attempt_id, execution_token=token,
                expected_attempt_version=state_version,
                request_hash=request_hash,
                result=_failure_result(receipt),
            )
        elif receipt.outcome is ExecutionOutcome.ACCEPTED:
            await self._actions.mark_accepted(
                attempt_id=attempt_id, execution_token=token,
                expected_state_version=state_version,
                request_hash=request_hash,
                external_receipt=receipt.external_receipt,
            )
        else:
            await self._actions.record_unknown(
                attempt_id=attempt_id, execution_token=token,
                expected_state_version=state_version,
                request_hash=request_hash,
                ambiguity_evidence=(
                    receipt.ambiguity_evidence
                    or {"kind": receipt.outcome.value}
                ),
            )


def _required_result(receipt: ExecutionReceipt) -> Mapping[str, object]:
    if receipt.result is None:
        raise RuntimeError("ACTION_RESULT_REQUIRED")
    result = receipt.result
    return {
        "status": result.status.value,
        "summary": result.summary,
        "data": result.data,
        "artifact_ids": list(result.artifact_ids),
        "usage": dict(result.usage),
        "cost": dict(result.cost),
        "external_receipt": dict(result.receipt),
        "error_code": (
            str(result.data.get("error_code"))
            if result.data and result.data.get("error_code") else None
        ),
    }


def _result(receipt: ExecutionReceipt) -> Mapping[str, object] | None:
    if receipt.outcome is ExecutionOutcome.COMPLETED:
        return _required_result(receipt)
    if receipt.outcome is ExecutionOutcome.FAILED:
        return _failure_result(receipt)
    return None


def _failure_result(receipt: ExecutionReceipt) -> Mapping[str, object]:
    error_code = receipt.external_receipt.get("error_code", "executor_failed")
    return {
        "status": "error",
        "summary": str(receipt.external_receipt.get("summary", "")),
        "data": dict(receipt.external_receipt),
        "artifact_ids": [],
        "usage": {},
        "cost": {},
        "external_receipt": dict(receipt.external_receipt),
        "error_code": str(error_code),
    }


def _int(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int):
        raise RuntimeError(f"ACTION_{field.upper()}_REQUIRED")
    return item


def _required(value: str | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"{name.upper()}_REQUIRED")
    return value


def _required_int(value: int | None, name: str) -> int:
    if value is None:
        raise RuntimeError(f"{name.upper()}_REQUIRED")
    return value


class _ActionLease:
    def __init__(
        self, *, repository: ActionRepositoryPort, attempt_id: str,
        token: str, state_version: int, lease_seconds: int,
        renew_interval: float, reconciliation: bool,
    ) -> None:
        self._repository = repository
        self._attempt_id = attempt_id
        self._token = token
        self.state_version = state_version
        self._lease_seconds = lease_seconds
        self._renew_interval = renew_interval
        self._reconciliation = reconciliation
        self._lock = asyncio.Lock()

    async def run(self, awaitable: object) -> ExecutionReceipt:
        if not hasattr(awaitable, "__await__"):
            raise TypeError("ACTION_EXECUTOR_AWAITABLE_REQUIRED")
        work = asyncio.ensure_future(awaitable)
        renewal = asyncio.create_task(self._renew())
        try:
            done, _ = await asyncio.wait(
                {work, renewal}, return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal in done:
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                error = renewal.exception()
                if error is not None:
                    raise error
                raise RuntimeError("ACTION_LEASE_LOST")
            result = await work
            async with self._lock:
                pass
            return result
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(self._renew_interval)
            async with self._lock:
                if self._reconciliation:
                    receipt = await self._repository.renew_reconciliation(
                        attempt_id=self._attempt_id,
                        reconciliation_token=self._token,
                        expected_state_version=self.state_version,
                        lease_seconds=self._lease_seconds,
                    )
                else:
                    receipt = await self._repository.renew(
                        attempt_id=self._attempt_id,
                        execution_token=self._token,
                        expected_state_version=self.state_version,
                        lease_seconds=self._lease_seconds,
                    )
                self.state_version = _required_int(
                    receipt.state_version, "Action renewal state_version",
                )
