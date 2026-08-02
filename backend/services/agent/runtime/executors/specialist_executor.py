"""Generic fenced provider executor used by AR-17.3 specialist families."""

from __future__ import annotations

import hashlib
from typing import Mapping, Protocol

from services.agent.runtime.domain import ActionAttempt, ActionResult, ActionResultStatus
from services.agent.runtime.executors.contracts import (
    ActionSnapshot, ResultPolicy, bounded_summary, canonical_json, safe_result,
)
from services.agent.runtime.executors.specialist_contracts import (
    CapabilityGrant, ProviderReceipt, ProviderState, SpecialistProvider,
    CostReservation, action_idempotency_key, receipt_facts, validate_public_request,
)
from services.agent.runtime.ports.executor import ExecutionOutcome, ExecutionReceipt


class SpecialistFactRepository(Protocol):
    async def cost(self, operation: str, item: CostReservation, **extra: object) -> object: ...
    async def provider_submission(self, **params: object) -> object: ...
    async def provider_unknown(self, **params: object) -> object: ...
    async def provider_terminal(self, **params: object) -> object: ...
    async def provider_reconcile(self, **params: object) -> object: ...


class SpecialistExecutor:
    """One descriptor-backed executor with no access to legacy ToolLoop code."""

    def __init__(
        self, *, executor_type: str, revision: int, provider: SpecialistProvider,
        capability: CapabilityGrant | None = None, policy: ResultPolicy | None = None,
        async_submit: bool = False,
        facts: SpecialistFactRepository | None = None,
    ) -> None:
        self.executor_type = executor_type
        self.revision = revision
        self.provider = provider
        self.capability = capability
        self.policy = policy or ResultPolicy()
        self.async_submit = async_submit
        self.facts = facts

    async def dispatch(self, attempt: ActionAttempt, request: Mapping[str, object]) -> ExecutionReceipt:
        snapshot = ActionSnapshot.from_attempt(
            attempt, request, executor_type=self.executor_type,
            executor_revision=self.revision,
        )
        if self.capability is not None:
            self.capability.assert_valid(attempt, self.executor_type, self.revision)
        try:
            validate_public_request(snapshot.request)
            await self._reserve(attempt, snapshot.request)
            receipt = await self.provider.submit(
                attempt, snapshot.request,
                idempotency_key=action_idempotency_key(attempt, self.executor_type),
            )
            if receipt.request_hash != snapshot.request_hash:
                return _failed(attempt, "SPECIALIST_PROVIDER_REQUEST_HASH_CONFLICT")
            await self._persist_provider_fact(attempt, receipt)
            await self._settle(attempt, receipt)
            return self._to_execution_receipt(attempt, receipt)
        except _DurableFactError as exc:
            return ExecutionReceipt(
                outcome=ExecutionOutcome.UNKNOWN,
                request_hash=attempt.request_hash,
                ambiguity_evidence={"error_code": str(exc)},
            )
        except PermissionError:
            return _failed(attempt, "SPECIALIST_CAPABILITY_DENIED")
        except Exception as exc:
            return ExecutionReceipt(
                outcome=ExecutionOutcome.UNKNOWN,
                request_hash=attempt.request_hash,
                ambiguity_evidence={"error_code": "SPECIALIST_SUBMIT_UNKNOWN", "type": type(exc).__name__},
            )

    async def reconcile(self, attempt: ActionAttempt) -> ExecutionReceipt:
        if attempt.status.value not in {"accepted", "unknown"}:
            raise RuntimeError("SPECIALIST_RECONCILE_STATUS_REQUIRED")
        try:
            receipt = await self.provider.reconcile(attempt, attempt.external_receipt)
            if self.facts is not None:
                if receipt.state is ProviderState.ACCEPTED and receipt.provider_task_ref:
                    await self.facts.provider_submission(
                        attempt_id=str(attempt.attempt_id), execution_token=_execution_token(attempt),
                        request_hash=attempt.request_hash, provider=receipt.provider,
                        provider_task_ref=receipt.provider_task_ref,
                        status_locator=receipt.status_locator,
                        callback_correlation=receipt.callback_correlation,
                        provider_idempotency_key=attempt.idempotency_key,
                        provider_request_hash=receipt.request_hash,
                        external_receipt=receipt_facts(receipt),
                    )
                else:
                    reconciliation_token = attempt.external_receipt.get("reconciliation_token")
                    if not isinstance(reconciliation_token, str) or not reconciliation_token:
                        raise _DurableFactError("SPECIALIST_RECONCILIATION_TOKEN_REQUIRED")
                    await self.facts.provider_reconcile(
                        attempt_id=str(attempt.attempt_id),
                        reconciliation_token=reconciliation_token,
                        request_hash=attempt.request_hash,
                        resolution=receipt.state.value,
                        result=dict(receipt.result),
                        ambiguity_evidence=dict(receipt.evidence),
                    )
            return self._to_execution_receipt(attempt, receipt)
        except Exception as exc:
            return ExecutionReceipt(
                outcome=ExecutionOutcome.UNKNOWN,
                request_hash=attempt.request_hash,
                ambiguity_evidence={"error_code": "SPECIALIST_RECONCILE_UNKNOWN", "type": type(exc).__name__},
            )

    async def cancel(self, attempt: ActionAttempt) -> ExecutionReceipt:
        if attempt.status.value not in {"claimed", "dispatching", "accepted", "unknown"}:
            raise RuntimeError("SPECIALIST_CANCEL_STATUS_INVALID")
        try:
            receipt = await self.provider.cancel(attempt, attempt.external_receipt)
            return self._to_execution_receipt(attempt, receipt)
        except Exception as exc:
            return ExecutionReceipt(
                outcome=ExecutionOutcome.UNKNOWN,
                request_hash=attempt.request_hash,
                ambiguity_evidence={"error_code": "SPECIALIST_CANCEL_UNKNOWN", "type": type(exc).__name__},
            )

    def _to_execution_receipt(self, attempt: ActionAttempt, receipt: ProviderReceipt) -> ExecutionReceipt:
        facts = receipt_facts(receipt)
        if receipt.state is ProviderState.COMPLETED:
            data = safe_result(receipt.result, self.policy)
            encoded = canonical_json(data).encode("utf-8")
            result = ActionResult(
                action_id=attempt.action_id, scope=attempt.scope,
                status=(ActionResultStatus.EMPTY if data.get("count") == 0 else ActionResultStatus.SUCCESS),
                result_hash=hashlib.sha256(encoded).hexdigest(),
                summary=bounded_summary(data, self.policy), data=data,
                cost=dict(receipt.cost), receipt=facts,
            )
            return ExecutionReceipt(ExecutionOutcome.COMPLETED, attempt.request_hash, facts, result=result)
        return ExecutionReceipt(
            outcome=ExecutionOutcome(receipt.state.value), request_hash=attempt.request_hash,
            external_receipt=facts, ambiguity_evidence=facts if receipt.state is ProviderState.UNKNOWN else {},
        )

    async def _reserve(self, attempt: ActionAttempt, request: Mapping[str, object]) -> None:
        if self.facts is None:
            return
        amount = request.get("reserved_credits", 0)
        if not isinstance(amount, int) or amount < 0:
            raise _DurableFactError("SPECIALIST_COST_RESERVE_INVALID")
        await self.facts.cost("reserve", CostReservation(
            action_id=str(attempt.action_id), attempt_id=str(attempt.attempt_id),
            kind="reserve", reserved_amount=amount,
            currency=str(request.get("currency", "credits")),
        ))

    async def _persist_provider_fact(self, attempt: ActionAttempt, receipt: ProviderReceipt) -> None:
        if self.facts is None:
            return
        params = {
            "attempt_id": str(attempt.attempt_id),
            "execution_token": _execution_token(attempt),
            "request_hash": attempt.request_hash,
        }
        if receipt.state is ProviderState.UNKNOWN:
            await self.facts.provider_unknown(
                **params, ambiguity_evidence=dict(receipt.evidence),
            )
        elif receipt.state is ProviderState.ACCEPTED:
            if not receipt.provider_task_ref:
                raise _DurableFactError("SPECIALIST_PROVIDER_REF_REQUIRED")
            await self.facts.provider_submission(
                **params, provider=receipt.provider,
                provider_task_ref=receipt.provider_task_ref,
                status_locator=receipt.status_locator,
                callback_correlation=receipt.callback_correlation,
                provider_idempotency_key=attempt.idempotency_key,
                provider_request_hash=receipt.request_hash,
                external_receipt=receipt_facts(receipt),
            )
        else:
            await self.facts.provider_terminal(
                **params, state=receipt.state.value,
                result=dict(receipt.result), ambiguity_evidence=dict(receipt.evidence),
            )

    async def _settle(self, attempt: ActionAttempt, receipt: ProviderReceipt) -> None:
        if self.facts is None:
            return
        if receipt.state is ProviderState.COMPLETED:
            amount = _actual_credits(receipt.cost)
            await self.facts.cost(
                "settle", CostReservation(
                    action_id=str(attempt.action_id), attempt_id=str(attempt.attempt_id),
                    kind="settle", reserved_amount=amount,
                ), actual_amount=amount,
                provider_receipt_hash=hashlib.sha256(canonical_json(receipt_facts(receipt)).encode()).hexdigest(),
            )
        elif receipt.state is ProviderState.FAILED:
            await self.facts.cost(
                "release", CostReservation(
                    action_id=str(attempt.action_id), attempt_id=str(attempt.attempt_id),
                    kind="release", reserved_amount=0,
                ), reason_code="provider_failed",
            )


class _DurableFactError(RuntimeError):
    pass


def _execution_token(attempt: ActionAttempt) -> str:
    return str(attempt.lease.fencing_token)


def _actual_credits(cost: Mapping[str, object]) -> int:
    value = cost.get("credits", cost.get("actual_credits", 0))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _DurableFactError("SPECIALIST_COST_SETTLEMENT_INVALID")
    return value


def _failed(attempt: ActionAttempt, error_code: str) -> ExecutionReceipt:
    return ExecutionReceipt(
        outcome=ExecutionOutcome.FAILED, request_hash=attempt.request_hash,
        external_receipt={"error_code": error_code},
    )
