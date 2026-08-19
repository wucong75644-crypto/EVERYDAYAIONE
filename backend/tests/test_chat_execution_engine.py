"""通道无关 Chat 执行内核单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from psycopg.types.json import Jsonb

from schemas.message import TextPart
from services.conversation_commands import CommandType, ConversationCommand
from services.conversation_state import ConversationPauseRequested
from services.handlers.chat.execution_engine import (
    ChatExecutionRequest,
    _stable_actor_tool_call_id,
    execute_chat,
)
from services.conversation_commands import SafePoint
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


def test_actor_tool_call_id_is_stable_for_same_turn_and_arguments():
    first = _stable_actor_tool_call_id("turn-1", 0, "erp_execute", '{"a":1}')
    second = _stable_actor_tool_call_id("turn-1", 0, "erp_execute", '{"a":1}')
    different_args = _stable_actor_tool_call_id("turn-1", 0, "erp_execute", '{"a":2}')

    assert first == second
    assert first != different_args
    assert first.startswith("actor-call:turn-1:0:")


@pytest.mark.asyncio
async def test_pause_checkpoint_wraps_resume_state_as_jsonb(monkeypatch):
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

    class CheckpointDB:
        def __init__(self):
            self.calls = []

        def rpc(self, name, params):
            self.calls.append((name, params))
            return SimpleNamespace(
                execute=AsyncMock(
                    return_value=SimpleNamespace(
                        data={"outcome": "saved", "version": 1},
                    )
                )
            )

    db = CheckpointDB()
    handler = SimpleNamespace(db=db, org_id="org-1", _adapter=None)
    runtime = ConversationTurnRuntime(
        conversation_id="conv-1",
        task_id="task-1",
        turn_id="turn-1",
        cancellation_event=asyncio.Event(),
        execution_token="token-1",
    )
    runtime.push(ConversationCommand(
        command_id="pause-1",
        command_type=CommandType.PAUSE,
        conversation_id="conv-1",
        task_id="task-1",
        turn_id="turn-1",
    ))

    with pytest.raises(ConversationPauseRequested):
        await execute_chat(
            handler=handler,
            request=_request(),
            runtime=runtime,
        )

    assert db.calls[0][0] == "save_generation_checkpoint"
    state = db.calls[0][1]["p_state"]
    assert isinstance(state, Jsonb)
    assert state.obj["version"] == 1
    adapter.close.assert_awaited_once()


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
    assert runtime.last_safe_point is SafePoint.BEFORE_COMMIT
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
