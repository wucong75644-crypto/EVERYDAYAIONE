"""Web 图片原子准备与已准备 task 提交测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes.message_image_preparation import prepare_and_start_image_generation
from schemas.message import GenerateRequest, GenerationType, TextPart
from services.generation_lifecycle import GenerationPreparation
from services.handlers.image_prepared_submission import submit_prepared_image_task


class _Lifecycle:
    instances = []

    def __init__(self, db) -> None:
        self.calls = []
        self.attach_calls = []
        self.fail_calls = []
        self.__class__.instances.append(self)

    def prepare(self, **kwargs):
        self.calls.append(kwargs)
        return GenerationPreparation(
            request_id=kwargs["request_id"],
            conversation_id=kwargs["conversation_id"], turn_id="turn-1",
            input_message_id="input-1",
            output_message_id=kwargs["output_message"]["id"],
            base_context_revision=1, context_through_message_id=None,
            task_ids=tuple(task["id"] for task in kwargs["tasks"]),
            already_prepared=False,
        )

    def attach_external_task(self, **kwargs):
        self.attach_calls.append(kwargs)

    def fail_prepared_task(self, **kwargs):
        self.fail_calls.append(kwargs)


class _Handler:
    def __init__(self) -> None:
        self.db = MagicMock()
        self.org_id = "org-1"
        self.preflight = MagicMock()
        self.start = AsyncMock(return_value="client-task")
        self._lock_credits = MagicMock(side_effect=["tx-1", "tx-2"])
        self._refund_credits = MagicMock()
        self._route_retry = AsyncMock(
            return_value=SimpleNamespace(recommended_model="retry-model")
        )
        self._build_callback_url = MagicMock(return_value="https://callback")

    def _extract_image_urls(self, content):
        return []

    def _extract_text_content(self, content):
        return content[0].text

    def _serialize_params(self, params):
        return dict(params)


class _ConversationService:
    async def get_conversation(self, conversation_id, user_id, org_id):
        return {"id": conversation_id, "context_summary": "summary"}


def _body(num_images: int = 2) -> GenerateRequest:
    return GenerateRequest(
        content=[TextPart(text="cat")], generation_type=GenerationType.IMAGE,
        model="image-model", params={"num_images": num_images},
        client_request_id="client-request", client_task_id="client-task",
        assistant_message_id="00000000-0000-0000-0000-000000000002",
    )


@pytest.mark.asyncio
async def test_image_batch_is_prepared_before_handler_start(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "api.routes.message_image_preparation.GenerationLifecycle", _Lifecycle,
    )
    monkeypatch.setattr(
        "api.routes.message_image_preparation.resolve_image_generation_settings",
        lambda **kwargs: {
            "model_id": "image-model", "aspect_ratio": "1:1",
            "resolution": None, "num_images": 2, "total_credits": 10,
        },
    )
    monkeypatch.setattr(
        "api.routes.message_image_preparation.record_user_activity",
        lambda *args, **kwargs: None,
    )
    handler = _Handler()

    response = await prepare_and_start_image_generation(
        db=object(), handler=handler,
        conversation_service=_ConversationService(), conversation_id="conv-1",
        user_id="user-1", org_id="org-1", request_id="request-row",
        body=_body(),
    )

    prepared = _Lifecycle.instances[0].calls[0]
    assert len(prepared["tasks"]) == 2
    assert {task["status"] for task in prepared["tasks"]} == {"preparing"}
    assert len(handler.start.await_args.kwargs["metadata"].prepared_task_ids) == 2
    assert response.user_message.id == "input-1"


class _Adapter:
    provider = SimpleNamespace(value="kie")

    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error

    async def generate(self, **kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(task_id=self.result)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_prepared_submission_locks_and_attaches_same_local_task(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.image_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    handler = _Handler()

    result = await submit_prepared_image_task(
        handler=handler, local_task_id="local-1", adapter=_Adapter("external-1"),
        index=0, batch_id="batch-1", generate_kwargs={"prompt": "cat"},
        user_id="user-1", model_id="model-1", per_image_credits=5,
        params={}, prompt="cat",
    )

    assert result == "external-1"
    assert handler._lock_credits.call_args.kwargs["task_id"] == "local-1"
    attach = _Lifecycle.instances[0].attach_calls[0]
    assert attach["task_id"] == "local-1"
    assert attach["actual_model_id"] == "model-1"


@pytest.mark.asyncio
async def test_explicit_provider_failure_refunds_and_fails_local_task(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.image_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    handler = _Handler()

    result = await submit_prepared_image_task(
        handler=handler, local_task_id="local-1",
        adapter=_Adapter(error=ValueError("rejected")), index=0,
        batch_id="batch-1", generate_kwargs={}, user_id="user-1",
        model_id="model-1", per_image_credits=5, params={}, prompt="cat",
    )

    assert result is None
    handler._refund_credits.assert_called_once_with("tx-1")
    assert _Lifecycle.instances[-1].fail_calls[0]["task_id"] == "local-1"


@pytest.mark.asyncio
async def test_unknown_timeout_keeps_preparing_and_locked(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.image_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    handler = _Handler()

    with pytest.raises(TimeoutError):
        await submit_prepared_image_task(
            handler=handler, local_task_id="local-1",
            adapter=_Adapter(error=TimeoutError("unknown")), index=0,
            batch_id="batch-1", generate_kwargs={}, user_id="user-1",
            model_id="model-1", per_image_credits=5, params={}, prompt="cat",
        )

    handler._refund_credits.assert_not_called()
    assert all(not instance.fail_calls for instance in _Lifecycle.instances)
    update = handler.db.table.return_value.update.call_args.args[0]
    assert update["terminal_reason"] == "submission_unknown"


@pytest.mark.asyncio
async def test_smart_retry_reuses_local_task_and_attaches_actual_model(monkeypatch):
    class _RetryContext:
        def __init__(self, **kwargs):
            self.failed_attempts = []
            self.checked = False

        def add_failure(self, model, error):
            self.failed_attempts.append((model, error))

        @property
        def can_retry(self):
            if self.checked:
                return False
            self.checked = True
            return True

    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.image_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    monkeypatch.setattr("services.intent_router.RetryContext", _RetryContext)
    monkeypatch.setattr(
        "services.adapters.factory.create_image_adapter",
        lambda model: _Adapter(result="retry-external"),
    )
    handler = _Handler()

    result = await submit_prepared_image_task(
        handler=handler, local_task_id="local-1",
        adapter=_Adapter(error=ValueError("first rejected")), index=0,
        batch_id="batch-1", generate_kwargs={}, user_id="user-1",
        model_id="model-1", per_image_credits=5,
        params={"_is_smart_mode": True}, prompt="cat",
    )

    assert result == "retry-external"
    assert [call.kwargs["task_id"] for call in handler._lock_credits.call_args_list] == [
        "local-1", "local-1",
    ]
    handler._refund_credits.assert_called_once_with("tx-1")
    attach = _Lifecycle.instances[-1].attach_calls[0]
    assert attach["actual_model_id"] == "retry-model"
    assert attach["credit_transaction_id"] == "tx-2"
