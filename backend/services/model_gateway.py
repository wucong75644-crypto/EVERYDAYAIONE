"""进程内 Chat ModelGateway。

Gateway 是主 Chat、Actor Chat 和企微兼容入口共享的模型边界。它复用现有
模型注册表与 adapter factory，只负责打开一次模型会话、转发 StreamChunk
以及关闭 Provider adapter；工具编排、消息持久化和通道协议仍由上层负责。
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable
from uuid import uuid4

from services.agent.observability.model_sampling import (
    ModelSamplingEvent,
    ObservabilitySamplingEventPublisher,
    SamplingEventPublisher,
    SamplingEventType,
)

if TYPE_CHECKING:
    from services.adapters.base import StreamChunk


@dataclass(frozen=True)
class ModelAttemptContext:
    """当前协程最近一次 Gateway attempt 的安全关联字段。"""

    task_id: str | None
    trace_id: str | None
    request_id: str
    attempt_id: str
    model_id: str
    provider: str | None
    request_index: int
    turn_index: int | None = None


_attempt_context: ContextVar[ModelAttemptContext | None] = ContextVar(
    "model_gateway_attempt_context",
    default=None,
)


class ModelGatewayTimeoutError(TimeoutError):
    """统一的永久 ModelGateway 请求超时。"""

    def __init__(self, model_id: str, timeout: float, phase: str) -> None:
        self.model_id = model_id
        self.timeout = timeout
        self.phase = phase
        super().__init__(
            f"ModelGateway request timed out | model={model_id} | "
            f"phase={phase} | timeout={timeout:.3f}s"
        )


@dataclass(frozen=True)
class ModelCallRequest:
    """一次模型会话的稳定请求边界。

    task_id 是业务任务 ID；trace_id 复用既有全链路追踪。request_id 可由
    调用方为单次模型请求显式指定；未指定时 Gateway 在真正开始 Provider 调用
    前生成。timeout 和 cancel_token 由 Gateway 统一落实为请求 deadline 与取消边界；
    retry_policy 仍由既有上层重试层持有，Gateway 不自行决定或执行重试。
    """

    model_id: str
    org_id: str | None = None
    db: Any = None
    task_id: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    timeout: float | None = None
    cancel_token: Any = None
    retry_policy: Any = None
    budget: Any = None


class ModelGatewaySession:
    """由 ModelGateway 管理生命周期的单次 Chat adapter 会话。"""

    def __init__(
        self,
        adapter: Any,
        request: ModelCallRequest,
        event_publisher: SamplingEventPublisher | None = None,
        provider: str | None = None,
    ) -> None:
        self._adapter = adapter
        self.request = request
        self._closed = False
        self._event_publisher = event_publisher or ObservabilitySamplingEventPublisher()
        self._provider = provider
        self._request_index = 0
        self._last_attempt_context: ModelAttemptContext | None = None
        # 延迟解析默认 timeout：open_chat 本身不能发起 Provider 请求，且
        # headless/测试注入器可能只构造会话而不消费模型流。
        self._stream_timeout = (
            float(request.timeout) if request.timeout is not None else None
        )

    @property
    def model_id(self) -> str:
        return self.request.model_id

    @property
    def adapter_name(self) -> str:
        return type(self._adapter).__name__

    @property
    def task_id(self) -> str | None:
        """业务任务标识；没有任务上下文的独立调用保持为空。"""
        return self.request.task_id

    @property
    def trace_id(self) -> str | None:
        """优先复用入口 trace，未设置时按既有 task 追踪语义回退。"""
        return _resolve_trace_id(self.request)

    @property
    def provider(self) -> str | None:
        return self._provider

    @property
    def last_attempt_context(self) -> ModelAttemptContext | None:
        """最近一次 Provider 调用的关联字段，供跨 Task 的既有重试层显式传递。"""
        return self._last_attempt_context

    @property
    def supports_google_search(self) -> bool:
        return bool(getattr(self._adapter, "supports_google_search", False))

    def create_google_search_tool(self) -> dict[str, Any]:
        return self._adapter.create_google_search_tool()

    def estimate_cost_unified(self, input_tokens: int, output_tokens: int) -> Any:
        return self._adapter.estimate_cost_unified(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        reasoning_effort: str | None = None,
        thinking_mode: str | None = None,
        *,
        turn_index: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """转发现有 StreamChunk，并统一处理请求 deadline 与取消。"""
        if self._closed:
            raise RuntimeError("MODEL_GATEWAY_SESSION_CLOSED")
        request_index = self._request_index
        self._request_index += 1
        request_id = self._next_request_id(request_index)
        attempt_id = _new_identifier("attempt")
        attempt_context = ModelAttemptContext(
            task_id=self.task_id,
            trace_id=self.trace_id,
            request_id=request_id,
            attempt_id=attempt_id,
            model_id=self.model_id,
            provider=self.provider,
            request_index=request_index,
            turn_index=turn_index,
        )
        # stream 可能由调用方放进独立 asyncio Task 消费；ContextVar 不会
        # 回传到父 Task，因此同时保存一份会话级只读关联信息给 retry 显式使用。
        self._last_attempt_context = attempt_context
        _set_attempt_context(attempt_context)
        usage: dict[str, int | float] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        first_chunk_emitted = False
        terminal_emitted = False

        def emit(
            event_type: SamplingEventType,
            *,
            error_type: str | None = None,
        ) -> None:
            nonlocal terminal_emitted
            if event_type in {
                SamplingEventType.COMPLETED,
                SamplingEventType.FAILED,
                SamplingEventType.CANCELLED,
            }:
                if terminal_emitted:
                    return
                terminal_emitted = True
            self._emit_event(
                ModelSamplingEvent(
                    event=event_type,
                    task_id=self.task_id,
                    trace_id=self.trace_id,
                    request_id=request_id,
                    attempt_id=attempt_id,
                    model_id=self.model_id,
                    provider=self.provider,
                    request_index=request_index,
                    turn_index=turn_index,
                    usage=dict(usage),
                    error_type=error_type,
                )
            )

        emit(SamplingEventType.STARTED)
        provider_iterator: Any = None
        next_chunk: asyncio.Task[Any] | None = None
        cancel_waiter: asyncio.Task[Any] | None = None
        stream_timeout: float | None = None
        try:
            _raise_if_cancelled(self.request.cancel_token, self.task_id)
            stream_timeout = self._stream_timeout
            if stream_timeout is None:
                stream_timeout = _resolve_request_timeout(self.request)
            provider_stream = self._adapter.stream_chat(
                messages=messages,
                reasoning_effort=reasoning_effort,
                thinking_mode=thinking_mode,
                **kwargs,
            )
            provider_iterator = provider_stream.__aiter__()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + stream_timeout
            while True:
                _raise_if_cancelled(self.request.cancel_token, self.task_id)
                remaining = self._remaining_timeout(deadline)
                if remaining <= 0:
                    await self._stop_provider_stream(provider_iterator)
                    raise ModelGatewayTimeoutError(
                        self.model_id,
                        stream_timeout,
                        "first_chunk" if not first_chunk_emitted else "stream",
                    )

                next_chunk = asyncio.create_task(provider_iterator.__anext__())
                cancel_waiter = _create_cancel_waiter(
                    self.request.cancel_token,
                    self.task_id,
                )
                wait_set: set[asyncio.Task[Any]] = {next_chunk}
                if cancel_waiter is not None:
                    wait_set.add(cancel_waiter)
                done, _ = await asyncio.wait(
                    wait_set,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_waiter is not None and cancel_waiter in done:
                    await self._stop_provider_stream(provider_iterator, next_chunk)
                    next_chunk = None
                    raise asyncio.CancelledError
                if self._is_cancelled():
                    await self._stop_provider_stream(provider_iterator, next_chunk)
                    next_chunk = None
                    raise asyncio.CancelledError
                if next_chunk not in done:
                    await self._stop_provider_stream(provider_iterator, next_chunk)
                    next_chunk = None
                    raise ModelGatewayTimeoutError(
                        self.model_id,
                        stream_timeout,
                        "first_chunk" if not first_chunk_emitted else "stream",
                    )
                if cancel_waiter is not None and not cancel_waiter.done():
                    cancel_waiter.cancel()
                if cancel_waiter is not None:
                    await asyncio.gather(cancel_waiter, return_exceptions=True)
                    cancel_waiter = None

                try:
                    chunk = next_chunk.result()
                except StopAsyncIteration:
                    next_chunk = None
                    break
                next_chunk = None
                _raise_if_cancelled(self.request.cancel_token, self.task_id)
                _accumulate_usage(usage, chunk)
                if not first_chunk_emitted:
                    first_chunk_emitted = True
                    emit(SamplingEventType.FIRST_CHUNK)
                yield chunk
            emit(SamplingEventType.COMPLETED)
        except ModelGatewayTimeoutError as error:
            emit(SamplingEventType.FAILED, error_type=type(error).__name__)
            raise
        except (asyncio.CancelledError, GeneratorExit):
            await self._stop_provider_stream(provider_iterator, next_chunk)
            emit(SamplingEventType.CANCELLED)
            raise
        except Exception as error:
            if _is_timeout_error(error):
                await self._stop_provider_stream(provider_iterator)
                timeout_error = ModelGatewayTimeoutError(
                    self.model_id,
                    stream_timeout or 0.0,
                    "provider",
                )
                emit(
                    SamplingEventType.FAILED,
                    error_type=type(timeout_error).__name__,
                )
                raise timeout_error from error
            emit(SamplingEventType.FAILED, error_type=type(error).__name__)
            raise
        finally:
            if cancel_waiter is not None and not cancel_waiter.done():
                cancel_waiter.cancel()
            if cancel_waiter is not None:
                await asyncio.gather(cancel_waiter, return_exceptions=True)

    def record_retry_started(
        self,
        *,
        request_id: str,
        previous_attempt_id: str | None = None,
        turn_index: int | None = None,
    ) -> None:
        """供既有重试层声明重试开始；本方法不决定也不执行重试。"""
        self._emit_event(
            ModelSamplingEvent(
                event=SamplingEventType.RETRY_STARTED,
                task_id=self.task_id,
                trace_id=self.trace_id,
                request_id=request_id,
                attempt_id=None,
                previous_attempt_id=previous_attempt_id,
                model_id=self.model_id,
                provider=self.provider,
                request_index=self._request_index,
                turn_index=turn_index,
            )
        )

    def _next_request_id(self, request_index: int) -> str:
        if request_index == 0 and self.request.request_id:
            return self.request.request_id
        return _new_identifier("request")

    def _emit_event(self, event: ModelSamplingEvent) -> None:
        try:
            self._event_publisher.publish(event)
        except Exception:
            # 采样不能破坏或阻塞既有模型流；不要在该热路径同步记录异常。
            pass

    async def close(self) -> None:
        """幂等关闭 Provider adapter，兼容异常与取消收尾。"""
        if self._closed:
            return
        self._closed = True
        await self._adapter.close()

    def _is_cancelled(self) -> bool:
        return _is_cancelled(self.request.cancel_token, self.task_id)

    def _remaining_timeout(self, deadline: float) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        budget_remaining = getattr(self.request.budget, "remaining", None)
        if isinstance(budget_remaining, (int, float)):
            remaining = min(remaining, float(budget_remaining))
        return remaining

    async def _stop_provider_stream(
        self,
        provider_iterator: Any,
        next_chunk: asyncio.Task[Any] | None = None,
    ) -> None:
        """停止 iterator 后关闭 adapter；收尾失败不能掩盖 cancel/timeout。"""
        if next_chunk is not None and not next_chunk.done():
            next_chunk.cancel()
        if next_chunk is not None:
            await asyncio.gather(next_chunk, return_exceptions=True)
        if provider_iterator is not None:
            close_iterator = getattr(provider_iterator, "aclose", None)
            if close_iterator is not None:
                try:
                    await close_iterator()
                except Exception:
                    pass
        try:
            await self.close()
        except Exception:
            pass


class ModelGateway:
    """永久进程内的模型调用门面。"""

    def __init__(
        self,
        adapter_factory: Callable[..., Any] | None = None,
        event_publisher: SamplingEventPublisher | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory
        self._event_publisher = event_publisher or ObservabilitySamplingEventPublisher()

    def open_chat(self, request: ModelCallRequest) -> ModelGatewaySession:
        """按现有工厂选择模型并创建一个可复用的 Chat 会话。"""
        # 让 pre-stream factory 异常也有完整的 request 生命周期；正常会话
        # 的首个 stream 会复用该 request_id，而后续工具回合自行生成新 ID。
        if request.request_id is None:
            request = replace(request, request_id=_new_identifier("request"))
        provider = _resolve_provider(request.model_id)
        if self._adapter_factory is None:
            # 运行时读取模块属性，保留现有测试和配置注入对 factory
            # 的替换能力。
            from services.adapters import factory

            adapter_factory = factory.create_chat_adapter
        else:
            adapter_factory = self._adapter_factory

        factory_kwargs: dict[str, Any] = {
            "org_id": request.org_id,
            "db": request.db,
        }
        if request.timeout is not None:
            factory_kwargs["stream_timeout"] = request.timeout
        try:
            adapter = adapter_factory(request.model_id, **factory_kwargs)
        except Exception as error:
            attempt_id = _new_identifier("attempt")
            _set_attempt_context(ModelAttemptContext(
                task_id=request.task_id,
                trace_id=_resolve_trace_id(request),
                request_id=request.request_id,
                attempt_id=attempt_id,
                model_id=request.model_id,
                provider=provider,
                request_index=0,
            ))
            self._publish_open_failure(
                request=request,
                provider=provider,
                attempt_id=attempt_id,
                error=error,
            )
            raise
        return ModelGatewaySession(
            adapter,
            request,
            event_publisher=self._event_publisher,
            provider=provider,
        )

    def record_retry_started(
        self,
        *,
        task_id: str,
        model_id: str,
        attempt_context: ModelAttemptContext | None = None,
    ) -> str | None:
        """记录既有重试层已决定的重试，不参与其路由或次数决策。"""
        previous = attempt_context or get_model_attempt_context()
        if previous is None or previous.task_id != task_id:
            return None
        event = ModelSamplingEvent(
            event=SamplingEventType.RETRY_STARTED,
            task_id=previous.task_id,
            trace_id=previous.trace_id,
            request_id=previous.request_id,
            attempt_id=None,
            previous_attempt_id=previous.attempt_id,
            model_id=model_id,
            provider=_resolve_provider(model_id),
            request_index=previous.request_index,
            turn_index=previous.turn_index,
        )
        try:
            self._event_publisher.publish(event)
        except Exception:
            # 采样失败不能影响既有 retry 路径。
            pass
        return previous.request_id

    def _publish_open_failure(
        self,
        *,
        request: ModelCallRequest,
        provider: str | None,
        attempt_id: str,
        error: Exception,
    ) -> None:
        shared = {
            "task_id": request.task_id,
            "trace_id": _resolve_trace_id(request),
            "request_id": request.request_id or _new_identifier("request"),
            "attempt_id": attempt_id,
            "model_id": request.model_id,
            "provider": provider,
            "request_index": 0,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        try:
            self._event_publisher.publish(
                ModelSamplingEvent(event=SamplingEventType.STARTED, **shared)
            )
            self._event_publisher.publish(
                ModelSamplingEvent(
                    event=SamplingEventType.FAILED,
                    error_type=type(error).__name__,
                    **shared,
                )
            )
        except Exception:
            # 观测不能覆盖原始的 adapter factory 异常。
            pass


async def _collect_stream_response(
    session: ModelGatewaySession,
    *,
    messages: list[dict[str, Any]],
    reasoning_effort: str | None = None,
    thinking_mode: str | None = None,
    **kwargs: Any,
) -> Any:
    """收集现有流式边界的文本响应；不引入第二套模型调用协议。"""
    content = ""
    finish_reason: str | None = None
    usage: dict[str, int | float] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    async for chunk in session.stream_chat(
        messages=messages,
        reasoning_effort=reasoning_effort,
        thinking_mode=thinking_mode,
        **kwargs,
    ):
        if getattr(chunk, "content", None):
            content += chunk.content
        _accumulate_usage(usage, chunk)
        if getattr(chunk, "finish_reason", None):
            finish_reason = chunk.finish_reason
    return SimpleNamespace(
        content=content,
        finish_reason=finish_reason,
        prompt_tokens=int(usage["prompt_tokens"]),
        completion_tokens=int(usage["completion_tokens"]),
        api_credits=usage.get("api_credits"),
    )


def _accumulate_usage(usage: dict[str, int | float], chunk: Any) -> None:
    """与 Chat execution_engine 一致地聚合每个 StreamChunk 的用量。"""
    usage["prompt_tokens"] += getattr(chunk, "prompt_tokens", 0) or 0
    usage["completion_tokens"] += getattr(chunk, "completion_tokens", 0) or 0
    credits = getattr(chunk, "credits_consumed", None)
    if credits is not None:
        # Provider credits 是既有最终帧语义，保留最近一次报告值而不在 Gateway 结算。
        usage["api_credits"] = credits


def _resolve_request_timeout(request: ModelCallRequest) -> float:
    if request.timeout is not None:
        return float(request.timeout)
    from services.timeout_resolver import resolve_stream_timeout

    return float(resolve_stream_timeout(request.model_id))


def _is_cancelled(token: Any, task_id: str | None) -> bool:
    if token is None:
        return False
    is_set = getattr(token, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    is_signalled = getattr(token, "is_signalled", None)
    if callable(is_signalled) and task_id:
        return bool(is_signalled(task_id))
    return False


def _raise_if_cancelled(token: Any, task_id: str | None) -> None:
    if _is_cancelled(token, task_id):
        raise asyncio.CancelledError


def _create_cancel_waiter(
    token: Any,
    task_id: str | None,
) -> asyncio.Task[Any] | None:
    if token is None:
        return None
    wait = getattr(token, "wait", None)
    if callable(wait):
        return asyncio.create_task(wait())
    if callable(getattr(token, "is_set", None)) or callable(
        getattr(token, "is_signalled", None)
    ):
        return asyncio.create_task(_poll_cancel(token, task_id))
    return None


async def _poll_cancel(token: Any, task_id: str | None) -> None:
    while not _is_cancelled(token, task_id):
        await asyncio.sleep(0.05)


def _is_timeout_error(error: BaseException) -> bool:
    """识别 Provider 已包装的 connect/read timeout，不误判普通错误文本。"""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, asyncio.TimeoutError)):
            return True
        if "timeout" in type(current).__name__.lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def _new_identifier(kind: str) -> str:
    return f"{kind}_{uuid4().hex}"


def _resolve_provider(model_id: str) -> str | None:
    """仅从现有注册表读取 Provider 名称；绝不读取或输出凭据。"""
    try:
        from services.adapters.factory import DEFAULT_MODEL_ID, MODEL_REGISTRY

        config = MODEL_REGISTRY.get(model_id) or MODEL_REGISTRY.get(DEFAULT_MODEL_ID)
        return config.provider.value if config else None
    except Exception:
        return None


def _resolve_trace_id(request: ModelCallRequest) -> str | None:
    if request.trace_id:
        return request.trace_id
    try:
        from services.agent.observability import get_trace_id

        trace_id = get_trace_id()
    except Exception:
        trace_id = ""
    return trace_id or request.task_id


def _set_attempt_context(context: ModelAttemptContext) -> None:
    _attempt_context.set(context)


def get_model_attempt_context() -> ModelAttemptContext | None:
    """读取当前协程最近一次 attempt，仅用于现有 retry 的关联。"""
    return _attempt_context.get()


def record_retry_started(
    *,
    task_id: str,
    model_id: str,
    attempt_context: ModelAttemptContext | None = None,
) -> str | None:
    """给既有 retry 层的窄接口；不改变其任何重试行为。"""
    return get_model_gateway().record_retry_started(
        task_id=task_id,
        model_id=model_id,
        attempt_context=attempt_context,
    )


_MODEL_GATEWAY = ModelGateway()


def get_model_gateway() -> ModelGateway:
    """返回应用进程内唯一的 Chat ModelGateway。"""
    return _MODEL_GATEWAY


__all__ = [
    "ModelCallRequest",
    "ModelAttemptContext",
    "ModelGatewayTimeoutError",
    "ModelGateway",
    "ModelGatewaySession",
    "get_model_attempt_context",
    "get_model_gateway",
    "record_retry_started",
]
