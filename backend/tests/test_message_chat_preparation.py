"""Web Chat 统一生成准备测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from api.routes.message_chat_preparation import prepare_and_start_chat_generation
from schemas.message import GenerateRequest, MessageOperation, TextPart
from services.generation_lifecycle import GenerationPreparation
from services.handlers.chat.actor_enqueue import stable_actor_task_id


class _Handler:
    def __init__(self) -> None:
        self.start = AsyncMock(return_value="client-task")

    def _extract_text_content(self, content):
        return content[0].text

    def _serialize_params(self, params):
        return dict(params)

    def _build_task_data(self, **kwargs):
        return {
            "id": "discarded",
            "external_task_id": kwargs["task_id"],
            "client_task_id": kwargs["metadata"].client_task_id,
            "user_id": kwargs["user_id"],
            "org_id": "org-1",
            "conversation_id": kwargs["conversation_id"],
            "type": kwargs["task_type"],
            "status": kwargs["status"],
            "model_id": kwargs["model_id"],
            "assistant_message_id": kwargs["message_id"],
            "request_params": kwargs["request_params"],
        }


class _Lifecycle:
    def __init__(self, result: GenerationPreparation) -> None:
        self.result = result
        self.calls = []

    def prepare(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _body(operation: MessageOperation = MessageOperation.SEND) -> GenerateRequest:
    return GenerateRequest(
        operation=operation,
        content=[TextPart(text="hello")],
        model="model-1",
        client_request_id="client-request",
        client_task_id="client-task",
        assistant_message_id="00000000-0000-0000-0000-000000000002",
        original_message_id=(
            "00000000-0000-0000-0000-000000000002"
            if operation == MessageOperation.RETRY else None
        ),
        created_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_send_prepares_stable_messages_and_reuses_task_anchor(monkeypatch):
    task_id = stable_actor_task_id(
        user_id="user-1", conversation_id="conversation-1",
        external_task_id="client-task",
    )
    result = GenerationPreparation(
        request_id="request-row", conversation_id="conversation-1",
        turn_id="turn-1", input_message_id="input-1",
        output_message_id="00000000-0000-0000-0000-000000000002",
        base_context_revision=3, context_through_message_id="through-1",
        task_ids=(task_id,), already_prepared=False,
    )
    lifecycle = _Lifecycle(result)
    monkeypatch.setattr(
        "api.routes.message_chat_preparation.GenerationLifecycle",
        lambda db: lifecycle,
    )
    monkeypatch.setattr(
        "api.routes.message_chat_preparation.record_user_activity", lambda *a, **k: None,
    )
    handler = _Handler()

    response = await prepare_and_start_chat_generation(
        db=object(), handler=handler, conversation_id="conversation-1",
        user_id="user-1", org_id="org-1", request_id="request-row",
        body=_body(),
    )

    prepared = lifecycle.calls[0]
    assert prepared["turn_id"] is not None
    assert prepared["input_message"]["id"] is not None
    assert prepared["tasks"][0]["id"] == task_id
    assert prepared["tasks"][0]["delivery_context"] == {"actor": True, "channel": "web"}
    metadata = handler.start.await_args.kwargs["metadata"]
    assert metadata.context_anchor.task_id == task_id
    assert response.user_message.id == "input-1"
    assert response.assistant_message.reply_to_message_id == "input-1"


@pytest.mark.asyncio
async def test_retry_uses_database_resolved_anchor_without_new_input(monkeypatch):
    task_id = stable_actor_task_id(
        user_id="user-1", conversation_id="conversation-1",
        external_task_id="client-task",
    )
    result = GenerationPreparation(
        request_id="request-row", conversation_id="conversation-1",
        turn_id="existing-turn", input_message_id="existing-input",
        output_message_id="00000000-0000-0000-0000-000000000002",
        base_context_revision=4, context_through_message_id=None,
        task_ids=(task_id,), already_prepared=False,
    )
    lifecycle = _Lifecycle(result)
    monkeypatch.setattr(
        "api.routes.message_chat_preparation.GenerationLifecycle", lambda db: lifecycle,
    )
    handler = _Handler()

    response = await prepare_and_start_chat_generation(
        db=object(), handler=handler, conversation_id="conversation-1",
        user_id="user-1", org_id=None, request_id="request-row",
        body=_body(MessageOperation.RETRY),
    )

    prepared = lifecycle.calls[0]
    assert prepared["turn_id"] is None
    assert prepared["input_message"] == {}
    assert response.user_message is None
    assert response.assistant_message.turn_id == "existing-turn"
