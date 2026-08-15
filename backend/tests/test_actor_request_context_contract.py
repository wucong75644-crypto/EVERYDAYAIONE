"""Conversation Actor 的 ChatHandler 上下文装配合同。"""

import asyncio
from types import SimpleNamespace

import pytest

from schemas.message import TextPart
from services.conversation_execution import GenerationClaim
from services.handlers.chat.execution_engine import ChatExecutionResult
from services.handlers.chat.executor import ChatGenerationExecutor


class _Query:
    def __init__(self, row):
        self._row = row

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._row)


class _DB:
    def __init__(self):
        self.rows = {
            "messages": {
                "id": "input-1", "conversation_id": "conv-1",
                "turn_id": "turn-1", "role": "user",
                "content": [{"type": "text", "text": "test"}],
            },
            "conversations": {
                "id": "conv-1", "org_id": "org-1", "user_id": "user-1",
                "source": "web", "scope_type": "user", "scope_id": "user-1",
            },
        }

    def table(self, name):
        return _Query(self.rows[name])


def _claim() -> GenerationClaim:
    return GenerationClaim(
        task_id="task-1", execution_token="token-1",
        conversation_id="conv-1", turn_id="turn-1",
        input_message_id="input-1", base_context_revision=0,
        context_through_message_id="input-1", execution_attempt=1,
        execution_mode="serial", user_id="user-1", org_id="org-1",
    )


@pytest.mark.asyncio
async def test_default_factory_injects_actor_request_context(monkeypatch):
    captured = {}

    async def fake_execute_chat(**kwargs):
        captured["handler"] = kwargs["handler"]
        return ChatExecutionResult(
            parts=[TextPart(text="完成")], content_blocks=[], usage={},
            credits_cost=0, tool_digest=None,
        )

    monkeypatch.setattr(
        "services.handlers.chat.executor.execute_chat", fake_execute_chat,
    )
    task = {
        "id": "task-1", "conversation_id": "conv-1",
        "assistant_message_id": "output-1", "user_id": "user-1",
        "org_id": "org-1", "model_id": "auto", "request_params": {},
    }

    await ChatGenerationExecutor(
        _DB(), handler_db_factory=lambda: object(),
    ).execute(task, _claim(), asyncio.Event())

    request_ctx = captured["handler"].request_ctx
    assert (request_ctx.user_id, request_ctx.org_id, request_ctx.request_id) == (
        "user-1", "org-1", "task-1",
    )
