"""通道无关 Chat 执行内核单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from schemas.message import TextPart
from services.handlers.chat.execution_engine import (
    ChatExecutionRequest,
    _last_tool_output,
    execute_chat,
)
from services.handlers.chat.execution_sink import CollectingExecutionSink


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


def test_last_tool_output_uses_latest_tool_block_and_limits_size():
    blocks = [
        {"type": "tool_result", "output": "旧结果"},
        {"type": "text", "text": "中间文字"},
        {"type": "tool_step", "output": "新" * 3000},
    ]

    result = _last_tool_output(blocks)

    assert result == "新" * 2000


@pytest.mark.asyncio
async def test_execute_chat_collects_usage_and_closes_adapter(monkeypatch):
    provider_payload = {}

    async def stream_chat(**_kwargs):
        provider_payload.update(_kwargs)
        yield SimpleNamespace(
            content="你好",
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=3,
            completion_tokens=2,
            cached_tokens=2,
            cache_creation_tokens=1,
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

    assert provider_payload["messages"] == prepared.messages
    assert provider_payload["messages"] is not prepared.messages
    assert provider_payload["tools"] == prepared.core_tools
    assert provider_payload["tools"] is not prepared.core_tools
    assert result.parts[0].text == "你好"
    assert result.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "cached_tokens": 2,
        "cache_creation_tokens": 1,
    }
    assert len(result.context_receipts) == 1
    assert result.context_receipts[0]["model_step"] == 0
    assert result.context_receipts[0]["plan_hash"]
    assert result.context_receipts[0]["context_plan_projection_match"] is True
    assert result.context_receipts[0]["context_plan_hash"] == (
        result.context_receipts[0]["plan_hash"]
    )
    assert result.context_receipts[0]["provider_tokens"] == 3
    assert result.context_receipts[0]["provider_usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "cached_tokens": 2,
        "cache_creation_tokens": 1,
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
async def test_execute_chat_stops_on_channel_cancel_and_interrupts_kernel(
    monkeypatch,
):
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
    prepared.budget.use_turn = lambda: setattr(
        prepared.budget,
        "turns_used",
        prepared.budget.turns_used + 1,
    )

    async def fake_prepare(**_kwargs):
        return prepared

    class _CancelledSink(CollectingExecutionSink):
        def is_cancelled(self):
            return True

    kernel = SimpleNamespace(interrupt=MagicMock())
    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    monkeypatch.setattr(
        "services.sandbox.kernel_manager.get_kernel_manager",
        lambda: kernel,
    )
    handler = SimpleNamespace(org_id=None, _adapter=None)

    with pytest.raises(asyncio.CancelledError):
        await execute_chat(
            handler=handler,
            request=_request(),
            sink=_CancelledSink(),
        )

    kernel.interrupt.assert_called_once_with("conv-1")
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
async def test_tool_turn_passes_step_and_remaining_budget_to_validation(
    monkeypatch,
):
    from services.agent.agent_result import AgentResult
    from services.agent.runtime.runtime_state import RuntimeState

    stream_calls = 0

    async def stream_chat(**_kwargs):
        nonlocal stream_calls
        stream_calls += 1
        if stream_calls == 1:
            yield SimpleNamespace(
                content=None,
                thinking_content=None,
                tool_calls=[SimpleNamespace(
                    index=0,
                    id="call-1",
                    name="code_execute",
                    arguments_delta='{"code":"bad"}',
                )],
                prompt_tokens=1,
                completion_tokens=1,
                credits_consumed=None,
                finish_reason="tool_calls",
            )
            return
        yield SimpleNamespace(
            content="已结束",
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=1,
            completion_tokens=1,
            credits_consumed=None,
            finish_reason="stop",
        )

    state = RuntimeState(task_id="task-1")
    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    budget = SimpleNamespace(
        stop_reason=None,
        turns_used=0,
        turns_remaining=1,
    )
    budget.use_turn = lambda: setattr(
        budget,
        "turns_used",
        budget.turns_used + 1,
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
            update_from_result=lambda *_args: None,
        ),
        messages=[],
        budget=budget,
        runtime_state=state,
        context_budget=SimpleNamespace(),
    )

    async def fake_prepare(**_kwargs):
        return prepared

    async def execute_tools(*_args, **_kwargs):
        return [(
            {
                "id": "call-1",
                "name": "code_execute",
                "arguments": '{"code":"bad"}',
            },
            AgentResult(
                summary="执行失败",
                status="error",
                metadata={"retryable": True},
            ),
            True,
            "执行失败",
        )]

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.compact_tool_context",
        AsyncMock(),
    )
    handler = SimpleNamespace(
        org_id=None,
        _adapter=None,
        _execute_tool_calls=execute_tools,
        _calculate_credits=lambda _usage: 0,
    )

    result = await execute_chat(handler=handler, request=_request())

    receipt = state.validation.receipts[0]
    assert receipt.model_step == 1
    assert receipt.decision.value == "wrap_up"
    assert result.parts[-1].text == "已结束"


@pytest.mark.asyncio
async def test_execute_chat_does_not_recheck_final_text_against_evidence(
    monkeypatch,
):
    from services.agent.agent_result import AgentResult
    from services.agent.runtime.artifact_collector import collect_tool_result
    from services.agent.runtime.runtime_state import RuntimeState

    async def stream_chat(**_kwargs):
        yield SimpleNamespace(
            content="模型最终回答1457单",
            thinking_content=None,
            tool_calls=None,
            prompt_tokens=1,
            completion_tokens=1,
            credits_consumed=None,
            finish_reason="stop",
        )

    state = RuntimeState.observing()
    state.ledger.record(
        collect_tool_result(
            AgentResult(
                summary="结构化数据",
                data=[{"有效订单": 1053}],
                source="erp_agent",
            ),
            tool_call_id="erp-1",
        )[0]
    )
    adapter = SimpleNamespace(stream_chat=stream_chat, close=AsyncMock())
    budget = SimpleNamespace(stop_reason=None, turns_used=0)
    budget.use_turn = lambda: setattr(
        budget,
        "turns_used",
        budget.turns_used + 1,
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
        runtime_state=state,
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

    assert result.parts[-1].text == "模型最终回答1457单"
    assert budget.turns_used == 1
