"""通道无关 Chat 执行内核单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schemas.message import TextPart
from services.handlers.chat.execution_engine import (
    ChatExecutionRequest,
    execute_chat,
)


def _evidence_runtime_state():
    from services.agent.agent_result import AgentResult
    from services.agent.runtime.artifact_collector import collect_tool_result
    from services.agent.runtime.runtime_state import RuntimeState

    state = RuntimeState.observing()
    result = AgentResult(
        summary="沙盒计算完成",
        data=[{"有效订单合计": 1056}],
        source="code_execute",
    )
    state.ledger.record(
        collect_tool_result(result, tool_call_id="compute-1")[0]
    )
    return state


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

    result = await execute_chat(handler=handler, request=_request())

    assert result.parts[0].text == "你好"
    assert result.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
    }
    assert result.credits_cost == 2
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_chat_stops_before_provider_when_cancelled(monkeypatch):
    adapter = SimpleNamespace(close=AsyncMock())
    prepared = SimpleNamespace(
        adapter=adapter,
        permission=SimpleNamespace(need_exit_attachment=False),
        core_tools=[],
        stream_kwargs={},
        tool_context=SimpleNamespace(
            discovered_tools=set(),
            build_context_prompt=lambda: "",
        ),
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


@pytest.mark.asyncio
async def test_execute_chat_retries_invalid_draft_then_returns_model_correction(
    monkeypatch,
):
    responses = iter(
        ["我猜是1457单", "重新计算后有效订单合计是1,056单"]
    )

    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content=next(responses),
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=1,
            completion_tokens=1,
            credits_consumed=None,
            finish_reason="stop",
        )

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    budget = SimpleNamespace(stop_reason=None, turns_used=0)
    budget.use_turn = lambda: setattr(
        budget, "turns_used", budget.turns_used + 1
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
        budget=budget,
        runtime_state=_evidence_runtime_state(),
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

    assert result.parts[-1].text == "重新计算后有效订单合计是1,056单"
    assert "1457" not in result.parts[-1].text
    assert budget.turns_used == 2
    assert "evidence_validation_error" in prepared.messages[-1]["content"]


@pytest.mark.asyncio
async def test_execute_chat_blocks_after_repeated_unsupported_claims(monkeypatch):
    responses = iter(["1457单", "1457单", "1457单"])

    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content=next(responses),
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=1,
            completion_tokens=1,
            credits_consumed=None,
            finish_reason="stop",
        )

    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    budget = SimpleNamespace(stop_reason=None, turns_used=0)
    budget.use_turn = lambda: setattr(
        budget, "turns_used", budget.turns_used + 1
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
        budget=budget,
        runtime_state=_evidence_runtime_state(),
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

    assert "未能通过证据一致性校验" in result.parts[-1].text
    assert budget.turns_used == 3
