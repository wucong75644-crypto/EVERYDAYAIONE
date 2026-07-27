"""Model Runtime SPI。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from services.agent.runtime.domain import ModelStepId, StopReason


@dataclass(frozen=True)
class ModelStepRequest:
    model_step_id: ModelStepId
    model_id: str
    request_hash: str
    input_receipt: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelStepResult:
    stop_reason: StopReason
    response_hash: str
    response_receipt: Mapping[str, object] = field(default_factory=dict)
    provider_stop_reason: str | None = None


class ModelPort(Protocol):
    """Provider adapter 必须实现的确定 ModelStep 边界。"""

    async def complete(self, request: ModelStepRequest) -> ModelStepResult:
        """执行一次逻辑 ModelStep；重试细节由 adapter receipt 描述。"""
