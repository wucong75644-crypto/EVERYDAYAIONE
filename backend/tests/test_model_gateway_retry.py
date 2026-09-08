"""T5: 真实 RetryContext + Gateway attempt 的失败、取消、用量安全边界。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest

from core.error_classifier import classify_error, ErrorCategory
from core.exceptions import InsufficientCreditsError, ValidationError
from schemas.message import GenerationType, TextPart
from services.adapters.types import ModelProvider, ProviderUnavailableError, StreamChunk, ToolCallDelta
from services.adapters.kie.client import KieAPIError, KieRateLimitError, KieInsufficientBalanceError
from services.adapters.dashscope.chat_adapter import DashScopeAPIError
from services.adapters.openrouter.chat_adapter import OpenRouterAPIError
from services.adapters.google.models import GoogleAPIError, GoogleRateLimitError, GoogleContentFilterError
from services.handlers.chat_handler import ChatHandler
from services.intent_router import RetryContext, RoutingDecision
from services.model_gateway import ModelCallRequest, ModelGateway, ModelGatewayError
from services.agent.observability.model_sampling import SamplingEventType

A, B = "gemini-3-pro", "qwen3.5-plus"


def wrapped(wrapper, cause):
    error = wrapper("wrapped")
    error.__cause__ = cause
    return error


def adapter(*steps):
    async def stream(**_kwargs):
        for step in steps:
            if isinstance(step, BaseException):
                raise step
            yield step
    return SimpleNamespace(stream_chat=Mock(side_effect=stream), close=AsyncMock())


def setup(*adapters, smart=True, cancel=None, context=None, on_retry=None):
    handler = ChatHandler(MagicMock())
    handler._send_retry_notification = AsyncMock()
    handler._record_breaker_result = Mock()
    handler._route_retry = AsyncMock(return_value=RoutingDecision(
        generation_type=GenerationType.CHAT, recommended_model=B,
    ))
    policy = handler._build_model_retry_policy(
        params={"_is_smart_mode": smart}, content=[TextPart(text="hello")],
        task_id="task", conversation_id="conv", user_id="user",
        retry_context=context, on_retry=on_retry,
    )
    events = []
    factory = Mock(side_effect=adapters)
    gateway = ModelGateway(adapter_factory=factory, event_publisher=SimpleNamespace(publish=events.append))
    session = gateway.open_chat(ModelCallRequest(
        model_id=A, task_id="task", request_id="request", retry_policy=policy,
        timeout=1.0, cancel_token=cancel,
    ))
    return session, handler, factory, events


async def consume(session):
    try:
        return [chunk async for chunk in session.stream_chat(messages=[{"role": "user", "content": "hi"}])]
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ConnectionError("reset"), httpx.ConnectError("connect"), httpx.ReadError("read"),
    httpx.RemoteProtocolError("disconnected"),
    wrapped(KieAPIError, httpx.ConnectError("connect")),
    wrapped(DashScopeAPIError, httpx.ReadError("read")),
    wrapped(OpenRouterAPIError, ConnectionResetError("reset")),
    wrapped(GoogleAPIError, ConnectionError("reset")),
    KieRateLimitError("429"), DashScopeAPIError("busy", 429),
    OpenRouterAPIError("busy", "429"), GoogleRateLimitError(),
    KieAPIError("down", 503), DashScopeAPIError("down", 500),
    OpenRouterAPIError("down", 502), GoogleAPIError("down", 503),
    ProviderUnavailableError("open", ModelProvider.KIE),
])
async def test_network_rate_limit_and_provider_failures_retry(error):
    first, second = adapter(error), adapter(StreamChunk(content="ok", prompt_tokens=4, completion_tokens=2))
    session, handler, factory, events = setup(first, second)
    chunks = await consume(session)
    assert [chunk.content for chunk in chunks] == ["ok"]
    assert factory.call_count == 2
    assert [call.args[0] for call in factory.call_args_list] == [A, B]
    assert handler._route_retry.await_count == 1
    assert session.retry_context.failed_models == [A]
    assert session.last_result.status == "completed"
    assert session.last_result.model_id == B
    assert session.last_result.usage == {"prompt_tokens": 4, "completion_tokens": 2}
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    terminal = [event for event in events if event.is_terminal]
    assert len(terminal) == 2
    assert {e.request_id for e in events} == {"request"}
    assert len({e.attempt_id for e in terminal}) == 2
    retry = [e for e in events if e.event is SamplingEventType.RETRY_STARTED]
    assert len(retry) == 1 and retry[0].previous_attempt_id == terminal[0].attempt_id
    assert {e.request_index for e in events} == {0}
    failed_breaker = [c for c in handler._record_breaker_result.call_args_list if not c.kwargs["success"]]
    assert len(failed_breaker) == (0 if isinstance(error, ProviderUnavailableError) else 1)


@pytest.mark.asyncio
async def test_factory_unavailable_falls_back_without_double_counting():
    error = ProviderUnavailableError("open", ModelProvider.KIE)
    second = adapter(StreamChunk(content="ok"))
    session, handler, factory, events = setup(error, second)
    assert events == []  # factory failure belongs to the first consumed attempt
    assert (await consume(session))[0].content == "ok"
    assert len(session.last_result.attempts) == 2
    assert session.last_result.attempts[0].error_code == "PROVIDER_UNAVAILABLE"
    assert factory.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ValidationError("refused"), InsufficientCreditsError(10, 0), RuntimeError("bug"), ValueError("bad input"),
    KieInsufficientBalanceError("balance"), KieAPIError("refused", 400),
    DashScopeAPIError("refused", 403), OpenRouterAPIError("refused", 402),
    GoogleContentFilterError(), GoogleAPIError("unknown"),
    wrapped(KieAPIError, ValueError("bad mapping")),
    wrapped(DashScopeAPIError, ValidationError("refused")),
])
async def test_business_and_unknown_errors_do_not_retry(error):
    session, handler, factory, _ = setup(adapter(error))
    with pytest.raises(ModelGatewayError) as raised:
        await consume(session)
    assert raised.value.result.status == "failed"
    assert len(raised.value.result.attempts) == 1
    handler._route_retry.assert_not_awaited()
    handler._send_retry_notification.assert_not_awaited()
    assert factory.call_count == 1
    handler._record_breaker_result.assert_not_called()


@pytest.mark.asyncio
async def test_content_filter_finish_is_a_business_failure():
    session, handler, factory, _ = setup(adapter(StreamChunk(finish_reason="content_filter")))
    with pytest.raises(ModelGatewayError) as raised:
        await consume(session)
    assert raised.value.result.error_code == "BUSINESS_ERROR"
    assert factory.call_count == 1
    handler._route_retry.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [
    StreamChunk(content="partial"), StreamChunk(thinking_content="thinking"),
    StreamChunk(tool_calls=[ToolCallDelta(index=0, id="call", name="write", arguments_delta="{")]),
    StreamChunk(prompt_tokens=8, completion_tokens=2, credits_consumed=1.5),
    StreamChunk(),  # conservative: even an empty delivered chunk ends the retry window
])
async def test_first_delivered_chunk_prevents_retry_and_concatenation(partial):
    session, handler, factory, events = setup(adapter(partial, ConnectionError("down")))
    seen = []
    with pytest.raises(ModelGatewayError) as raised:
        async for chunk in session.stream_chat(messages=[]):
            seen.append(chunk)
    await session.close()
    assert seen == [partial]
    assert raised.value.result.partial_output is True
    assert raised.value.result.error_code == "MODEL_PARTIAL_OUTPUT"
    assert raised.value.result.stop_reason == "partial_output"
    assert factory.call_count == 1
    handler._route_retry.assert_not_awaited()
    assert not any(e.event is SamplingEventType.RETRY_STARTED for e in events)
    if partial.credits_consumed is not None:
        assert session.last_result.attempts[0].usage["api_credits"] == 1.5
        assert session.last_result.usage["prompt_tokens"] == 8


@pytest.mark.asyncio
async def test_retry_context_default_is_two_total_attempts():
    session, handler, factory, _ = setup(adapter(ConnectionError("a")), adapter(ConnectionError("b")))
    with pytest.raises(ModelGatewayError) as raised:
        await consume(session)
    assert session.retry_context.max_retries == 2
    assert session.retry_context.failed_models == [A, B]
    assert factory.call_count == 2
    assert handler._route_retry.await_count == 1
    assert raised.value.result.error_code == "GENERATION_FAILED"
    assert raised.value.result.stop_reason == "retry_exhausted"


@pytest.mark.asyncio
async def test_manual_model_keeps_retry_disabled():
    session, handler, factory, _ = setup(adapter(ConnectionError("a")), smart=False)
    with pytest.raises(ModelGatewayError):
        await consume(session)
    assert session.retry_context is None
    assert factory.call_count == 1
    handler._route_retry.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", [None, A, "not-a-model"])
async def test_no_route_or_invalid_candidate_cannot_bypass_failed_models(candidate):
    session, handler, factory, _ = setup(adapter(ConnectionError("a")))
    handler._route_retry.return_value = RoutingDecision(
        generation_type=GenerationType.CHAT, recommended_model=candidate,
    ) if candidate else None
    with pytest.raises(ModelGatewayError) as raised:
        await consume(session)
    assert raised.value.result.stop_reason == "no_candidate"
    assert factory.call_count == 1


@pytest.mark.asyncio
async def test_timeout_keeps_t4_no_retry_semantics():
    session, handler, factory, _ = setup(adapter(wrapped(DashScopeAPIError, httpx.ReadTimeout("timeout"))))
    with pytest.raises(ModelGatewayError) as raised:
        await consume(session)
    assert raised.value.result.error_code == "MODEL_TIMEOUT"
    assert raised.value.result.classified_error.is_transient
    assert factory.call_count == 1
    handler._route_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_before_call_and_after_failed_attempt_never_retry():
    cancel = asyncio.Event()
    first = adapter(ConnectionError("a"))
    session, handler, factory, _ = setup(first, cancel=cancel)
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await consume(session)
    first.stream_chat.assert_not_called()
    handler._route_retry.assert_not_awaited()
    assert session.last_result.status == "cancelled"

    cancel.clear()
    async def fail_and_cancel(**_kwargs):
        cancel.set()
        raise ConnectionError("cancel wins")
        yield
    first = adapter()
    first.stream_chat = fail_and_cancel
    session, handler, factory, _ = setup(first, cancel=cancel)
    with pytest.raises(asyncio.CancelledError):
        await consume(session)
    handler._route_retry.assert_not_awaited()
    assert factory.call_count == 1


@pytest.mark.asyncio
async def test_cancel_during_routing_stops_router_and_next_provider():
    cancel, started, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    session, handler, factory, _ = setup(adapter(ConnectionError("a")), cancel=cancel)
    async def route(_ctx):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()
    handler._route_retry.side_effect = route
    pending = asyncio.create_task(consume(session))
    await asyncio.wait_for(started.wait(), 1)
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 1)
    assert stopped.is_set()
    assert session.last_result.status == "cancelled"
    assert factory.call_count == 1
    handler._send_retry_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_in_retry_notification_prevents_next_attempt():
    cancel = asyncio.Event()
    async def on_retry(_model, _attempt):
        cancel.set()
    session, handler, factory, events = setup(adapter(ConnectionError("a")), cancel=cancel, on_retry=on_retry)
    with pytest.raises(asyncio.CancelledError):
        await consume(session)
    assert factory.call_count == 1
    assert not any(e.event is SamplingEventType.RETRY_STARTED for e in events)


@pytest.mark.parametrize("wrapper", [KieAPIError, DashScopeAPIError, OpenRouterAPIError, GoogleAPIError])
def test_classifier_scope_preserves_other_chains_and_handles_nested_causes(wrapper):
    error = wrapper("busy", status_code=429)
    old = classify_error(error)
    classified = classify_error(error, model_call=True)
    assert classified.is_retryable and classified.is_transient
    if wrapper is KieAPIError:
        assert old.error_code == "KIE_ERROR" and not old.is_transient
    else:
        assert old.category is ErrorCategory.UNKNOWN
    nested = wrapped(wrapper, wrapped(wrapper, ConnectionError("down")))
    assert classify_error(nested, model_call=True).error_code == "NETWORK_ERROR"
    nested = wrapped(wrapper, wrapper("refused", status_code=400))
    assert classify_error(nested, model_call=True).category is ErrorCategory.BUSINESS
    a, b = wrapper("a"), wrapper("b")
    a.__cause__, b.__cause__ = b, a
    assert not classify_error(a, model_call=True).is_retryable
    a.__cause__ = a
    assert not classify_error(a, model_call=True).is_retryable

@pytest.mark.asyncio
async def test_previous_tool_round_output_closes_automatic_retry_window():
    calls = 0
    async def stream(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield StreamChunk(tool_calls=[ToolCallDelta(index=0, id="call", name="write", arguments_delta="{}")])
        else:
            raise ConnectionError("after tool result")
    first = adapter()
    first.stream_chat = stream
    session, handler, factory, events = setup(first)
    assert len([chunk async for chunk in session.stream_chat(messages=[])]) == 1
    with pytest.raises(ModelGatewayError) as raised:
        await consume(session)
    assert raised.value.result.partial_output
    assert raised.value.result.error_code == "MODEL_PARTIAL_OUTPUT"
    assert factory.call_count == 1
    handler._route_retry.assert_not_awaited()
    terminal = [e for e in events if e.is_terminal]
    assert terminal[0].request_id != terminal[1].request_id


@pytest.mark.asyncio
async def test_circuit_breaker_counts_each_provider_attempt_once(monkeypatch):
    from services.adapters.factory import MODEL_REGISTRY
    from services.circuit_breaker import get_breaker
    session, handler, factory, _ = setup(adapter(ConnectionError("a")), adapter(StreamChunk(content="ok")))
    session.request.retry_policy.record_breaker = ChatHandler._record_breaker_result
    breakers = {model: get_breaker(MODEL_REGISTRY[model].provider) for model in (A, B)}
    failure = Mock()
    success = Mock()
    monkeypatch.setattr(breakers[A], "record_failure", failure)
    monkeypatch.setattr(breakers[B], "record_success", success)
    await consume(session)
    failure.assert_called_once()
    success.assert_called_once()

@pytest.mark.asyncio
async def test_existing_context_limit_is_not_reset_by_gateway():
    ctx = RetryContext(is_smart_mode=True, original_content="hello", generation_type=GenerationType.CHAT)
    ctx.add_failure("earlier-model", "down")
    session, handler, factory, _ = setup(adapter(ConnectionError("a")), context=ctx)
    with pytest.raises(ModelGatewayError) as raised:
        await consume(session)
    assert raised.value.result.stop_reason == "retry_exhausted"
    assert session.retry_context is ctx
    assert ctx.failed_models == ["earlier-model", A]
    assert factory.call_count == 1
    handler._route_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_refusal_closes_suspended_provider_iterator():
    closed = asyncio.Event()
    async def stream(**_kwargs):
        try:
            yield StreamChunk(finish_reason="content_filter")
            await asyncio.Event().wait()
        finally:
            closed.set()
    provider = adapter()
    provider.stream_chat = stream
    session, _, _, _ = setup(provider)
    with pytest.raises(ModelGatewayError):
        await consume(session)
    assert closed.is_set()
