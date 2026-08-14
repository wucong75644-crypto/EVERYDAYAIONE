"""Capability-only read Executors used by AR-17.2 and later specialists."""

from __future__ import annotations

import hashlib
from typing import Awaitable, Callable, Mapping, Protocol

from services.agent.runtime.domain import (
    ActionAttempt, ActionResult, ActionResultStatus,
)
from services.agent.runtime.executors.contracts import (
    ActionSnapshot, ResultPolicy, bounded_summary, canonical_json,
    safe_result,
)
from services.agent.runtime.ports.executor import (
    ExecutionOutcome, ExecutionReceipt,
)


class ReadCapability(Protocol):
    async def read(
        self, snapshot: ActionSnapshot, request: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def bind(self, snapshot: ActionSnapshot) -> "ReadCapability": ...


ReadOperation = Callable[
    [ActionSnapshot, Mapping[str, object]],
    Awaitable[Mapping[str, object]],
]


class CallableReadCapability:
    """Adapter for a narrowly scoped DB/Workspace/Artifact read port."""

    def __init__(self, operation: ReadOperation) -> None:
        self._operation = operation

    async def read(
        self, snapshot: ActionSnapshot, request: Mapping[str, object],
    ) -> Mapping[str, object]:
        value = await self._operation(snapshot, request)
        if not isinstance(value, Mapping):
            raise ValueError("EXECUTOR_CAPABILITY_OBJECT_REQUIRED")
        return value


class ScopedReadCapability(CallableReadCapability):
    """Read port that rejects scopes it was not issued for."""

    def __init__(
        self, operation: ReadOperation, *, allowed_scope_kinds: frozenset[str],
    ) -> None:
        super().__init__(operation)
        if not allowed_scope_kinds:
            raise ValueError("READ_SCOPE_ALLOWLIST_REQUIRED")
        self._allowed_scope_kinds = allowed_scope_kinds

    async def read(
        self, snapshot: ActionSnapshot, request: Mapping[str, object],
    ) -> Mapping[str, object]:
        if snapshot.scope.kind.value not in self._allowed_scope_kinds:
            raise PermissionError("READ_SCOPE_NOT_ALLOWED")
        if snapshot.scope.kind.value == "user" and not snapshot.scope.user_id:
            raise PermissionError("READ_USER_SCOPE_REQUIRED")
        if snapshot.scope.kind.value == "channel" and not snapshot.scope.org_id:
            raise PermissionError("READ_ORG_SCOPE_REQUIRED")
        return await super().read(snapshot, request)


class ReadOnlyExecutor:
    """One real action kind backed only by its injected minimum capability."""

    def __init__(
        self, *, executor_type: str, executor_revision: int,
        capability: ReadCapability, policy: ResultPolicy | None = None,
        allowed_scope_kinds: frozenset[str] = frozenset({"user", "channel"}),
    ) -> None:
        if not allowed_scope_kinds:
            raise ValueError("READ_SCOPE_ALLOWLIST_REQUIRED")
        self._executor_type = executor_type
        self._executor_revision = executor_revision
        self._capability = capability
        self._policy = policy or ResultPolicy()
        self._allowed_scope_kinds = allowed_scope_kinds

    async def dispatch(
        self, attempt: ActionAttempt, request: Mapping[str, object],
    ) -> ExecutionReceipt:
        snapshot = ActionSnapshot.from_attempt(
            attempt, request, executor_type=self._executor_type,
            executor_revision=self._executor_revision,
        )
        if snapshot.scope.kind.value not in self._allowed_scope_kinds:
            return _failed(attempt, "READ_SCOPE_NOT_ALLOWED")
        if snapshot.scope.kind.value == "user" and not snapshot.scope.user_id:
            return _failed(attempt, "READ_USER_SCOPE_REQUIRED")
        if snapshot.scope.kind.value == "channel" and not snapshot.scope.org_id:
            return _failed(attempt, "READ_ORG_SCOPE_REQUIRED")
        try:
            binder = getattr(self._capability, "bind", None)
            capability = binder(snapshot) if callable(binder) else self._capability
            raw = await capability.read(snapshot, snapshot.request)
            data = safe_result(raw, self._policy)
            canonical = canonical_json(data)
            summary = bounded_summary(data, self._policy)
        except PermissionError:
            return _failed(attempt, "READ_PERMISSION_DENIED")
        except ValueError:
            return _failed(attempt, "READ_RESULT_INVALID")
        except Exception:
            return _failed(attempt, "READ_CAPABILITY_FAILED")
        return ExecutionReceipt(
            outcome=ExecutionOutcome.COMPLETED,
            request_hash=snapshot.request_hash,
            result=ActionResult(
                action_id=attempt.action_id, scope=attempt.scope,
                status=(ActionResultStatus.EMPTY
                        if data.get("count") == 0 else ActionResultStatus.SUCCESS),
                result_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                summary=summary, data=data,
            ),
        )

    async def reconcile(self, attempt: ActionAttempt) -> ExecutionReceipt:
        raise RuntimeError("IMMEDIATE_READ_RECONCILIATION_UNSUPPORTED")

    async def cancel(self, attempt: ActionAttempt) -> ExecutionReceipt:
        raise RuntimeError("IMMEDIATE_READ_CANCELLATION_UNSUPPORTED")


def _failed(attempt: ActionAttempt, error_code: str) -> ExecutionReceipt:
    return ExecutionReceipt(
        outcome=ExecutionOutcome.FAILED,
        request_hash=attempt.request_hash,
        external_receipt={"error_code": error_code, "summary": ""},
    )
