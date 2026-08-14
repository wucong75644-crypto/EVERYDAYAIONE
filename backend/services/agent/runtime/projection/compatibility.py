"""Pure RuntimeEvent to legacy-compatibility projection classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.agent.runtime.domain import RuntimeEvent
from services.agent.runtime.domain.errors import PersistenceContractError


class ProjectionAction(StrEnum):
    CHECKPOINT_ONLY = "checkpoint_only"
    USER_MESSAGE = "user_message"
    RUN_PENDING = "run_pending"
    RUN_RUNNING = "run_running"
    RUN_WAITING = "run_waiting"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    ACTION_PROGRESS = "action_progress"


@dataclass(frozen=True)
class CompatibilityProjection:
    action: ProjectionAction
    terminal: bool = False


_CHECKPOINT_ONLY = {
    "session.created",
    "command.attempts_exhausted",
    "model_step.created",
    "model_step.completed",
    "model_step.failed",
}
_ACTION_PROGRESS = {
    "action.requested",
    "action.accepted",
    "action.retry_scheduled",
    "action.unknown",
    "action.completed",
    "action.failed",
    "action.rejected",
    "action.cancelled",
}
_RUN_ACTIONS = {
    "run.created": ProjectionAction.RUN_PENDING,
    "run.claimed": ProjectionAction.RUN_RUNNING,
    "run.resumed": ProjectionAction.RUN_RUNNING,
    "run.waiting": ProjectionAction.RUN_WAITING,
    "run.completed": ProjectionAction.RUN_COMPLETED,
    "run.failed": ProjectionAction.RUN_FAILED,
    "run.cancelled": ProjectionAction.RUN_CANCELLED,
}


def classify_event(event: RuntimeEvent) -> CompatibilityProjection:
    """Classify every event emitted by migrations 212-219."""
    if event.event_version != 1:
        raise PersistenceContractError(
            f"unsupported projection event version: {event.event_version}",
        )
    if event.event_type == "command.accepted":
        return CompatibilityProjection(ProjectionAction.USER_MESSAGE)
    if event.event_type in _CHECKPOINT_ONLY:
        return CompatibilityProjection(ProjectionAction.CHECKPOINT_ONLY)
    if event.event_type in _ACTION_PROGRESS:
        return CompatibilityProjection(ProjectionAction.ACTION_PROGRESS)
    action = _RUN_ACTIONS.get(event.event_type)
    if action is None:
        raise PersistenceContractError(
            f"unsupported projection event type: {event.event_type}",
        )
    return CompatibilityProjection(
        action,
        terminal=action in {
            ProjectionAction.RUN_COMPLETED,
            ProjectionAction.RUN_FAILED,
            ProjectionAction.RUN_CANCELLED,
        },
    )
