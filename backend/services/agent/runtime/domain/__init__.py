"""Agent Runtime 无框架依赖的领域协议。"""

from services.agent.runtime.domain.action import (
    ActionAttempt,
    ActionAttemptStatus,
    ActionResult,
    ActionResultStatus,
    ActionStatus,
    RetryDisposition,
    require_action_result,
    require_retry_safe,
)
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
    Lease,
)
from services.agent.runtime.domain.identity import (
    ActionAttemptId,
    ActionId,
    FencingToken,
    IdempotencyKey,
    ModelStepId,
    RunId,
    RuntimeEventId,
    SessionId,
)
from services.agent.runtime.domain.model_step import ModelStepStatus, StopReason
from services.agent.runtime.domain.model_attempt import (
    ModelAttemptStatus,
    ModelDispatchPhase,
    ModelLateOutcome,
    ModelRetryDisposition,
    allowed_model_attempt_transitions,
    validate_model_attempt_transition,
)
from services.agent.runtime.domain.run import (
    RunAttempt,
    RunAttemptOutcome,
    RunStatus,
)
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind
from services.agent.runtime.domain.session import (
    SessionCommand,
    SessionCommandType,
    SessionStatus,
)
from services.agent.runtime.domain.sandbox_job import (
    SandboxCleanupStatus,
    SandboxJobSnapshot,
    SandboxJobStatus,
    SandboxMaterializationStatus,
)
from services.agent.runtime.domain.transitions import (
    allowed_transitions,
    validate_transition,
)

__all__ = [
    "ActionAttempt",
    "ActionAttemptId",
    "ActionAttemptStatus",
    "ActionId",
    "ActionResult",
    "ActionResultStatus",
    "ActionStatus",
    "EventDurability",
    "EventSequence",
    "FencingToken",
    "IdempotencyKey",
    "IdempotencyOutcome",
    "IdempotencyRecord",
    "Lease",
    "ModelStepId",
    "ModelStepStatus",
    "ModelAttemptStatus",
    "ModelDispatchPhase",
    "ModelLateOutcome",
    "ModelRetryDisposition",
    "RetryDisposition",
    "RunAttempt",
    "RunAttemptOutcome",
    "RunId",
    "RunStatus",
    "SandboxCleanupStatus",
    "SandboxJobSnapshot",
    "SandboxJobStatus",
    "SandboxMaterializationStatus",
    "RuntimeActorType",
    "RuntimeEvent",
    "RuntimeEventDraft",
    "RuntimeEventId",
    "RuntimeScope",
    "ScopeKind",
    "SessionCommand",
    "SessionCommandType",
    "SessionId",
    "SessionStatus",
    "StopReason",
    "allowed_transitions",
    "allowed_model_attempt_transitions",
    "require_retry_safe",
    "require_action_result",
    "validate_transition",
    "validate_model_attempt_transition",
]
