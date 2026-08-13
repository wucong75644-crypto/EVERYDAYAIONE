from types import SimpleNamespace

import pytest

from api.routes import message_runtime_media_retry as retry_route
from core.exceptions import AppException
from schemas.message import GenerateRequest, GenerationType, MessageOperation
from services.runtime_media_message_control import RuntimeMediaRetryReceipt


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows.pop(0))


class _DB:
    def __init__(self, *rows):
        self.rows = list(rows)

    def table(self, name):
        assert name == "messages"
        return _Query(self.rows)


def _message(*, runtime=True):
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "conversation_id": "22222222-2222-4222-8222-222222222222",
        "role": "assistant",
        "content": [{
            "type": "image", "url": None,
            "slot_id": "33333333-3333-4333-8333-333333333333",
            "slot_index": 4, "slot_status": "failed", "slot_revision": 2,
        }],
        "status": "failed",
        "generation_params": ({"runtime_media_batch": {"slot_count": 5}}
                              if runtime else {"model": "legacy"}),
        "created_at": "2026-08-14T00:00:00Z",
    }


def _request(**params):
    return GenerateRequest(
        operation=MessageOperation.REGENERATE_SINGLE,
        generation_type=GenerationType.IMAGE,
        original_message_id="11111111-1111-4111-8111-111111111111",
        client_task_id="client-task",
        params={"image_index": 4, **params},
    )


@pytest.mark.asyncio
async def test_standalone_image_retry_keeps_legacy_route() -> None:
    result = await retry_route.try_runtime_media_slot_retry(
        conversation_id="22222222-2222-4222-8222-222222222222",
        body=_request(), ctx=SimpleNamespace(org_id="org"),
        db=_DB(_message(runtime=False)), user_id="user", request_id="request",
        gen_type=GenerationType.IMAGE, record_feedback=lambda *_: None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_runtime_retry_uses_stable_slot_and_returns_client_task(monkeypatch) -> None:
    captured = {}

    class _Service:
        def __init__(self, _db, **_scope):
            pass

        async def retry_slot(self, *args, **kwargs):
            captured.update({"args": args, **kwargs})
            return RuntimeMediaRetryReceipt(
                action_id="action", run_id="run", task_id="task",
                slot_id="33333333-3333-4333-8333-333333333333",
                slot_index=4, slot_revision=3, replayed=False,
            )

    monkeypatch.setattr(retry_route, "RuntimeMediaMessageControlService", _Service)
    monkeypatch.setattr(retry_route, "record_user_activity", lambda *_args, **_kwargs: None)
    feedback = []
    result = await retry_route.try_runtime_media_slot_retry(
        conversation_id="22222222-2222-4222-8222-222222222222",
        body=_request(
            runtime_slot_id="33333333-3333-4333-8333-333333333333",
            runtime_slot_revision=2, _task_slot_id="limit-slot",
        ),
        ctx=SimpleNamespace(org_id="org"), db=_DB(_message(), _message()),
        user_id="user", request_id="request", gen_type=GenerationType.IMAGE,
        record_feedback=lambda *_: feedback.append(True),
    )

    assert result and result.task_id == "client-task"
    assert captured["slot_id"] == "33333333-3333-4333-8333-333333333333"
    assert captured["expected_slot_revision"] == 2
    assert captured["client_task_id"] == "client-task"
    assert captured["task_slot_id"] == "limit-slot"
    assert feedback == [True]


@pytest.mark.asyncio
async def test_runtime_retry_rejects_stale_visual_slot() -> None:
    with pytest.raises(AppException) as error:
        await retry_route.try_runtime_media_slot_retry(
            conversation_id="22222222-2222-4222-8222-222222222222",
            body=_request(runtime_slot_revision=1),
            ctx=SimpleNamespace(org_id="org"), db=_DB(_message()),
            user_id="user", request_id="request", gen_type=GenerationType.IMAGE,
            record_feedback=lambda *_: None,
        )
    assert error.value.code == "RUNTIME_MEDIA_SLOT_STALE"
