"""通道无关 Chat 执行内核单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schemas.message import TextPart
from services.handlers.chat.execution_engine import (
    ChatExecutionRequest,
    _stable_actor_tool_call_id,
    _actor_tool_completion_command_id,
    execute_chat,
)
from services.conversation_commands import SafePoint
from services.conversation_commands import CommandType, ConversationCommand
from services.conversation_turn_runtime import ConversationTurnRuntime


def _request() -> ChatExecutionRequest:
    return ChatExecutionRequest(
        content=[TextPart(text="你好")],
        user_id="user-1",
        conversation_id="conv-1",
        task_id="task-1",
        message_id="output-1",
        model_id="model-1",
        context_anchor=object(),
    )


class _PauseStore:
    def __init__(self, ready: asyncio.Event):
        self.ready = ready

    async def load_pending(self, *, task_id: str, execution_token: str):
        if not self.ready.is_set():
            return []
        return [ConversationCommand(
            command_id="pause-event",
            command_type=CommandType.PAUSE,
            conversation_id="conv-1",
            task_id=task_id,
            turn_id="turn-1",
        )]

    async def acknowledge(self, **_kwargs):
        return None


def test_actor_tool_call_id_is_stable_for_same_turn_and_arguments():
    first = _stable_actor_tool_call_id("turn-1", 0, "erp_execute", '{"a":1}')
    second = _stable_actor_tool_call_id("turn-1", 0, "erp_execute", '{"a":1}')
    different_args = _stable_actor_tool_call_id("turn-1", 0, "erp_execute", '{"a":2}')

    assert first == second
    assert first != different_args
    assert first.startswith("actor-call:turn-1:0:")


def test_actor_tool_completion_command_id_is_bounded_and_stable():
    tool_call_ids = [f"actor-call:turn-1:{index}:" + "x" * 120 for index in range(8)]

    first = _actor_tool_completion_command_id("task-1", "turn-1", tool_call_ids)
    second = _actor_tool_completion_command_id("task-1", "turn-1", tool_call_ids)
    reordered = _actor_tool_completion_command_id(
        "task-1", "turn-1", list(reversed(tool_call_ids))
    )

    assert first == second
    assert first != reordered
    assert len(first) <= 200
    assert first.startswith("tool-batch:task-1:turn-1:8:")


@pytest.mark.asyncio
async def test_execute_chat_collects_usage_and_closes_adapter(monkeypatch):
    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content="你好",
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=3,
            completion_tokens=2,
            credits_consumed=None,
            finish_reason="stop",
        )

    adapter = SimpleNamespace(
        stream_chat=stream_chat,
        close=AsyncMock(),
    )
    prepared = SimpleNamespace(
        adapter=adapter,
        permission=SimpleNamespace(
            need_exit_attachment=False,
            get_reminder=lambda _turn: "",
        ),
        core_tools=[],
        stream_kwargs={},
        tool_context=SimpleNamespace(
            discovered_tools=set(),
            build_context_prompt=lambda: "",
        ),
        messages=[],
        budget=SimpleNamespace(
            stop_reason=None,
            turns_used=0,
            use_turn=lambda: None,
        ),
    )

    def use_turn():
        prepared.budget.turns_used += 1

    prepared.budget.use_turn = use_turn

    async def fake_prepare(**_kwargs):
        return prepared

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    handler = SimpleNamespace(
        org_id="org-1",
        _adapter=None,
        _calculate_credits=lambda usage: usage["completion_tokens"],
    )
    runtime = ConversationTurnRuntime(
        conversation_id="conv-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
    )

    result = await execute_chat(
        handler=handler,
        request=_request(),
        runtime=runtime,
    )

    assert result.parts[0].text == "你好"
    assert result.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
    }
    assert result.credits_cost == 2
    adapter.close.assert_awaited_once()
    assert runtime.last_safe_point is SafePoint.AFTER_MODEL
    assert len(runtime.applied_commands) == 1


@pytest.mark.asyncio
async def test_execute_chat_stops_before_provider_when_cancelled(monkeypatch):
    adapter = SimpleNamespace(close=AsyncMock())
    prepared = SimpleNamespace(
        adapter=adapter,
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
    handler = SimpleNamespace(org_id=None, _adapter=None)

    with pytest.raises(asyncio.CancelledError):
        await execute_chat(
            handler=handler,
            request=_request(),
            cancellation_event=event,
        )

    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_chat_interrupts_waiting_provider_when_command_arrives(monkeypatch):
    provider_started = asyncio.Event()

    async def stream_chat(**_kwargs):
        provider_started.set()
        await asyncio.Event().wait()
        yield SimpleNamespace(
            content="never reached",
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=0,
            completion_tokens=0,
            credits_consumed=None,
            finish_reason="stop",
        )

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    prepared = SimpleNamespace(
        adapter=adapter,
        permission=SimpleNamespace(need_exit_attachment=False),
        core_tools=[],
        stream_kwargs={},
        tool_context=SimpleNamespace(discovered_tools=set()),
        messages=[],
        budget=SimpleNamespace(
            stop_reason=None,
            turns_used=0,
            use_turn=lambda: None,
        ),
    )

    async def fake_prepare(**_kwargs):
        return prepared

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    handler = SimpleNamespace(org_id=None, _adapter=None)
    runtime = ConversationTurnRuntime(
        conversation_id="conv-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
        command_store=_PauseStore(provider_started),
    )
    runtime.start_command_watcher(interval_seconds=0.01)
    try:
        execution = asyncio.create_task(
            execute_chat(
                handler=handler,
                request=_request(),
                runtime=runtime,
            )
        )
        await asyncio.wait_for(provider_started.wait(), timeout=0.2)
        with pytest.raises(Exception) as error:
            await asyncio.wait_for(execution, timeout=0.2)
        assert error.type.__name__ == "ConversationPauseRequested"
        adapter.close.assert_awaited_once()
    finally:
        await runtime.stop_command_watcher()


@pytest.mark.asyncio
async def test_execute_chat_preserves_thinking_as_structured_part(monkeypatch):
    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content=None,
            thinking_content="分析中",
            tool_calls=None,
            prompt_tokens=1,
            completion_tokens=1,
            credits_consumed=0,
            finish_reason=None,
        )
        yield SimpleNamespace(
            content="结论",
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=0,
            completion_tokens=0,
            credits_consumed=None,
            finish_reason="stop",
        )

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    budget = SimpleNamespace(stop_reason=None, turns_used=0)

    def use_turn():
        budget.turns_used += 1

    budget.use_turn = use_turn
    prepared = SimpleNamespace(
        adapter=adapter,
        permission=SimpleNamespace(
            need_exit_attachment=False,
            get_reminder=lambda _turn: "",
        ),
        core_tools=[],
        stream_kwargs={},
        tool_context=SimpleNamespace(
            discovered_tools=set(),
            build_context_prompt=lambda: "",
        ),
        messages=[],
        budget=budget,
    )

    async def fake_prepare(**_kwargs):
        return prepared

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    handler = SimpleNamespace(
        org_id=None,
        _adapter=None,
        _calculate_credits=lambda _usage: 0,
    )

    result = await execute_chat(handler=handler, request=_request())

    assert [part.type for part in result.parts] == ["thinking", "text"]
    assert result.parts[0].text == "分析中"
    assert result.parts[1].text == "结论"
