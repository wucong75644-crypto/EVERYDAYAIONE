"""Generic fenced provider executor used by AR-17.3 specialist families."""

from __future__ import annotations

import hashlib
from typing import Mapping

from services.agent.runtime.domain import ActionAttempt, ActionResult, ActionResultStatus
from services.agent.runtime.executors.contracts import (
    ActionSnapshot, ResultPolicy, bounded_summary, canonical_json, safe_result,
)
from services.agent.runtime.executors.specialist_contracts import (
    CapabilityGrant, ProviderReceipt, ProviderState, SpecialistProvider,
    ReconciliationContext, action_idempotency_key, receipt_facts, validate_public_request,
)
from services.agent.runtime.ports.executor import ExecutionOutcome, ExecutionReceipt


class SpecialistExecutor:
    """One descriptor-backed executor with no access to legacy ToolLoop code."""

    def __init__(
        self, *, executor_type: str, revision: int, provider: SpecialistProvider,
        capability: CapabilityGrant | None = None, policy: ResultPolicy | None = None,
        async_submit: bool = False,
        facts: object | None = None,
    ) -> None:
        self.executor_type = executor_type
        self.revision = revision
        self.provider = provider
        self.capability = capability
        self.policy = policy or ResultPolicy()
        self.async_submit = async_submit
        # Kept as an ignored compatibility argument while composition migrates.

    async def dispatch(self, attempt: ActionAttempt, request: Mapping[str, object]) -> ExecutionReceipt:
        snapshot = ActionSnapshot.from_attempt(
            attempt, request, executor_type=self.executor_type,
            executor_revision=self.revision,
        )
        if self.capability is not None:
            self.capability.assert_valid(attempt, self.executor_type, self.revision)
        try:
            validate_public_request(snapshot.request)
            receipt = await self.provider.submit(
                attempt, snapshot.request,
                idempotency_key=action_idempotency_key(attempt, self.executor_type),
            )
            if receipt.request_hash != snapshot.request_hash:
                return ExecutionReceipt(
                    outcome=ExecutionOutcome.UNKNOWN,
                    request_hash=attempt.request_hash,
                    ambiguity_evidence={"error_code": "SPECIALIST_PROVIDER_REQUEST_HASH_CONFLICT", "provider": receipt.provider},
                )
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

    async def reconcile(self, attempt: ActionAttempt, context: ReconciliationContext | None = None) -> ExecutionReceipt:
        if attempt.status.value not in {"accepted", "unknown"}:
            raise RuntimeError("SPECIALIST_RECONCILE_STATUS_REQUIRED")
        if context is None:
            return ExecutionReceipt(
                outcome=ExecutionOutcome.UNKNOWN, request_hash=attempt.request_hash,
                ambiguity_evidence={"error_code": "SPECIALIST_RECONCILIATION_CONTEXT_REQUIRED"},
            )
        try:
            receipt = await self.provider.reconcile(attempt, {
                **dict(attempt.external_receipt),
                "reconciliation_token": context.token,
                "reconciliation_state_version": context.state_version,
            })
            return self._to_execution_receipt(attempt, receipt)
        except Exception as exc:
            return ExecutionReceipt(
                outcome=ExecutionOutcome.UNKNOWN,
                request_hash=attempt.request_hash,
                ambiguity_evidence={"error_code": "SPECIALIST_RECONCILE_UNKNOWN", "type": type(exc).__name__},
            )

    async def cancel(self, attempt: ActionAttempt, context: ReconciliationContext | None = None) -> ExecutionReceipt:
        if attempt.status.value not in {"claimed", "dispatching", "accepted", "unknown"}:
            raise RuntimeError("SPECIALIST_CANCEL_STATUS_INVALID")
        try:
            if attempt.status.value in {"accepted", "unknown"} and context is None:
                raise _DurableFactError("SPECIALIST_RECONCILIATION_CONTEXT_REQUIRED")
            provider_receipt = dict(attempt.external_receipt)
            if context is not None:
                provider_receipt.update({"reconciliation_token": context.token, "reconciliation_state_version": context.state_version})
            receipt = await self.provider.cancel(attempt, provider_receipt)
            if receipt.state is ProviderState.CANCELLED and receipt.evidence.get("cancel_confirmed") is not True:
                return ExecutionReceipt(
                    outcome=ExecutionOutcome.UNKNOWN,
                    request_hash=attempt.request_hash,
                    ambiguity_evidence={"error_code": "SPECIALIST_CANCEL_UNPROVEN", **dict(receipt.evidence)},
                )
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

class _DurableFactError(RuntimeError):
    pass


def _failed(attempt: ActionAttempt, error_code: str) -> ExecutionReceipt:
    return ExecutionReceipt(
        outcome=ExecutionOutcome.FAILED, request_hash=attempt.request_hash,
        external_receipt={"error_code": error_code},
    )
