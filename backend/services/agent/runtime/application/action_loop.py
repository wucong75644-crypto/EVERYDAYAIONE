"""Injected Action dispatch and reconciliation loops for AR-14."""

from __future__ import annotations

import asyncio
import hashlib
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
    ActionRecoveryOperation,
    CoordinatorRecoveryPort,
    RecoveryOutcome,
)
from services.agent.runtime.ports.executor import (
    DispatchCapabilityIssuerPort,
    ExecutionOutcome,
    ExecutionReceipt,
    ExecutorDispatchUnknown,
)
from services.agent.runtime.executors.specialist_contracts import ReconciliationContext, CostReservation
from services.agent.runtime.executors.specialist_executor import SpecialistExecutor
from services.agent.runtime.executors.contracts import canonical_json
from services.agent.runtime.application.action_loop_support import (
    ActionLease as _ActionLease,
    CostReserveFailure as _CostReserveFailure,
    failure_result as _failure_result,
    int_value as _int,
    required as _required,
    required_int as _required_int,
    required_result as _required_result,
    required_time as _required_time,
    reserved_amount as _reserved_amount,
    result as _result,
)


class ActionLoopDriver:
    def __init__(
        self, *, recovery_repository: CoordinatorRecoveryPort,
        action_repository: ActionRepositoryPort,
        authorization_repository: ActionAuthorizationPort,
        resolver: ActionExecutorResolver, worker_id: str,
        lease_seconds: int = 120, renew_interval: float = 40.0,
        capability_issuer: DispatchCapabilityIssuerPort | None = None,
        specialist_facts: object | None = None,
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
        self._specialist_facts = specialist_facts or getattr(
            resolver, "specialist_facts", None,
        )

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
            if claim.operation is ActionRecoveryOperation.CANCEL:
                await self.cancel_action(claim.snapshot, lease=lease)
                return True
            if claim.operation is not ActionRecoveryOperation.RECONCILE:
                raise RuntimeError("ACTION_RECOVERY_OPERATION_REQUIRED")
            resolved = self._resolver.resolve(claim.snapshot)
            reconciled_attempt = self._with_capabilities(
                resolved.attempt, resolved.descriptor, "reconcile",
            )
            context = ReconciliationContext(
                token=_required(claim.execution_token, "reconciliation token"),
                lease_expires_at=_required_time(claim.lease_expires_at),
                state_version=lease.state_version,
            )
            reconcile_call = (
                resolved.executor.reconcile(reconciled_attempt, context)
                if isinstance(resolved.executor, SpecialistExecutor)
                else resolved.executor.reconcile(reconciled_attempt)
            )
            receipt = await lease.run(reconcile_call)
            await self._apply(
                claim.snapshot, receipt,
                token=_required(
                    claim.execution_token, "reconciliation token",
                ),
                state_version=lease.state_version,
                reconciliation=True,
                reserved_amount=_reserved_amount(claim.snapshot),
                specialist=isinstance(resolved.executor, SpecialistExecutor),
            )
        except (
            FencingTokenMismatchError,
            LeaseExpiredError,
            StaleVersionError,
        ):
            pass
        return True
    async def cancel_action(self, snapshot: ActionDispatchSnapshot, *,
                            lease: _ActionLease | None = None) -> ExecutionOutcome:
        from services.agent.runtime.application.action_cancel import (
            cancel_action,
        )
        return await cancel_action(self, snapshot, lease=lease)

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
            await self._reserve_specialist(dispatching_attempt, request, resolved.executor)
            receipt = await lease.run(
                resolved.executor.dispatch(dispatching_attempt, request),
            )
        except _CostReserveFailure:
            await self._actions.fail_before_dispatch(
                attempt_id=str(attempt["id"]), execution_token=token,
                expected_attempt_version=(lease.state_version if lease is not None else gate.state_version),
                request_hash=request_hash, error_code="SPECIALIST_COST_RESERVE_FAILED",
            )
            return
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
            reserved_amount=_reserved_amount(snapshot),
            specialist=isinstance(resolved.executor, SpecialistExecutor),
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
        reserved_amount: int, specialist: bool,
    ) -> None:
        attempt_id = str(snapshot.attempt["id"])
        request_hash = str(snapshot.attempt["request_hash"])
        if receipt.request_hash != request_hash:
            raise RuntimeError("EXECUTOR_REQUEST_HASH_CONFLICT")
        if reconciliation:
            if await self._try_specialist_finalize(
                receipt, attempt_id=attempt_id, token=token,
                state_version=state_version, request_hash=request_hash,
                reconciliation=True,
                reserved_amount=reserved_amount,
                specialist=specialist,
            ):
                return
            if await self._persist_specialist_nonterminal(
                receipt, attempt_id=attempt_id, token=token,
                state_version=state_version, request_hash=request_hash, reconciliation=True,
                specialist=specialist,
            ):
                return
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
        if await self._try_specialist_finalize(
            receipt, attempt_id=attempt_id, token=token,
            state_version=state_version, request_hash=request_hash,
            reconciliation=False,
            reserved_amount=reserved_amount,
            specialist=specialist,
        ):
            return
        if await self._persist_specialist_nonterminal(
            receipt, attempt_id=attempt_id, token=token,
            state_version=state_version, request_hash=request_hash, reconciliation=False,
            specialist=specialist,
        ):
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

    async def _try_specialist_finalize(
        self, receipt: ExecutionReceipt, *, attempt_id: str, token: str,
        state_version: int, request_hash: str, reconciliation: bool,
        reserved_amount: int, specialist: bool,
    ) -> bool:
        """The application is the sole specialist terminal owner."""
        if not specialist or self._specialist_facts is None or receipt.outcome not in {
            ExecutionOutcome.COMPLETED, ExecutionOutcome.FAILED,
            ExecutionOutcome.CANCELLED,
        }:
            return False
        if not hasattr(self._specialist_facts, "finalize"):
            raise RuntimeError("SPECIALIST_FINALIZE_REPOSITORY_REQUIRED")
        external = dict(receipt.external_receipt)
        cost = dict(receipt.result.cost) if receipt.result is not None else {}
        amount = cost.get("credits", cost.get("actual_credits", 0))
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            amount = 0
        result = _result(receipt)
        provider_receipt_hash = hashlib.sha256(
            canonical_json(external).encode("utf-8")
        ).hexdigest()
        await self._specialist_facts.finalize(
            attempt_id=attempt_id,
            execution_token=None if reconciliation else token,
            reconciliation_token=token if reconciliation else None,
            expected_state_version=state_version,
            request_hash=request_hash,
            terminal_state=receipt.outcome.value,
            provider_receipt=external,
            result=result or {"status": "empty", "summary": "cancelled", "data": {}, "artifact_ids": [], "usage": {}, "cost": {}, "external_receipt": external},
            cost_kind=("settle" if receipt.outcome is ExecutionOutcome.COMPLETED
                       else "release" if receipt.outcome is ExecutionOutcome.FAILED
                       else "refund"),
            reserved_amount=reserved_amount, actual_amount=amount, currency="credits",
            reason_code="runtime", provider_receipt_hash=provider_receipt_hash,
        )
        return True

    async def _reserve_specialist(self, attempt, request, executor) -> None:
        if self._specialist_facts is None or not isinstance(executor, SpecialistExecutor):
            return
        amount = request.get("reserved_credits", 0)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise RuntimeError("SPECIALIST_COST_RESERVE_INVALID")
        try:
            await self._specialist_facts.cost(
                "reserve", CostReservation(
                    action_id=str(attempt.action_id), attempt_id=str(attempt.attempt_id),
                    kind="reserve", reserved_amount=amount,
                    currency=str(request.get("currency", "credits")),
                ),
            )
        except Exception as exc:
            raise _CostReserveFailure from exc

    async def _persist_specialist_nonterminal(
        self, receipt: ExecutionReceipt, *, attempt_id: str, token: str,
        state_version: int, request_hash: str, reconciliation: bool, specialist: bool,
    ) -> bool:
        if not specialist or self._specialist_facts is None or not hasattr(self._specialist_facts, "still_accepted"):
            return False
        if receipt.outcome not in {ExecutionOutcome.ACCEPTED, ExecutionOutcome.UNKNOWN}:
            return False
        if reconciliation:
            method = ("still_accepted" if receipt.outcome is ExecutionOutcome.ACCEPTED
                      else "still_unknown")
            await getattr(self._specialist_facts, method)(
                attempt_id=attempt_id, reconciliation_token=token,
                expected_state_version=state_version, request_hash=request_hash,
                provider_receipt=dict(receipt.external_receipt),
                ambiguity_evidence=receipt.ambiguity_evidence,
            )
            return True
        external = dict(receipt.external_receipt)
        if receipt.outcome is ExecutionOutcome.UNKNOWN:
            await self._specialist_facts.provider_unknown(
                attempt_id=attempt_id, execution_token=token,
                request_hash=request_hash,
                ambiguity_evidence=receipt.ambiguity_evidence,
            )
            return True
        provider_ref = external.get("provider_task_ref")
        provider = external.get("provider")
        if not isinstance(provider_ref, str) or not isinstance(provider, str):
            raise RuntimeError("SPECIALIST_PROVIDER_IDENTITY_REQUIRED")
        await self._specialist_facts.provider_submission(
            attempt_id=attempt_id, execution_token=token, request_hash=request_hash,
            provider=provider, provider_task_ref=provider_ref,
            status_locator=external.get("status_locator"),
            callback_correlation=external.get("callback_correlation"),
            provider_idempotency_key=external.get("provider_idempotency_key", attempt_id),
            provider_request_hash=str(external.get("request_hash", request_hash)),
            external_receipt=external,
        )
        return True
