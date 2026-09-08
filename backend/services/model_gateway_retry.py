"""Gateway attempt 结果与既有 Chat 重试策略的依赖接口。

不定义重试次数、候选模型或另一份失败历史：这些仍属于 RetryContext 和
IntentRouter。副作用通过调用方 hook 执行，Gateway 不读写业务任务或积分。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from core.error_classifier import ClassifiedError
    from services.intent_router import RetryContext, RoutingDecision
    from services.model_gateway import ModelAttemptContext


@dataclass
class ModelRetryPolicy:
    build_context: Callable[[str, Exception, Any], RetryContext | None]
    route: Callable[[RetryContext], Awaitable[RoutingDecision | None]]
    record_breaker: Callable[..., None]
    on_retry: Callable[[str, int], Awaitable[None]] | None = None
    context: RetryContext | None = None
    prepare_stream: Callable[[Any, dict[str, Any]], dict[str, Any]] | None = None


@dataclass(frozen=True)
class ModelAttemptResult:
    context: ModelAttemptContext
    status: str
    usage: dict[str, int | float]
    partial_output: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class ModelCallResult:
    request_id: str
    model_id: str
    status: str
    attempts: tuple[ModelAttemptResult, ...]
    usage: dict[str, int | float]
    partial_output: bool = False
    error_code: str | None = None
    stop_reason: str | None = None
    classified_error: ClassifiedError | None = None


class ModelGatewayError(Exception):
    """模型请求已经结束；上层只负责提交失败终态，不能再次重试。"""

    def __init__(self, result: ModelCallResult, error: Exception) -> None:
        self.result = result
        self.original = error
        super().__init__(str(error) or type(error).__name__)
