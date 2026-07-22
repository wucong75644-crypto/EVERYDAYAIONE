"""Web 视频原子准备与已准备 task 提交测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routes.message_video_preparation import prepare_and_start_video_generation
from schemas.message import GenerateRequest, GenerationType, TextPart
from services.generation_lifecycle import GenerationPreparation
from services.handlers.video_handler import VideoHandler
from services.handlers.video_prepared_submission import (
    VideoSubmissionSettings,
    resolve_video_submission_settings,
    submit_prepared_video_task,
)


class _Lifecycle:
    instances = []

    def __init__(self, db):
        self.calls = []
        self.attach_calls = []
        self.fail_calls = []
        self.__class__.instances.append(self)

    def prepare(self, **kwargs):
        self.calls.append(kwargs)
        return GenerationPreparation(
            request_id=kwargs["request_id"], conversation_id=kwargs["conversation_id"],
            turn_id="turn-1", input_message_id="input-1",
            output_message_id=kwargs["output_message"]["id"],
            base_context_revision=1, context_through_message_id=None,
            task_ids=(kwargs["tasks"][0]["id"],), already_prepared=False,
        )

    def attach_external_task(self, **kwargs):
        self.attach_calls.append(kwargs)

    def fail_prepared_task(self, **kwargs):
        self.fail_calls.append(kwargs)


class _Handler:
    def __init__(self):
        self.db = MagicMock()
        self.org_id = "org-1"
        self._check_balance = MagicMock()
        self._lock_credits = MagicMock(side_effect=["tx-1", "tx-2"])
        self._refund_credits = MagicMock()
        self._route_retry = AsyncMock(
            return_value=SimpleNamespace(recommended_model="retry-model")
        )
        self._build_callback_url = MagicMock(return_value="https://callback")
        self.start = AsyncMock(return_value="client-task")

    def _extract_text_content(self, content):
        return content[0].text

    def _extract_image_url(self, content):
        return None

    def _serialize_params(self, params):
        return dict(params)


class _ConversationService:
    async def get_conversation(self, conversation_id, user_id, org_id):
        return {"id": conversation_id, "context_summary": "summary"}


class _Adapter:
    provider = SimpleNamespace(value="kie")

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def generate(self, **kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(task_id=self.result)

    async def close(self):
        return None


def _settings():
    return VideoSubmissionSettings(
        prompt="dance", image_url=None, model_id="video-model",
        aspect_ratio="landscape", remove_watermark=True, credits=20,
    )


@pytest.mark.asyncio
async def test_video_handler_requires_prepared_task_before_balance(monkeypatch):
    handler = VideoHandler(MagicMock())
    handler._check_balance = MagicMock()
    submit = AsyncMock()
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.resolve_video_submission_settings",
        lambda *args: _settings(),
    )
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.submit_prepared_video_task", submit,
    )

    with pytest.raises(RuntimeError, match="VIDEO_PREPARED_TASK_MISSING"):
        await handler.start(
            message_id="message-1", conversation_id="conversation-1",
            user_id="user-1", content=[TextPart(text="dance")], params={},
            metadata=SimpleNamespace(client_task_id="client-task"),
        )

    handler._check_balance.assert_not_called()
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_handler_delegates_prepared_task(monkeypatch):
    handler = VideoHandler(MagicMock())
    handler._check_balance = MagicMock()
    submit = AsyncMock(return_value="client-task")
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.resolve_video_submission_settings",
        lambda *args: _settings(),
    )
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.submit_prepared_video_task", submit,
    )

    result = await handler.start(
        message_id="message-1", conversation_id="conversation-1",
        user_id="user-1", content=[TextPart(text="dance")], params={},
        metadata=SimpleNamespace(
            client_task_id="client-task", prepared_task_id="local-task",
        ),
    )

    assert result == "client-task"
    handler._check_balance.assert_called_once_with("user-1", 20)
    assert submit.await_args.kwargs["local_task_id"] == "local-task"


def test_video_settings_resolve_model_duration_and_cost(monkeypatch):
    calculate = MagicMock(return_value={"user_credits": 30})
    monkeypatch.setattr("config.kie_models.calculate_video_cost", calculate)

    settings = resolve_video_submission_settings(
        _Handler(), [TextPart(text="dance")],
        {"model": "video-model", "n_frames": "126", "aspect_ratio": "portrait"},
    )

    assert settings.model_id == "video-model"
    assert settings.aspect_ratio == "portrait"
    assert settings.credits == 30
    assert calculate.call_args.kwargs["duration_seconds"] == 15


@pytest.mark.asyncio
async def test_video_task_is_prepared_before_handler_start(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr("api.routes.message_video_preparation.GenerationLifecycle", _Lifecycle)
    monkeypatch.setattr(
        "api.routes.message_video_preparation.resolve_video_submission_settings",
        lambda *args: _settings(),
    )
    monkeypatch.setattr(
        "api.routes.message_video_preparation.record_user_activity",
        lambda *args, **kwargs: None,
    )
    handler = _Handler()
    body = GenerateRequest(
        content=[TextPart(text="dance")], generation_type=GenerationType.VIDEO,
        model="video-model", client_request_id="request", client_task_id="client-task",
        assistant_message_id="00000000-0000-0000-0000-000000000002",
    )

    response = await prepare_and_start_video_generation(
        db=object(), handler=handler, conversation_service=_ConversationService(),
        conversation_id="conv-1", user_id="user-1", org_id="org-1",
        request_id="request-row", body=body,
    )

    task = _Lifecycle.instances[0].calls[0]["tasks"][0]
    assert task["status"] == "preparing"
    metadata = handler.start.await_args.kwargs["metadata"]
    assert metadata.prepared_task_id == task["id"]
    assert response.user_message.id == "input-1"


@pytest.mark.asyncio
async def test_prepared_video_locks_and_attaches_same_task(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    monkeypatch.setattr(
        "services.adapters.factory.create_video_adapter",
        lambda model: _Adapter(result="external-1"),
    )
    handler = _Handler()

    result = await submit_prepared_video_task(
        handler=handler, local_task_id="local-1", user_id="user-1",
        params={}, settings=_settings(), client_task_id="client-task",
    )

    assert result == "client-task"
    assert handler._lock_credits.call_args.kwargs["task_id"] == "local-1"
    assert _Lifecycle.instances[-1].attach_calls[0]["task_id"] == "local-1"


@pytest.mark.asyncio
async def test_video_rejection_refunds_and_fails_prepared_task(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    monkeypatch.setattr(
        "services.adapters.factory.create_video_adapter",
        lambda model: _Adapter(error=ValueError("rejected")),
    )
    handler = _Handler()

    with pytest.raises(ValueError, match="rejected"):
        await submit_prepared_video_task(
            handler=handler, local_task_id="local-1", user_id="user-1",
            params={}, settings=_settings(), client_task_id="client-task",
        )

    handler._refund_credits.assert_called_once_with("tx-1")
    assert _Lifecycle.instances[-1].fail_calls[0]["task_id"] == "local-1"


@pytest.mark.asyncio
async def test_video_timeout_keeps_preparing_and_locked(monkeypatch):
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    monkeypatch.setattr(
        "services.adapters.factory.create_video_adapter",
        lambda model: _Adapter(error=TimeoutError("unknown")),
    )
    handler = _Handler()

    with pytest.raises(TimeoutError):
        await submit_prepared_video_task(
            handler=handler, local_task_id="local-1", user_id="user-1",
            params={}, settings=_settings(), client_task_id="client-task",
        )

    handler._refund_credits.assert_not_called()
    assert all(not instance.fail_calls for instance in _Lifecycle.instances)
    update = handler.db.table.return_value.update.call_args.args[0]
    assert update["terminal_reason"] == "submission_unknown"


@pytest.mark.asyncio
async def test_video_smart_retry_reuses_task_and_attaches_actual_model(monkeypatch):
    class _RetryContext:
        def __init__(self, **kwargs):
            self.checked = False

        def add_failure(self, *args):
            return None

        @property
        def can_retry(self):
            if self.checked:
                return False
            self.checked = True
            return True

    adapters = iter([
        _Adapter(error=ValueError("first rejected")),
        _Adapter(result="retry-external"),
    ])
    _Lifecycle.instances.clear()
    monkeypatch.setattr(
        "services.handlers.video_prepared_submission.GenerationLifecycle", _Lifecycle,
    )
    monkeypatch.setattr("services.intent_router.RetryContext", _RetryContext)
    monkeypatch.setattr(
        "services.adapters.factory.create_video_adapter", lambda model: next(adapters),
    )
    handler = _Handler()

    result = await submit_prepared_video_task(
        handler=handler, local_task_id="local-1", user_id="user-1",
        params={"_is_smart_mode": True}, settings=_settings(),
        client_task_id="client-task",
    )

    assert result == "client-task"
    assert [call.kwargs["task_id"] for call in handler._lock_credits.call_args_list] == [
        "local-1", "local-1",
    ]
    handler._refund_credits.assert_called_once_with("tx-1")
    attach = _Lifecycle.instances[-1].attach_calls[0]
    assert attach["actual_model_id"] == "retry-model"
    assert attach["credit_transaction_id"] == "tx-2"
