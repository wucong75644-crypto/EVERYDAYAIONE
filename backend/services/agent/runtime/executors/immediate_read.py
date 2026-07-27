"""Representative professional adapter for bounded read-only operations."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from services.agent.runtime.domain import (
    ActionAttempt,
    ActionResult,
    ActionResultStatus,
)
from services.agent.runtime.executors.capabilities import (
    RestrictedDatabaseCapability,
)
from services.agent.runtime.ports.executor import (
    ExecutionOutcome,
    ExecutionReceipt,
)


class ImmediateReadExecutor:
    """Execute one declared read operation through a restricted capability."""

    def __init__(
        self, capability: RestrictedDatabaseCapability,
        operation: str,
    ) -> None:
        if not operation.strip():
            raise ValueError("operation is required")
        self._capability = capability
        self._operation = operation

    async def dispatch(
        self, attempt: ActionAttempt, request: Mapping[str, object],
    ) -> ExecutionReceipt:
        data = await self._capability.read(
            str(attempt.action_id),
            str(attempt.attempt_id),
            self._operation,
            request,
        )
        canonical = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        result = ActionResult(
            action_id=attempt.action_id,
            scope=attempt.scope,
            status=ActionResultStatus.SUCCESS,
            result_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            summary=str(data.get("summary") or ""),
            data=data,
        )
        return ExecutionReceipt(
            outcome=ExecutionOutcome.COMPLETED,
            request_hash=attempt.request_hash,
            result=result,
        )

    async def reconcile(self, attempt: ActionAttempt) -> ExecutionReceipt:
        raise RuntimeError("IMMEDIATE_READ_RECONCILIATION_UNSUPPORTED")

    async def cancel(self, attempt: ActionAttempt) -> ExecutionReceipt:
        raise RuntimeError("IMMEDIATE_READ_CANCELLATION_UNSUPPORTED")
