"""ModelGateway 与共享 Chat 执行边界测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.model_gateway import (
    ModelCallRequest,
    ModelGateway,
    ModelGatewaySession,
    _collect_stream_response,
    get_model_attempt_context,
    get_model_gateway,
)
from services.agent.observability.model_sampling import (
    ObservabilitySamplingEventPublisher,
    SamplingEventType,
)


class _SamplingEvents:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


def _chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=text,
        thinking_content=None,
        tool_calls=None,
        prompt_tokens=1,
        completion_tokens=2,
        credits_consumed=None,
        finish_reason="stop",
    )


def test_get_model_gateway_returns_process_singleton() -> None:
    assert get_model_gateway() is get_model_gateway()


@pytest.mark.asyncio
async def test_gateway_uses_existing_factory_and_forwards_chunks() -> None:
    async def stream_chat(**_kwargs):
        yield _chunk("a")
        yield _chunk("b")

    adapter = SimpleNamespace(
        stream_chat=stream_chat,
        close=AsyncMock(),
        supports_google_search=False,
    )
    factory = Mock(return_value=adapter)
    gateway = ModelGateway(adapter_factory=factory)
    request = ModelCallRequest(
        model_id="model-1",
        org_id="org-1",
        db="db-1",
        task_id="task-1",
    )

    session = gateway.open_chat(request)
    chunks = [chunk async for chunk in session.stream_chat(messages=[])]

    factory.assert_called_once_with("model-1", org_id="org-1", db="db-1")
    assert chunks[0].content == "a"
    assert chunks[1].content == "b"
    assert session.request.task_id == "task-1"


def test_gateway_records_factory_failure_as_a_failed_lifecycle() -> None:
    events = _SamplingEvents()
    factory = Mock(side_effect=RuntimeError("provider setup failed"))
    gateway = ModelGateway(adapter_factory=factory, event_publisher=events)

    with pytest.raises(RuntimeError, match="provider setup failed"):
        gateway.open_chat(ModelCallRequest(
            model_id="model-1", task_id="task-1", trace_id="trace-1",
        ))

    assert [event.event for event in events.events] == [
        SamplingEventType.STARTED,
        SamplingEventType.FAILED,
    ]
    assert events.events[0].request_id == events.events[-1].request_id
    assert events.events[0].attempt_id == events.events[-1].attempt_id
    assert events.events[-1].error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_collect_stream_response_preserves_text_and_usage() -> None:
    async def stream_chat(**_kwargs):
        yield _chunk("part one")
        yield _chunk("part two")

    adapter = SimpleNamespace(
        stream_chat=stream_chat,
        close=AsyncMock(),
    )
    events = _SamplingEvents()
    session = ModelGatewaySession(
        adapter,
        ModelCallRequest(model_id="model-1"),
        event_publisher=events,
    )

    response = await _collect_stream_response(session, messages=[{"role": "user", "content": "hi"}])

    assert response.content == "part onepart two"
    assert response.prompt_tokens == 2
    assert response.completion_tokens == 4


@pytest.mark.asyncio
async def test_collect_stream_response_preserves_provider_credits() -> None:
    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content="a", prompt_tokens=1, completion_tokens=2,
            credits_consumed=0.5, finish_reason=None,
        )
        yield SimpleNamespace(
            content="b", prompt_tokens=3, completion_tokens=4,
            credits_consumed=1.25, finish_reason="stop",
        )

    events = _SamplingEvents()
    session = ModelGatewaySession(
        SimpleNamespace(stream_chat=stream_chat, close=AsyncMock()),
        ModelCallRequest(model_id="model-1"),
        event_publisher=events,
    )

    response = await _collect_stream_response(session, messages=[])

    assert response.prompt_tokens == 4
    assert response.completion_tokens == 6
    assert response.api_credits == 1.25
    assert events.events[-1].usage == {
        "prompt_tokens": 4,
        "completion_tokens": 6,
        "api_credits": 1.25,
    }


@pytest.mark.asyncio
async def test_gateway_session_close_is_idempotent() -> None:
    adapter = SimpleNamespace(close=AsyncMock())
    session = ModelGatewaySession(
        adapter,
        ModelCallRequest(model_id="model-1"),
    )

    await session.close()
    await session.close()

    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_preserves_provider_exception() -> None:
    provider_error = RuntimeError("provider down")

    async def stream_chat(**_kwargs):
        raise provider_error
        yield  # pragma: no cover

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    events = _SamplingEvents()
    session = ModelGatewaySession(
        adapter,
        ModelCallRequest(model_id="model-1", task_id="task-1"),
        event_publisher=events,
        provider="google",
    )

    with pytest.raises(RuntimeError, match="provider down"):
        async for ignored_chunk in session.stream_chat(messages=[]):
            pass

    await session.close()
    adapter.close.assert_awaited_once()
    assert [event.event for event in events.events] == [
        SamplingEventType.STARTED,
        SamplingEventType.FAILED,
    ]
    assert events.events[-1].error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_gateway_session_remains_closable_after_consumer_cancels_stream() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream_chat(**_kwargs):
        started.set()
        await release.wait()
        yield _chunk("unreachable")

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    events = _SamplingEvents()
    session = ModelGatewaySession(
        adapter,
        ModelCallRequest(model_id="model-1", task_id="task-1"),
        event_publisher=events,
    )

    async def consume() -> None:
        async for _chunk in session.stream_chat(messages=[]):
            pass

    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await session.close()

    adapter.close.assert_awaited_once()
    assert [event.event for event in events.events] == [
        SamplingEventType.STARTED,
        SamplingEventType.CANCELLED,
    ]


@pytest.mark.asyncio
async def test_execute_chat_closes_gateway_session_on_cancellation(monkeypatch) -> None:
    from services.handlers.chat.execution_engine import (
        ChatExecutionRequest,
        execute_chat,
    )

    adapter = SimpleNamespace(close=AsyncMock())
    session = ModelGatewaySession(
        adapter,
        ModelCallRequest(model_id="model-1", request_id="task-1"),
    )
    prepared = SimpleNamespace(
        model_gateway=session,
        permission=SimpleNamespace(need_exit_attachment=False),
        core_tools=[],
        stream_kwargs={},
        tool_context=SimpleNamespace(discovered_tools=set()),
        messages=[],
        budget=SimpleNamespace(stop_reason=None, turns_used=0),
    )

    async def fake_prepare(**_kwargs):
        return prepared

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    event = asyncio.Event()
    event.set()
    handler = SimpleNamespace(
        org_id=None,
        _adapter=None,
        _calculate_credits=lambda _usage: 0,
    )

    with pytest.raises(asyncio.CancelledError):
        await execute_chat(
            handler=handler,
            request=ChatExecutionRequest(
                content=[],
                user_id="user-1",
                conversation_id="conv-1",
                task_id="task-1",
                message_id="message-1",
                model_id="model-1",
                context_anchor=None,
            ),
            cancellation_event=event,
        )

    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_sampling_lifecycle_has_stable_request_and_single_first_chunk() -> None:
    async def stream_chat(**_kwargs):
        yield _chunk("first")
        yield _chunk("second")

    events = _SamplingEvents()
    session = ModelGatewaySession(
        SimpleNamespace(stream_chat=stream_chat, close=AsyncMock()),
        ModelCallRequest(
            model_id="model-1",
            task_id="task-1",
            trace_id="trace-1",
            request_id="request-1",
        ),
        event_publisher=events,
        provider="dashscope",
    )

    chunks = [chunk async for chunk in session.stream_chat(messages=[], turn_index=3)]

    assert [chunk.content for chunk in chunks] == ["first", "second"]
    assert [event.event for event in events.events] == [
        SamplingEventType.STARTED,
        SamplingEventType.FIRST_CHUNK,
        SamplingEventType.COMPLETED,
    ]
    assert {event.request_id for event in events.events} == {"request-1"}
    assert {event.attempt_id for event in events.events} == {
        events.events[0].attempt_id,
    }
    assert events.events[0].task_id == "task-1"
    assert events.events[0].trace_id == "trace-1"
    assert events.events[0].provider == "dashscope"
    assert events.events[-1].turn_index == 3
    assert events.events[-1].usage == {
        "prompt_tokens": 2,
        "completion_tokens": 4,
    }


@pytest.mark.asyncio
async def test_gateway_assigns_independent_attempt_ids_for_each_provider_call() -> None:
    async def stream_chat(**_kwargs):
        yield _chunk("one")

    events = _SamplingEvents()
    session = ModelGatewaySession(
        SimpleNamespace(stream_chat=stream_chat, close=AsyncMock()),
        ModelCallRequest(model_id="model-1", task_id="task-1"),
        event_publisher=events,
    )

    [chunk async for chunk in session.stream_chat(messages=[])]
    [chunk async for chunk in session.stream_chat(messages=[])]

    started = [
        event for event in events.events
        if event.event is SamplingEventType.STARTED
    ]
    assert len(started) == 2
    assert started[0].attempt_id != started[1].attempt_id
    assert started[0].request_id != started[1].request_id
    for request_id in {event.request_id for event in started}:
        lifecycle = [event for event in events.events if event.request_id == request_id]
        assert [event.event for event in lifecycle] == [
            SamplingEventType.STARTED,
            SamplingEventType.FIRST_CHUNK,
            SamplingEventType.COMPLETED,
        ]


@pytest.mark.asyncio
async def test_gateway_retry_event_reuses_the_failed_request_context() -> None:
    async def stream_chat(**_kwargs):
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    events = _SamplingEvents()
    gateway = ModelGateway(event_publisher=events)
    session = ModelGatewaySession(
        SimpleNamespace(stream_chat=stream_chat, close=AsyncMock()),
        ModelCallRequest(
            model_id="model-1", task_id="task-1", trace_id="trace-1",
        ),
        event_publisher=events,
        provider="google",
    )

    with pytest.raises(RuntimeError, match="provider down"):
        async for ignored_chunk in session.stream_chat(messages=[]):
            pass

    failed = events.events[-1]
    context = get_model_attempt_context()
    request_id = gateway.record_retry_started(
        task_id="task-1",
        model_id="model-2",
    )

    retry = events.events[-1]
    async def retry_stream_chat(**_kwargs):
        yield _chunk("retry success")

    retry_session = ModelGatewaySession(
        SimpleNamespace(stream_chat=retry_stream_chat, close=AsyncMock()),
        ModelCallRequest(
            model_id="model-2",
            task_id="task-1",
            trace_id="trace-1",
            request_id=request_id,
        ),
        event_publisher=events,
        provider="dashscope",
    )
    [chunk async for chunk in retry_session.stream_chat(messages=[])]

    retry_attempt_started = events.events[-3]
    assert context is not None
    assert request_id == failed.request_id
    assert retry.event is SamplingEventType.RETRY_STARTED
    assert retry.request_id == failed.request_id
    assert retry.previous_attempt_id == failed.attempt_id
    assert retry.model_id == "model-2"
    assert retry_attempt_started.event is SamplingEventType.STARTED
    assert retry_attempt_started.request_id == failed.request_id
    assert retry_attempt_started.attempt_id != failed.attempt_id


@pytest.mark.asyncio
async def test_gateway_retry_context_survives_the_execution_engines_chunk_task() -> None:
    async def stream_chat(**_kwargs):
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    events = _SamplingEvents()
    gateway = ModelGateway(event_publisher=events)
    session = ModelGatewaySession(
        SimpleNamespace(stream_chat=stream_chat, close=AsyncMock()),
        ModelCallRequest(model_id="model-1", task_id="task-1", trace_id="trace-1"),
        event_publisher=events,
        provider="google",
    )
    iterator = session.stream_chat(messages=[]).__aiter__()
    parent_context = get_model_attempt_context()

    with pytest.raises(RuntimeError, match="provider down"):
        await asyncio.create_task(iterator.__anext__())

    # 子 Task 的 ContextVar 不会回传；retry 必须使用 session 的显式关联字段。
    assert get_model_attempt_context() is parent_context
    context = session.last_attempt_context
    assert context is not None
    request_id = gateway.record_retry_started(
        task_id="task-1",
        model_id="model-2",
        attempt_context=context,
    )

    retry = events.events[-1]
    assert request_id == context.request_id
    assert retry.event is SamplingEventType.RETRY_STARTED
    assert retry.previous_attempt_id == context.attempt_id


@pytest.mark.asyncio
async def test_execute_chat_exposes_child_task_failure_context_for_retry(monkeypatch) -> None:
    from services.handlers.chat.execution_engine import (
        ChatExecutionRequest,
        execute_chat,
    )

    async def stream_chat(**_kwargs):
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    session = ModelGatewaySession(
        SimpleNamespace(stream_chat=stream_chat, close=AsyncMock()),
        ModelCallRequest(model_id="model-1", task_id="task-1", trace_id="trace-1"),
        event_publisher=_SamplingEvents(),
    )
    prepared = SimpleNamespace(model_gateway=session)

    async def fake_prepare(**_kwargs):
        return prepared

    async def fake_run_loop(**_kwargs):
        iterator = session.stream_chat(messages=[]).__aiter__()
        await asyncio.create_task(iterator.__anext__())

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    monkeypatch.setattr(
        "services.handlers.chat.execution_engine._run_loop",
        fake_run_loop,
    )
    handler = SimpleNamespace(
        org_id=None,
        _adapter=None,
        _calculate_credits=lambda _usage: 0,
    )

    with pytest.raises(RuntimeError, match="provider down"):
        await execute_chat(
            handler=handler,
            request=ChatExecutionRequest(
                content=[], user_id="user-1", conversation_id="conv-1",
                task_id="task-1", message_id="message-1", model_id="model-1",
                context_anchor=None,
            ),
        )

    assert handler._last_model_attempt_context is session.last_attempt_context
    assert handler._last_model_attempt_context is not None


@pytest.mark.asyncio
async def test_default_sampling_publisher_does_not_delay_first_chunk(monkeypatch) -> None:
    import time

    from services.agent.observability import model_sampling

    def slow_publish(_event) -> None:
        time.sleep(0.1)

    monkeypatch.setattr(model_sampling, "_publish_sampling_event", slow_publish)
    session = ModelGatewaySession(
        SimpleNamespace(
            stream_chat=lambda **_kwargs: _single_chunk_stream(),
            close=AsyncMock(),
        ),
        ModelCallRequest(model_id="model-1", task_id="task-1"),
        event_publisher=ObservabilitySamplingEventPublisher(),
    )

    started_at = time.monotonic()
    iterator = session.stream_chat(messages=[]).__aiter__()
    first = await iterator.__anext__()
    assert first.content == "first"
    assert time.monotonic() - started_at < 0.05
    await iterator.aclose()
    await asyncio.sleep(0.11)


async def _single_chunk_stream():
    yield _chunk("first")


@pytest.mark.asyncio
async def test_sampling_event_excludes_prompt_and_api_key_from_safe_fields() -> None:
    async def stream_chat(**_kwargs):
        yield _chunk("safe output")

    events = _SamplingEvents()
    session = ModelGatewaySession(
        SimpleNamespace(stream_chat=stream_chat, close=AsyncMock()),
        ModelCallRequest(model_id="model-1", task_id="task-1"),
        event_publisher=events,
    )

    secret_prompt = "private user content with example-api-key-value"
    [chunk async for chunk in session.stream_chat(messages=[{
        "role": "user", "content": secret_prompt,
    }])]

    session.record_retry_started(
        request_id="request-1",
        previous_attempt_id="attempt-1",
    )

    fields = [event.log_fields() for event in events.events]
    rendered = repr(fields)
    assert fields[-1]["event"] == SamplingEventType.RETRY_STARTED.value
    assert "example-api-key-value" not in rendered
    assert "private user content" not in rendered
    assert all("input" not in field for field in fields)


@pytest.mark.asyncio
async def test_prepare_chat_stream_opens_gateway_for_shared_web_actor_path(
    monkeypatch,
) -> None:
    from services.handlers.chat import stream_setup

    adapter = SimpleNamespace(
        stream_chat=Mock(),
        close=AsyncMock(),
        supports_google_search=False,
    )
    factory = Mock(return_value=adapter)
    gateway = ModelGateway(adapter_factory=factory)
    monkeypatch.setattr(
        "services.model_gateway.get_model_gateway",
        lambda: gateway,
    )
    monkeypatch.setattr(
        stream_setup,
        "_prepare_permission_and_tools",
        lambda *_args: (SimpleNamespace(), []),
    )
    monkeypatch.setattr(
        stream_setup,
        "_prepare_request_context",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(
        stream_setup,
        "_prepare_budget",
        lambda: SimpleNamespace(),
    )
    handler = SimpleNamespace(
        org_id="org-1",
        db="db-1",
        _extract_text_content=lambda _content: "问题",
        _build_llm_messages=AsyncMock(return_value=[]),
    )

    prepared = await stream_setup.prepare_chat_stream(
        handler=handler,
        content=[],
        user_id="user-1",
        conversation_id="conv-1",
        task_id="task-1",
        model_id="model-1",
        model_request_id="request-1",
        permission_mode="auto",
        needs_google_search=False,
        params={},
        context_anchor=None,
    )

    factory.assert_called_once_with("model-1", org_id="org-1", db="db-1")
    assert prepared.model_gateway is not None
    assert prepared.model_gateway.request.request_id == "request-1"
    await prepared.model_gateway.close()
    adapter.close.assert_awaited_once()
