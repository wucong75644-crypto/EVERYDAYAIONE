"""The single adapter from persistent Action snapshots to Executor SPI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol

from services.agent.runtime.domain import (
    ActionAttempt,
    ActionAttemptId,
    ActionAttemptStatus,
    ActionId,
    FencingToken,
    IdempotencyKey,
    Lease,
    RuntimeScope,
    ScopeKind,
)
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.types import ExecutorDescriptor
from services.agent.runtime.ports.coordinator_recovery import (
    ActionDispatchSnapshot,
)
from services.agent.runtime.ports.executor import ExecutorPort


@dataclass(frozen=True, kw_only=True)
class ResolvedActionExecution:
    descriptor: ExecutorDescriptor
    executor: ExecutorPort
    attempt: ActionAttempt
    request: Mapping[str, object]


class ActionExecutorResolver(Protocol):
    def resolve(
        self, snapshot: ActionDispatchSnapshot,
    ) -> ResolvedActionExecution: ...


class PostgresActionExecutorResolver:
    """Registry remains the only action-kind to Executor mapping SSOT."""

    def __init__(self, registry: ExecutorRegistry) -> None:
        self._registry = registry
        self.specialist_facts = registry.specialist_facts

    def resolve(
        self, snapshot: ActionDispatchSnapshot,
    ) -> ResolvedActionExecution:
        action = snapshot.action
        attempt = snapshot.attempt
        descriptor, executor = self._registry.resolve(
            _text(action, "tool_name"),
        )
        scope = RuntimeScope(
            kind=ScopeKind(_text(action, "scope_kind")),
            scope_id=_text(action, "scope_id"),
            user_id=_optional_text(action.get("user_id")),
            org_id=_optional_text(action.get("org_id")),
        )
        return ResolvedActionExecution(
            descriptor=descriptor,
            executor=executor,
            attempt=ActionAttempt(
                attempt_id=ActionAttemptId(_text(attempt, "id")),
                action_id=ActionId(_text(attempt, "action_id")),
                scope=scope,
                attempt_number=_integer(attempt, "attempt_number"),
                status=ActionAttemptStatus(_text(attempt, "status")),
                worker_id=_text(attempt, "worker_id"),
                idempotency_key=IdempotencyKey(
                    _text(attempt, "idempotency_key"),
                ),
                request_hash=_text(attempt, "request_hash"),
                lease=Lease(
                    fencing_token=FencingToken(
                        _text(attempt, "execution_token"),
                    ),
                    expires_at=_datetime(attempt, "lease_expires_at"),
                ),
                started_at=_datetime(attempt, "claimed_at"),
                state_version=_nonnegative_integer(attempt, "state_version"),
                accepted_at=_optional_datetime(attempt.get("accepted_at")),
                ended_at=_optional_datetime(attempt.get("ended_at")),
                session_id=_text(action, "session_id"),
                run_id=_text(action, "run_id"),
                external_receipt=_mapping(
                    attempt.get("external_receipt", {}),
                    "external_receipt",
                ),
                ambiguity_evidence=_mapping(
                    attempt.get("ambiguity_evidence", {}),
                    "ambiguity_evidence",
                ),
            ),
            request=_mapping(action.get("arguments"), "arguments"),
        )


def _text(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} must be nonblank text")
    return item


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional identity must be nonblank text")
    return value


def _integer(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{field} must be a positive integer")
    return item


def _nonnegative_integer(value: Mapping[str, object], field: str) -> int:
    item = value.get(field, 0)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return item


def _datetime(value: Mapping[str, object], field: str) -> datetime:
    item = _optional_datetime(value.get(field))
    if item is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return item


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value
