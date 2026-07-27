from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.agent.runtime.domain import (
    EventDurability,
    EventSequence,
    RuntimeActorType,
    RuntimeEvent,
    RuntimeScope,
    ScopeKind,
)
from services.agent.runtime.domain.errors import PersistenceContractError
from services.agent.runtime.projection import ProjectionAction, classify_event


def _event(event_type: str, *, version: int = 1) -> RuntimeEvent:
    return RuntimeEvent(
        event_id="event", session_id="session",
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id="user", user_id="user", org_id=None,
        ),
        event_type=event_type, event_version=version,
        durability=EventDurability.DURABLE, correlation_id="correlation",
        actor_type=RuntimeActorType.SYSTEM, payload_hash="hash",
        occurred_at=datetime.now(timezone.utc), redaction_revision="v1",
        sequence=EventSequence(1),
    )


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("command.accepted", ProjectionAction.USER_MESSAGE),
        ("run.created", ProjectionAction.RUN_PENDING),
        ("run.claimed", ProjectionAction.RUN_RUNNING),
        ("run.resumed", ProjectionAction.RUN_RUNNING),
        ("run.waiting", ProjectionAction.RUN_WAITING),
        ("run.completed", ProjectionAction.RUN_COMPLETED),
        ("run.failed", ProjectionAction.RUN_FAILED),
        ("run.cancelled", ProjectionAction.RUN_CANCELLED),
        ("action.completed", ProjectionAction.ACTION_PROGRESS),
        ("model_step.completed", ProjectionAction.CHECKPOINT_ONLY),
    ],
)
def test_event_classification_is_deterministic(
    event_type: str, expected: ProjectionAction,
) -> None:
    assert classify_event(_event(event_type)).action is expected


def test_unknown_type_and_version_fail_closed() -> None:
    with pytest.raises(PersistenceContractError, match="unsupported"):
        classify_event(_event("unknown.event"))
    with pytest.raises(PersistenceContractError, match="version"):
        classify_event(_event("run.created", version=2))
