"""MessageIdempotencyService 单元测试。"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from core.exceptions import AppException
from schemas.message import (
    GenerateRequest,
    GenerateResponse,
    GenerationType,
    Message,
    MessageOperation,
    MessageRole,
    TextPart,
)
from services.message_idempotency_service import MessageIdempotencyService


def _request(key: str | None = None) -> Request:
    headers = [] if key is None else [(b"idempotency-key", key.encode())]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def _body(**overrides) -> GenerateRequest:
    values = {
        "content": [TextPart(text="生成一张图")],
        "generation_type": GenerationType.IMAGE,
        "model": "image-model",
        "client_request_id": "request-1",
        "client_task_id": "task-1",
        "assistant_message_id": "00000000-0000-0000-0000-000000000001",
    }
    values.update(overrides)
    return GenerateRequest(**values)


def _response() -> GenerateResponse:
    assistant = Message(
        id="00000000-0000-0000-0000-000000000001",
        conversation_id="00000000-0000-0000-0000-000000000002",
        role=MessageRole.ASSISTANT,
        content=[],
        created_at=datetime.now(timezone.utc),
    )
    return GenerateResponse(
        task_id="task-1",
        assistant_message=assistant,
        operation=MessageOperation.SEND,
        generation_type="image",
    )


def _db_with_claim(data: dict) -> MagicMock:
    db = MagicMock()
    db.rpc.return_value.execute.return_value = SimpleNamespace(data=data)
    return db


def test_legacy_request_without_key_bypasses_idempotency() -> None:
    body = _body(client_request_id=None)
    service = MessageIdempotencyService(MagicMock(), "user-1", None)

    assert service.claim(_request(), "conversation-1", body) is None


def test_header_and_body_key_must_match() -> None:
    service = MessageIdempotencyService(MagicMock(), "user-1", None)

    with pytest.raises(AppException) as caught:
        service.claim(_request("other-key"), "conversation-1", _body())

    assert caught.value.code == "IDEMPOTENCY_KEY_MISMATCH"


def test_header_key_length_is_validated_before_database_call() -> None:
    db = MagicMock()
    service = MessageIdempotencyService(db, "user-1", None)
    body = _body(client_request_id=None)

    with pytest.raises(AppException) as caught:
        service.claim(_request("x" * 101), "conversation-1", body)

    assert caught.value.code == "IDEMPOTENCY_KEY_INVALID"
    db.rpc.assert_not_called()


def test_fingerprint_ignores_runtime_params() -> None:
    first = _body(params={"aspect_ratio": "1:1", "_user_location": "杭州"})
    second = _body(params={"aspect_ratio": "1:1", "_user_location": "上海"})

    assert MessageIdempotencyService.build_fingerprint("conv", first) == (
        MessageIdempotencyService.build_fingerprint("conv", second)
    )


def test_claimed_request_returns_execution_right() -> None:
    db = _db_with_claim({"outcome": "claimed", "request_id": "record-1"})
    service = MessageIdempotencyService(db, "user-1", None)

    claim = service.claim(_request("request-1"), "conversation-1", _body())

    assert claim is not None
    assert claim.request_id == "record-1"
    assert claim.replay_response is None


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        ("fingerprint_mismatch", "IDEMPOTENCY_KEY_REUSED"),
        ("processing", "IDEMPOTENCY_REQUEST_IN_PROGRESS"),
    ],
)
def test_conflicting_claims_raise_structured_errors(outcome: str, expected_code: str) -> None:
    db = _db_with_claim({"outcome": outcome, "request_id": "record-1"})
    service = MessageIdempotencyService(db, "user-1", None)

    with pytest.raises(AppException) as caught:
        service.claim(_request("request-1"), "conversation-1", _body())

    assert caught.value.code == expected_code


def test_completed_claim_replays_original_response() -> None:
    response = _response()
    db = _db_with_claim({
        "outcome": "completed",
        "request_id": "record-1",
        "stored_response_body": response.model_dump(mode="json"),
    })
    service = MessageIdempotencyService(db, "user-1", None)

    claim = service.claim(_request("request-1"), "conversation-1", _body())

    assert claim is not None
    assert claim.replay_response is not None
    assert claim.replay_response.task_id == "task-1"


def test_failed_claim_replays_original_error() -> None:
    db = _db_with_claim({
        "outcome": "failed",
        "request_id": "record-1",
        "stored_response_status": 402,
        "stored_response_body": {
            "error": {"code": "INSUFFICIENT_CREDITS", "message": "积分不足", "details": {}}
        },
    })
    service = MessageIdempotencyService(db, "user-1", None)

    with pytest.raises(AppException) as caught:
        service.claim(_request("request-1"), "conversation-1", _body())

    assert caught.value.code == "INSUFFICIENT_CREDITS"
    assert caught.value.status_code == 402


def test_complete_persists_replayable_response() -> None:
    db = MagicMock()
    service = MessageIdempotencyService(db, "user-1", None)
    claim = SimpleNamespace(request_id="record-1")

    service.complete(claim, _response())

    update = db.table.return_value.update.call_args.args[0]
    assert update["status"] == "completed"
    assert update["response_body"]["task_id"] == "task-1"


def test_unexpected_failure_persists_generic_replayable_error() -> None:
    db = MagicMock()
    service = MessageIdempotencyService(db, "user-1", None)
    claim = SimpleNamespace(request_id="record-1")

    service.fail_unexpected(claim, RuntimeError("sensitive detail"))

    update = db.table.return_value.update.call_args.args[0]
    assert update["status"] == "failed"
    assert update["response_status"] == 500
    assert update["response_body"]["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "消息请求处理失败",
        "details": {},
    }
    assert "sensitive detail" not in str(update)


def test_unexpected_failure_does_not_mask_original_when_persistence_fails() -> None:
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.execute.side_effect = (
        RuntimeError("database unavailable")
    )
    service = MessageIdempotencyService(db, "user-1", None)

    service.fail_unexpected(
        SimpleNamespace(request_id="record-1"),
        RuntimeError("original"),
    )
