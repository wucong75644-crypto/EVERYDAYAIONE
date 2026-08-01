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
    action_idempotency_key, receipt_facts, validate_public_request,
)
from services.agent.runtime.ports.executor import ExecutionOutcome, ExecutionReceipt


class SpecialistExecutor:
    """One descriptor-backed executor with no access to legacy ToolLoop code."""

    def __init__(
        self, *, executor_type: str, revision: int, provider: SpecialistProvider,
        capability: CapabilityGrant | None = None, policy: ResultPolicy | None = None,
        async_submit: bool = False,
    ) -> None:
        self.executor_type = executor_type
        self.revision = revision
        self.provider = provider
        self.capability = capability
        self.policy = policy or ResultPolicy()
        self.async_submit = async_submit

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
                return _failed(attempt, "SPECIALIST_PROVIDER_REQUEST_HASH_CONFLICT")
            return self._to_execution_receipt(attempt, receipt)
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


def _failed(attempt: ActionAttempt, error_code: str) -> ExecutionReceipt:
    return ExecutionReceipt(
        outcome=ExecutionOutcome.FAILED, request_hash=attempt.request_hash,
        external_receipt={"error_code": error_code},
    )
