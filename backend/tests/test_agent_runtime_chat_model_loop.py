"""Runtime ownership tests for the conversational ModelLoop bridge."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.handlers.chat.execution_engine import ChatExecutionRequest, execute_chat
from schemas.message import TextPart


def _request() -> ChatExecutionRequest:
    return ChatExecutionRequest(
        content=[TextPart(text="你好")], user_id="user-1",
        conversation_id="conv-1", task_id="task-1", message_id="out-1",
        model_id="model-1", context_anchor=object(),
    )


@pytest.mark.asyncio
async def test_chat_execution_delegates_turns_to_runtime_model_loop(monkeypatch):
    adapter = SimpleNamespace(close=AsyncMock())
    prepared = SimpleNamespace(
        adapter=adapter,
        permission=SimpleNamespace(need_exit_attachment=False),
        core_tools=[], stream_kwargs={},
        tool_context=SimpleNamespace(discovered_tools=set()), messages=[],
        budget=SimpleNamespace(stop_reason=None, turns_used=0),
    )
    called = False

    async def fake_prepare(**_kwargs):
        return prepared

    async def fake_runtime_run(self, **kwargs):
        nonlocal called
        called = True
        kwargs["totals"].text = "runtime result"
        kwargs["blocks"].append({"type": "text", "text": "runtime result"})
        prepared.budget.stop_reason = "runtime"

    monkeypatch.setattr(
        "services.handlers.chat.execution_engine.prepare_chat_stream",
        fake_prepare,
    )
    monkeypatch.setattr(
        "services.agent.runtime.application.chat_model_loop.RuntimeChatModelLoop.run",
        fake_runtime_run,
    )
    handler = SimpleNamespace(
        org_id=None, _adapter=None, _calculate_credits=lambda _usage: 0,
    )

    await execute_chat(handler=handler, request=_request())

    assert called is True
    adapter.close.assert_awaited_once()
