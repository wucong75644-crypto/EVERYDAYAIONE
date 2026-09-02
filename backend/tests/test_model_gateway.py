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
    get_model_gateway,
)


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
        request_id="task-1",
    )

    session = gateway.open_chat(request)
    chunks = [chunk async for chunk in session.stream_chat(messages=[])]

    factory.assert_called_once_with("model-1", org_id="org-1", db="db-1")
    assert chunks[0].content == "a"
    assert chunks[1].content == "b"
    assert session.request.request_id == "task-1"


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
    session = ModelGatewaySession(
        adapter,
        ModelCallRequest(model_id="model-1"),
    )

    with pytest.raises(RuntimeError, match="provider down"):
        async for _chunk in session.stream_chat(messages=[]):
            pass

    await session.close()
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_session_remains_closable_after_consumer_cancels_stream() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream_chat(**_kwargs):
        started.set()
        await release.wait()
        yield _chunk("unreachable")

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    session = ModelGatewaySession(
        adapter,
        ModelCallRequest(model_id="model-1", request_id="task-1"),
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
        permission_mode="auto",
        needs_google_search=False,
        params={},
        context_anchor=None,
    )

    factory.assert_called_once_with("model-1", org_id="org-1", db="db-1")
    assert prepared.model_gateway is not None
    await prepared.model_gateway.close()
    adapter.close.assert_awaited_once()
