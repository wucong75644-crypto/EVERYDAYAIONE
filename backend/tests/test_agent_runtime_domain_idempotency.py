"""AR-05 稳定身份、幂等键与事件 sequence 测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.agent.runtime.domain.errors import IdempotencyConflictError
from services.agent.runtime.domain.events import (
    EventDurability,
    EventSequence,
    RuntimeActorType,
    RuntimeEvent,
    RuntimeEventDraft,
)
from services.agent.runtime.domain.execution import (
    IdempotencyOutcome,
    IdempotencyRecord,
)
from services.agent.runtime.domain.identity import (
    IdempotencyKey,
    RuntimeEventId,
    SessionId,
)
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind


SCOPE = RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", "o-1")


def test_duplicate_idempotency_key_returns_existing_identity() -> None:
    record = IdempotencyRecord(
        IdempotencyKey("command:1"),
        SCOPE,
        "hash-1",
        "run-1",
    )
    assert record.resolve(
        scope=SCOPE,
        request_hash="hash-1",
    ) is IdempotencyOutcome.EXISTING
    assert record.entity_id == "run-1"


@pytest.mark.parametrize(
    ("scope", "request_hash"),
    (
        (SCOPE, "different"),
        (RuntimeScope(ScopeKind.USER, "user:u-2", "u-2", "o-1"), "hash-1"),
    ),
)
def test_idempotency_key_collision_is_rejected(
    scope: RuntimeScope,
    request_hash: str,
) -> None:
    record = IdempotencyRecord(
        IdempotencyKey("command:1"),
        SCOPE,
        "hash-1",
        "run-1",
    )
    with pytest.raises(IdempotencyConflictError):
        record.resolve(scope=scope, request_hash=request_hash)


def test_event_sequence_is_positive_and_ordered() -> None:
    assert EventSequence(1) < EventSequence(2)
    with pytest.raises(ValueError, match="positive"):
        EventSequence(0)


def test_runtime_event_keeps_scope_and_sequence() -> None:
    event = RuntimeEvent(
        event_id=RuntimeEventId("event-1"),
        session_id=SessionId("session-1"),
        sequence=EventSequence(1),
        scope=SCOPE,
        event_type="session.created",
        event_version=1,
        durability=EventDurability.DURABLE,
        correlation_id="correlation-1",
        actor_type=RuntimeActorType.SYSTEM,
        payload_hash="payload-hash",
        occurred_at=datetime(2026, 7, 27, tzinfo=UTC),
        redaction_revision="v1",
    )
    assert event.sequence == EventSequence(1)
    assert event.scope == SCOPE


def test_event_draft_has_no_caller_supplied_sequence() -> None:
    draft = RuntimeEventDraft(
        session_id=SessionId("session-1"),
        scope=SCOPE,
        event_type="run.created",
        event_version=1,
        durability=EventDurability.DURABLE,
        correlation_id="correlation-1",
        actor_type=RuntimeActorType.SYSTEM,
        payload_hash="payload-hash",
        occurred_at=datetime(2026, 7, 27, tzinfo=UTC),
        redaction_revision="v1",
    )
    assert not hasattr(draft, "sequence")
