"""电商图两阶段原子准备测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes.message_ecom_preparation import prepare_and_start_ecom_generation
from schemas.message import GenerateRequest, GenerationType, TextPart
from services.generation_lifecycle import GenerationPreparation


class _Lifecycle:
    calls = []

    def __init__(self, db):
        pass

    def prepare(self, **kwargs):
        self.__class__.calls.append(kwargs)
        return GenerationPreparation(
            request_id=kwargs["request_id"], conversation_id=kwargs["conversation_id"],
            turn_id=kwargs["turn_id"], input_message_id=kwargs["input_message"]["id"],
            output_message_id=kwargs["output_message"]["id"],
            base_context_revision=1, context_through_message_id=None,
            task_ids=tuple(task["id"] for task in kwargs["tasks"]),
            already_prepared=False,
        )


class _Handler:
    def __init__(self):
        self.start = AsyncMock(return_value="client-task")
        self.preflight = MagicMock()
        self.prepare_phase2_params = MagicMock()

    def _serialize_params(self, params):
        return dict(params)


class _ConversationService:
    async def get_conversation(self, conversation_id, user_id, org_id):
        return {"id": conversation_id, "context_summary": "summary"}


def _body(params=None):
    return GenerateRequest(
        content=[TextPart(text="plan")], generation_type=GenerationType.IMAGE_ECOM,
        params=params or {}, client_request_id="client-request",
        client_task_id="client-task",
        assistant_message_id="00000000-0000-0000-0000-000000000002",
    )


@pytest.mark.asyncio
async def test_phase1_prepares_running_task_before_handler(monkeypatch):
    _Lifecycle.calls.clear()
    monkeypatch.setattr(
        "api.routes.message_ecom_preparation.GenerationLifecycle", _Lifecycle,
    )
    monkeypatch.setattr(
        "api.routes.message_ecom_preparation.record_user_activity",
        lambda *args, **kwargs: None,
    )
    handler = _Handler()

    response = await prepare_and_start_ecom_generation(
        db=object(), handler=handler, conversation_service=_ConversationService(),
        conversation_id="conv-1", user_id="user-1", org_id="org-1",
        request_id="request-row", body=_body(),
    )

    task = _Lifecycle.calls[0]["tasks"][0]
    assert task["status"] == "running"
    assert task["external_task_id"] == "client-task"
    assert handler.start.await_args.kwargs["metadata"].prepared_task_id == task["id"]
    assert response.generation_type == GenerationType.IMAGE_ECOM.value


@pytest.mark.asyncio
async def test_phase2_delegates_to_atomic_image_batch(monkeypatch):
    delegated = AsyncMock(return_value=SimpleNamespace(generation_type="image_ecom"))
    monkeypatch.setattr(
        "api.routes.message_ecom_preparation.prepare_and_start_image_generation",
        delegated,
    )
    handler = _Handler()
    body = _body({"image_task_meta": [{"prompt": "hero"}]})

    await prepare_and_start_ecom_generation(
        db=object(), handler=handler, conversation_service=_ConversationService(),
        conversation_id="conv-1", user_id="user-1", org_id=None,
        request_id="request-row", body=body,
    )

    handler.prepare_phase2_params.assert_called_once_with(body.content, body.params)
    assert delegated.await_args.kwargs["response_generation_type"] == GenerationType.IMAGE_ECOM
