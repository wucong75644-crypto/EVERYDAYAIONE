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
    _execute_tools,
    _read_turn,
    _run_loop,
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
async def test_actor_tool_preview_is_emitted_before_arguments_finish():
    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content=None,
            thinking_content=None,
            tool_calls=[SimpleNamespace(
                index=0,
                id="provider-call-1",
                name="erp_agent",
                arguments_delta="",
            )],
            prompt_tokens=0,
            completion_tokens=0,
            credits_consumed=None,
            finish_reason=None,
        )
        yield SimpleNamespace(
            content=None,
            thinking_content=None,
            tool_calls=[SimpleNamespace(
                index=0,
                id=None,
                name=None,
                arguments_delta='{"query":"近三天订单"}',
            )],
            prompt_tokens=0,
            completion_tokens=0,
            credits_consumed=None,
            finish_reason="tool_calls",
        )

    class Sink:
        def __init__(self):
            self.added = []
            self.updated = []

        async def on_thinking(self, _text):
            return None

        async def on_text(self, _text):
            return None

        async def on_block(self, block):
            self.added.append(dict(block))

        async def on_block_update(self, block):
            self.updated.append(dict(block))

    prepared = SimpleNamespace(
        adapter=SimpleNamespace(stream_chat=stream_chat),
        messages=[],
        stream_kwargs={},
    )
    runtime = ConversationTurnRuntime(
        conversation_id="conv-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
    )
    sink = Sink()
    blocks = []

    _, _, calls, previewed_ids = await _read_turn(
        prepared,
        [],
        asyncio.Event(),
        sink,
        SimpleNamespace(
            text="",
            thinking="",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
            chunk_count=0,
            last_finish_reason=None,
        ),
        blocks,
        runtime,
    )

    assert sink.added == [{
        "type": "tool_step",
        "tool_name": "erp_agent",
        "tool_call_id": "actor-call:turn-1:0",
        "status": "running",
    }]
    assert sink.updated == []
    assert calls[0]["id"] == "actor-call:turn-1:0"
    assert calls[0]["arguments"] == '{"query":"近三天订单"}'
    assert previewed_ids == {"actor-call:turn-1:0"}


@pytest.mark.asyncio
async def test_actor_tool_preview_is_updated_before_tool_execution(monkeypatch):
    order = []

    class Sink:
        def __init__(self):
            self.added = []
            self.updated = []

        async def on_block(self, block):
            self.added.append(dict(block))

        async def on_block_update(self, block):
            order.append("update")
            self.updated.append(dict(block))

    async def execute_tool_calls(*_args, **_kwargs):
        order.append("execute")
        call = _args[0][0]
        return [(call, "查询完成", False, "查询完成")]

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.compact_tool_context",
        lambda **_kwargs: asyncio.sleep(0),
    )
    handler = SimpleNamespace(
        _execute_tool_calls=execute_tool_calls,
        _get_conv_source=lambda _conversation_id: "web",
        _pending_emit_payloads=[],
    )
    call = {
        "id": "actor-call:turn-1:0",
        "name": "erp_agent",
        "arguments": '{"query":"近三天订单"}',
    }
    blocks = [{
        "type": "tool_step",
        "tool_name": "erp_agent",
        "tool_call_id": call["id"],
        "status": "running",
    }]
    prepared = SimpleNamespace(
        messages=[],
        budget=SimpleNamespace(),
        tool_context=SimpleNamespace(
            update_from_result=lambda *_args: None,
        ),
    )
    request = SimpleNamespace(
        task_id="task-1",
        conversation_id="conv-1",
        message_id="message-1",
        user_id="user-1",
    )

    await _execute_tools(
        handler=handler,
        request=request,
        prepared=prepared,
        turn=0,
        turn_text="",
        calls=[call],
        previewed_call_ids={call["id"]},
        cancellation_event=asyncio.Event(),
        sink=Sink(),
        blocks=blocks,
        runtime=None,
    )

    assert order == ["update", "execute"]
    assert len(blocks) == 1


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


@pytest.mark.asyncio
async def test_form_result_stops_tool_loop_before_a_second_model_turn(monkeypatch):
    """表单已发出时，模型不得再生成与表单重复的确认文案。"""
    read_turns = 0

    async def fake_read_turn(*_args, **_kwargs):
        nonlocal read_turns
        read_turns += 1
        return "", "", [{
            "id": "call-1",
            "name": "manage_scheduled_task",
            "arguments": "{}",
        }], set()

    async def fake_execute_tools(*, handler, **_kwargs):
        handler._terminal_form_pending = True

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine._read_turn", fake_read_turn,
    )
    monkeypatch.setattr(
        "services.handlers.chat.execution_engine._execute_tools", fake_execute_tools,
    )

    budget = SimpleNamespace(stop_reason=None, turns_used=0)
    budget.use_turn = lambda: setattr(budget, "turns_used", budget.turns_used + 1)
    prepared = SimpleNamespace(
        budget=budget,
        core_tools=[],
        tool_context=SimpleNamespace(discovered_tools=set()),
        messages=[],
        permission=SimpleNamespace(need_exit_attachment=False),
    )
    handler = SimpleNamespace(org_id="org-1", _terminal_form_pending=False)

    await _run_loop(
        handler=handler,
        request=_request(),
        prepared=prepared,
        cancellation_event=asyncio.Event(),
        sink=SimpleNamespace(),
        totals=SimpleNamespace(),
        blocks=[],
        runtime=None,
    )

    assert read_turns == 1
