"""统一消息路由的幂等接入测试。"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from api.deps import OrgContext
from core.exceptions import AppException
from schemas.message import (
    GenerateRequest,
    GenerateResponse,
    Message,
    MessageOperation,
    MessageRole,
    TextPart,
)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


def _body() -> GenerateRequest:
    return GenerateRequest(content=[TextPart(text="hello")])


def _response() -> GenerateResponse:
    assistant = Message(
        id="assistant-1",
        conversation_id="conversation-1",
        role=MessageRole.ASSISTANT,
        content=[],
        created_at=datetime.now(timezone.utc),
    )
    return GenerateResponse(
        task_id="task-1",
        assistant_message=assistant,
        operation=MessageOperation.SEND,
    )


@pytest.mark.asyncio
async def test_replayed_request_skips_slot_and_generation() -> None:
    replay = _response()
    service = MagicMock()
    service.claim.return_value = SimpleNamespace(replay_response=replay)
    task_limit = MagicMock()
    task_limit.check_and_acquire = AsyncMock()

    with patch("api.routes.message.MessageIdempotencyService", return_value=service), patch(
        "api.routes.message._do_generate_message", new_callable=AsyncMock
    ) as generate:
        from api.routes.message import generate_message

        result = await generate_message(
            request=_request(),
            conversation_id="conversation-1",
            body=_body(),
            ctx=OrgContext(user_id="user-1"),
            db=MagicMock(),
            task_limit_service=task_limit,
        )

    assert result is replay
    task_limit.check_and_acquire.assert_not_awaited()
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_request_records_completed_response() -> None:
    response = _response()
    claim = SimpleNamespace(request_id="request-row", replay_response=None)
    service = MagicMock()
    service.claim.return_value = claim

    with patch("api.routes.message.MessageIdempotencyService", return_value=service), patch(
        "api.routes.message._do_generate_message", new_callable=AsyncMock, return_value=response
    ):
        from api.routes.message import generate_message

        result = await generate_message(
            request=_request(),
            conversation_id="conversation-1",
            body=_body(),
            ctx=OrgContext(user_id="user-1"),
            db=MagicMock(),
            task_limit_service=None,
        )

    assert result is response
    service.complete.assert_called_once_with(claim, response)
    service.fail.assert_not_called()


@pytest.mark.asyncio
async def test_business_rejection_records_replayable_failure() -> None:
    claim = SimpleNamespace(request_id="request-row", replay_response=None)
    service = MagicMock()
    service.claim.return_value = claim
    rejection = AppException("INSUFFICIENT_CREDITS", "积分不足", 402)

    with patch("api.routes.message.MessageIdempotencyService", return_value=service), patch(
        "api.routes.message._do_generate_message", new_callable=AsyncMock, side_effect=rejection
    ):
        from api.routes.message import generate_message

        with pytest.raises(AppException) as caught:
            await generate_message(
                request=_request(),
                conversation_id="conversation-1",
                body=_body(),
                ctx=OrgContext(user_id="user-1"),
                db=MagicMock(),
                task_limit_service=None,
            )

    assert caught.value is rejection
    service.fail.assert_called_once_with(claim, rejection)


@pytest.mark.asyncio
async def test_unexpected_failure_records_generic_terminal_state_and_reraises() -> None:
    claim = SimpleNamespace(request_id="request-row", replay_response=None)
    service = MagicMock()
    service.claim.return_value = claim
    unexpected = RuntimeError("database disconnected")

    with patch("api.routes.message.MessageIdempotencyService", return_value=service), patch(
        "api.routes.message._do_generate_message",
        new_callable=AsyncMock,
        side_effect=unexpected,
    ):
        from api.routes.message import generate_message

        with pytest.raises(RuntimeError) as caught:
            await generate_message(
                request=_request(),
                conversation_id="conversation-1",
                body=_body(),
                ctx=OrgContext(user_id="user-1"),
                db=MagicMock(),
                task_limit_service=None,
            )

    assert caught.value is unexpected
    service.fail_unexpected.assert_called_once_with(claim, unexpected)
