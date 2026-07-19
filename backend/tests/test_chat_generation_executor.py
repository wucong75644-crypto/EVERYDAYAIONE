"""ChatGenerationExecutor 单元测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from schemas.message import ImagePart, TextPart
from services.conversation_execution import GenerationClaim
from services.handlers.chat.execution_engine import ChatExecutionResult
from services.handlers.chat.executor import ChatGenerationExecutor
from services.agent.runtime.artifacts import normalize_tool_result


class _Query:
    def __init__(self, row, updates=None):
        self._row = row
        self._updates = updates

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def update(self, values):
        if self._updates is not None:
            self._updates.append(values)
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._row)


class _DB:
    def __init__(self, row, conversation=None):
        self.updates = []
        self.rows = {
            "messages": row,
            "conversations": conversation or {
                "id": "conv-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "source": "wecom",
                "scope_type": "user",
                "scope_id": "user-1",
            },
        }

    def table(self, name):
        return _Query(self.rows.get(name), self.updates)


def _claim() -> GenerationClaim:
    return GenerationClaim(
        task_id="task-1",
        execution_token="token-1",
        conversation_id="conv-1",
        turn_id="turn-1",
        input_message_id="input-1",
        base_context_revision=4,
        context_through_message_id="closed-1",
        execution_attempt=1,
        execution_mode="serial",
    )


def _task() -> dict:
    return {
        "id": "task-1",
        "conversation_id": "conv-1",
        "assistant_message_id": "output-1",
        "user_id": "user-1",
        "org_id": "org-1",
        "model_id": "auto",
        "request_params": {"permission_mode": "auto"},
    }


@pytest.mark.asyncio
async def test_executor_loads_multimodal_content_from_input_message(monkeypatch):
    row = {
        "id": "input-1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "role": "user",
        "content": json.dumps([
            {"type": "text", "text": "分析图片"},
            {"type": "image", "url": "https://cdn.example.com/a.png"},
        ]),
    }
    captured = {}

    async def fake_execute_chat(**kwargs):
        captured["request"] = kwargs["request"]
        draft = normalize_tool_result(
            "完整结果",
            tool_call_id="call-1",
            tool_name="erp_agent",
        )
        return ChatExecutionResult(
            parts=[TextPart(text="完成")],
            content_blocks=[{"type": "text", "text": "完成"}],
            usage={"prompt_tokens": 3, "completion_tokens": 2},
            credits_cost=2,
            tool_digest=None,
            artifact_drafts=(draft,),
            context_receipts=[{
                "model_step": 0,
                "base_revision": 4,
                "plan_hash": "plan-hash",
                "model": "qwen3.5-plus",
                "block_refs": [],
                "estimated_tokens": 10,
                "provider_tokens": None,
                "trimmed_refs": [],
                "compaction_id": None,
            }],
            compaction={
                "id": "compaction-1",
                "source_hash": "source-hash",
            },
        )

    monkeypatch.setattr(
        "services.handlers.chat.executor.execute_chat",
        fake_execute_chat,
    )
    handler_db = object()
    received_db = {}

    def build_handler(db):
        received_db["value"] = db
        received_db["handler"] = SimpleNamespace(org_id=None)
        return received_db["handler"]

    executor = ChatGenerationExecutor(
        _DB(row),
        build_handler,
        handler_db_factory=lambda: handler_db,
    )

    outcome = await executor.execute(_task(), _claim(), asyncio.Event())

    assert isinstance(captured["request"].content[0], TextPart)
    assert isinstance(captured["request"].content[1], ImagePart)
    assert captured["request"].context_anchor.base_revision == 4
    assert received_db["value"] is handler_db
    assert received_db["handler"].org_id == "org-1"
    assert received_db["handler"].execution_scope.context_scope == "user"
    assert received_db["handler"]._workspace_user_id == "user-1"
    assert received_db["handler"]._personal_context_allowed is True
    assert outcome.result_content == [{"type": "text", "text": "完成"}]
    assert outcome.credits_cost == 2
    assert outcome.artifacts[0]["storage_kind"] == "inline"
    assert outcome.artifacts[0]["inline_content"] == "完整结果"
    assert outcome.context_items[0]["item_type"] == "user"
    assert outcome.context_items[1]["item_type"] == "assistant"
    assert outcome.context_receipts[0]["plan_hash"] == "plan-hash"
    assert outcome.compaction == {
        "id": "compaction-1",
        "source_hash": "source-hash",
    }


@pytest.mark.asyncio
async def test_executor_rejects_input_message_scope_mismatch():
    row = {
        "id": "input-1",
        "conversation_id": "other",
        "turn_id": "turn-1",
        "role": "user",
        "content": [{"type": "text", "text": "test"}],
    }
    executor = ChatGenerationExecutor(
        _DB(row),
        AsyncMock(),
        handler_db_factory=lambda: object(),
    )

    with pytest.raises(RuntimeError, match="ACTOR_INPUT_MESSAGE_SCOPE_MISMATCH"):
        await executor.execute(_task(), _claim(), asyncio.Event())


@pytest.mark.asyncio
async def test_executor_propagates_generation_error(monkeypatch):
    row = {
        "id": "input-1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "role": "user",
        "content": [{"type": "text", "text": "test"}],
    }

    async def fail_execute_chat(**_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(
        "services.handlers.chat.executor.execute_chat",
        fail_execute_chat,
    )
    executor = ChatGenerationExecutor(
        _DB(row),
        lambda _db: SimpleNamespace(org_id=None),
        handler_db_factory=lambda: object(),
    )

    with pytest.raises(RuntimeError, match="provider down"):
        await executor.execute(_task(), _claim(), asyncio.Event())


@pytest.mark.asyncio
async def test_executor_retries_smart_mode_inside_actor(monkeypatch):
    from services.adapters.factory import DEFAULT_MODEL_ID
    from services.intent_router import RetryContext
    from schemas.message import GenerationType

    row = {
        "id": "input-1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "role": "user",
        "content": [{"type": "text", "text": "test"}],
    }
    task = _task()
    task["client_task_id"] = "client-1"
    task["request_params"] = {"_is_smart_mode": True}
    requests = []

    async def fake_execute_chat(**kwargs):
        requests.append(kwargs["request"])
        if len(requests) == 1:
            raise RuntimeError("provider down")
        return ChatExecutionResult(
            parts=[TextPart(text="重试成功")],
            content_blocks=[],
            usage={},
            credits_cost=0,
            tool_digest=None,
        )

    retry_context = RetryContext(
        is_smart_mode=True,
        original_content="test",
        generation_type=GenerationType.CHAT,
    )
    retry_context.add_failure(DEFAULT_MODEL_ID, "provider down")
    handler = SimpleNamespace(
        org_id=None,
        _build_retry_context=lambda **_kwargs: retry_context,
        _route_retry=AsyncMock(
            return_value=SimpleNamespace(recommended_model="fallback-model"),
        ),
        _send_retry_notification=AsyncMock(),
    )
    db = _DB(row)
    monkeypatch.setattr(
        "services.handlers.chat.executor.execute_chat",
        fake_execute_chat,
    )
    executor = ChatGenerationExecutor(
        db,
        lambda _db: handler,
        handler_db_factory=lambda: object(),
    )

    outcome = await executor.execute(task, _claim(), asyncio.Event())

    assert outcome.result_content[0]["text"] == "重试成功"
    assert [request.model_id for request in requests] == [
        DEFAULT_MODEL_ID,
        "fallback-model",
    ]
    handler._send_retry_notification.assert_awaited_once_with(
        "client-1",
        "conv-1",
        "user-1",
        "fallback-model",
        1,
    )
    assert db.updates == [
        {
            "model_id": "fallback-model",
            "request_params": {
                "_is_smart_mode": True,
                "_retry_from_model": DEFAULT_MODEL_ID,
            },
        }
    ]


@pytest.mark.asyncio
async def test_executor_rejects_invalid_input_content():
    row = {
        "id": "input-1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "role": "user",
        "content": [{"type": "unknown"}],
    }
    executor = ChatGenerationExecutor(
        _DB(row),
        lambda _db: SimpleNamespace(org_id=None),
        handler_db_factory=lambda: object(),
    )

    with pytest.raises(ValueError):
        await executor.execute(_task(), _claim(), asyncio.Event())


@pytest.mark.asyncio
async def test_executor_injects_task_scoped_sink(monkeypatch):
    row = {
        "id": "input-1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "role": "user",
        "content": [{"type": "text", "text": "test"}],
    }
    sink = object()
    captured = {}

    async def fake_execute_chat(**kwargs):
        captured["sink"] = kwargs["sink"]
        return ChatExecutionResult(
            parts=[TextPart(text="完成")],
            content_blocks=[],
            usage={},
            credits_cost=0,
            tool_digest=None,
        )

    monkeypatch.setattr(
        "services.handlers.chat.executor.execute_chat",
        fake_execute_chat,
    )
    executor = ChatGenerationExecutor(
        _DB(row),
        lambda _db: SimpleNamespace(org_id=None),
        handler_db_factory=lambda: object(),
        sink_factory=lambda task, claim, event: sink,
    )

    await executor.execute(_task(), _claim(), asyncio.Event())

    assert captured["sink"] is sink


@pytest.mark.asyncio
async def test_executor_builds_channel_scope_from_conversation(monkeypatch):
    row = {
        "id": "input-1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "role": "user",
        "content": [{"type": "text", "text": "分析群文件"}],
    }
    conversation = {
        "id": "conv-1",
        "org_id": "org-1",
        "user_id": None,
        "source": "wecom",
        "scope_type": "channel",
        "scope_id": "group-1",
    }
    task = _task()
    task["delivery_context"] = {
        "channel": "wecom",
        "chattype": "group",
        "corp_id": "corp-1",
        "chatid": "group-1",
    }
    captured = {}

    async def fake_execute_chat(**kwargs):
        captured["request"] = kwargs["request"]
        return ChatExecutionResult(
            parts=[TextPart(text="完成")],
            content_blocks=[],
            usage={},
            credits_cost=0,
            tool_digest=None,
        )

    monkeypatch.setattr(
        "services.handlers.chat.executor.execute_chat",
        fake_execute_chat,
    )
    handler = SimpleNamespace(org_id=None)
    executor = ChatGenerationExecutor(
        _DB(row, conversation),
        lambda _db: handler,
        handler_db_factory=lambda: object(),
    )

    await executor.execute(task, _claim(), asyncio.Event())

    scope = captured["request"].execution_scope
    assert scope.context_scope == "channel"
    assert scope.actor_user_id == "user-1"
    assert scope.personal_context_allowed is False
    assert scope.workspace_owner_id.startswith("channels/wecom/")
    assert handler._workspace_user_id == scope.workspace_owner_id


@pytest.mark.asyncio
async def test_executor_rejects_channel_delivery_scope_mismatch():
    row = {
        "id": "input-1",
        "conversation_id": "conv-1",
        "turn_id": "turn-1",
        "role": "user",
        "content": [{"type": "text", "text": "test"}],
    }
    conversation = {
        "id": "conv-1",
        "org_id": "org-1",
        "user_id": None,
        "source": "wecom",
        "scope_type": "channel",
        "scope_id": "group-1",
    }
    task = _task()
    task["delivery_context"] = {
        "channel": "wecom",
        "chattype": "group",
        "corp_id": "corp-1",
        "chatid": "other-group",
    }
    executor = ChatGenerationExecutor(
        _DB(row, conversation),
        lambda _db: SimpleNamespace(org_id=None),
        handler_db_factory=lambda: object(),
    )

    with pytest.raises(RuntimeError, match="ACTOR_EXECUTION_SCOPE_MISMATCH"):
        await executor.execute(task, _claim(), asyncio.Event())
