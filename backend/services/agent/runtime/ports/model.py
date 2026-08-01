"""Model Runtime SPI。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.context import ProviderContextPlan
from services.agent.runtime.domain import ModelStepId, StopReason
from services.agent.runtime.domain.identity import require_stable_value


class ModelOutputKind(StrEnum):
    TEXT = "text"
    STRUCTURED = "structured"


class ProviderAttemptOutcome(StrEnum):
    COMPLETED = "completed"
    RETRYING = "retrying"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class ModelInputReceipt:
    receipt_id: str
    receipt_hash: str
    context_plan_hash: str

    def __post_init__(self) -> None:
        require_stable_value(self.receipt_id, "receipt_id")
        require_stable_value(self.receipt_hash, "receipt_hash")
        require_stable_value(self.context_plan_hash, "context_plan_hash")


@dataclass(frozen=True, kw_only=True)
class ModelRequestOptions:
    temperature: float | None = None
    reasoning_effort: str | None = None
    thinking_mode: str | None = None
    structured_output: bool = False
    response_schema_revision: str | None = None
    timeout_seconds: float = 120.0
    max_provider_attempts: int = 1

    def __post_init__(self) -> None:
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if self.structured_output != (self.response_schema_revision is not None):
            raise ValueError(
                "structured output requires exactly one schema revision"
            )
        if self.response_schema_revision is not None:
            require_stable_value(
                self.response_schema_revision,
                "response_schema_revision",
            )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= self.max_provider_attempts <= 3:
            raise ValueError(
                "max_provider_attempts must be between 1 and 3"
            )


@dataclass(frozen=True, kw_only=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.as_tuple()):
            raise ValueError("model usage cannot be negative")

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (
            self.input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )


@dataclass(frozen=True, kw_only=True)
class ModelToolCall:
    index: int
    call_id: str
    name: str
    arguments_json: str
    provider_call_id: str | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("tool call index cannot be negative")
        require_stable_value(self.call_id, "tool_call_id")
        require_stable_value(self.name, "tool_name")
        if self.provider_call_id is not None:
            require_stable_value(
                self.provider_call_id,
                "provider_tool_call_id",
            )
        try:
            arguments = json.loads(self.arguments_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("tool arguments must be valid JSON") from error
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")


@dataclass(frozen=True, kw_only=True)
class ModelOutput:
    kind: ModelOutputKind
    content: str
    schema_revision: str | None = None

    def __post_init__(self) -> None:
        require_stable_value(self.content, "model output")
        if self.kind is ModelOutputKind.STRUCTURED:
            require_stable_value(
                self.schema_revision or "",
                "schema_revision",
            )
            try:
                json.loads(self.content)
            except json.JSONDecodeError as error:
                raise ValueError(
                    "structured output must be valid JSON"
                ) from error
        elif self.schema_revision is not None:
            raise ValueError("text output cannot have a schema revision")


@dataclass(frozen=True, kw_only=True)
class ProviderAttemptReceipt:
    attempt_number: int
    provider: str
    outcome: ProviderAttemptOutcome
    status_code: int | None = None
    response_started: bool = False
    retry_reason: str | None = None
    provider_request_id: str | None = None
    ambiguity_evidence: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        require_stable_value(self.provider, "provider")
        if self.status_code is not None and self.status_code < 100:
            raise ValueError("provider status code is invalid")


@dataclass(frozen=True, kw_only=True)
class ModelResponseReceipt:
    output_kind: ModelOutputKind | None
    output_characters: int
    tool_call_count: int
    invalid_tool_call_count: int
    usage: ModelUsage
    provider: str
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if (
            self.output_characters < 0
            or self.tool_call_count < 0
            or self.invalid_tool_call_count < 0
        ):
            raise ValueError("response receipt counts cannot be negative")
        require_stable_value(self.provider, "provider")


@dataclass(frozen=True, kw_only=True)
class ModelStepRequest:
    model_step_id: ModelStepId
    model_id: str
    request_hash: str
    input_receipt: ModelInputReceipt
    context_plan: ProviderContextPlan
    model_revision: str
    prompt_revision: str
    tool_catalog_revision: str
    options: ModelRequestOptions
    org_id: str | None = None
    provider_api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.model_step_id, "model_step_id"),
            (self.model_id, "model_id"),
            (self.request_hash, "request_hash"),
            (self.model_revision, "model_revision"),
            (self.prompt_revision, "prompt_revision"),
            (self.tool_catalog_revision, "tool_catalog_revision"),
        ):
            require_stable_value(value, name)
        if self.input_receipt.context_plan_hash != self.context_plan.plan_hash:
            raise ValueError("input receipt does not match ContextPlan")
        if self.org_id is not None:
            require_stable_value(self.org_id, "org_id")


@dataclass(frozen=True, kw_only=True)
class ModelStepResult:
    stop_reason: StopReason
    provider_stop_reason: str | None
    response_hash: str
    response_receipt: ModelResponseReceipt
    output: ModelOutput | None
    tool_calls: tuple[ModelToolCall, ...]
    usage: ModelUsage
    attempts: tuple[ProviderAttemptReceipt, ...]

    def __post_init__(self) -> None:
        require_stable_value(self.response_hash, "response_hash")
        if not self.attempts:
            raise ValueError("model result requires an attempt receipt")
        if self.response_receipt.usage != self.usage:
            raise ValueError("response receipt usage does not match result")
        if self.response_receipt.tool_call_count != len(self.tool_calls):
            raise ValueError("response receipt tool count does not match result")
        if self.stop_reason is StopReason.TOOL_CALLS and not self.tool_calls:
            raise ValueError("tool_calls stop requires tool descriptors")
        if self.stop_reason is StopReason.STRUCTURED_FINAL and (
            self.output is None
            or self.output.kind is not ModelOutputKind.STRUCTURED
        ):
            raise ValueError(
                "structured_final requires structured output"
            )


class ModelCallError(RuntimeError):
    """Provider 调用未形成可完成的 ModelStepResult。"""

    def __init__(
        self,
        message: str,
        *,
        model_step_id: ModelStepId,
        provider: str,
        request_hash: str,
        attempts: tuple[ProviderAttemptReceipt, ...],
    ) -> None:
        super().__init__(message)
        self.model_step_id = model_step_id
        self.provider = provider
        self.request_hash = request_hash
        self.attempts = attempts


class ModelProviderError(ModelCallError):
    """Provider 明确失败，且未形成模型结果。"""


class ModelCallUnknownError(ModelCallError):
    """请求可能已被 Provider 接受，结果不可安全重试。"""


class ModelPort(Protocol):
    """Provider adapter 必须实现的确定 ModelStep 边界。"""

    async def complete(
        self,
        request: ModelStepRequest,
        *,
        observer: ModelResponseStartObserver | None = None,
    ) -> ModelStepResult:
        """执行一次逻辑 ModelStep；重试细节由 adapter receipt 描述。"""


class ModelResponseStartObserver(Protocol):
    """首个 Provider 响应在被消费前必须持久化 response_started。"""

    async def response_started(
        self,
        *,
        provider: str,
        provider_request_id: str | None,
    ) -> None: ...
