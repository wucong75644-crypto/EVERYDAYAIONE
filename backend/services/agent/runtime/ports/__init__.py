"""Agent Runtime 应用层稳定 SPI。"""

from services.agent.runtime.ports.event import RuntimeEventPort
from services.agent.runtime.ports.executor import (
    ExecutionOutcome,
    ExecutionReceipt,
    ExecutorPort,
)
from services.agent.runtime.ports.model import (
    ModelPort,
    ModelStepRequest,
    ModelStepResult,
)
from services.agent.runtime.ports.projection import (
    ProjectionClaim,
    ProjectionOutcome,
    ProjectionOutboxPort,
    ProjectionPort,
    ProjectionReceipt,
)
from services.agent.runtime.ports.repository import (
    ClaimOutcome,
    MutationOutcome,
    MutationReceipt,
    RunClaim,
    RuntimeRepositoryPort,
    SessionSnapshot,
)

__all__ = [
    "ExecutionOutcome",
    "ExecutionReceipt",
    "ExecutorPort",
    "ModelPort",
    "ModelStepRequest",
    "ModelStepResult",
    "ProjectionOutcome",
    "ProjectionClaim",
    "ProjectionOutboxPort",
    "ProjectionPort",
    "ProjectionReceipt",
    "RuntimeEventPort",
    "RuntimeRepositoryPort",
    "ClaimOutcome",
    "MutationOutcome",
    "MutationReceipt",
    "RunClaim",
    "SessionSnapshot",
]
