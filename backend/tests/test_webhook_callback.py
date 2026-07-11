"""Webhook 快速响应、鉴权与完成互斥测试。"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from api.routes.webhook import _processing_tasks, handle_webhook
from services.adapters.base import ImageGenerateResult, TaskStatus
from services.handlers.base import BaseHandler
from services.task_completion_service import TaskCompletionService


def _request(payload: dict, token: str | None = "secret-token") -> Request:
    body = json.dumps(payload).encode()
    query_string = f"token={token}".encode() if token is not None else b""
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/webhook/kie",
            "query_string": query_string,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def _success_payload() -> dict:
    return {
        "taskId": "kie-task-1",
        "state": "success",
        "resultJson": json.dumps({"resultUrls": ["https://kie.example/image.png"]}),
    }


def test_callback_url_requires_base_url_and_token() -> None:
    settings = MagicMock(callback_base_url="https://example.com/", callback_token=None)
    with patch("core.config.get_settings", return_value=settings):
        assert BaseHandler._build_callback_url(MagicMock(), "kie") is None


def test_callback_url_contains_encoded_token() -> None:
    settings = MagicMock(
        callback_base_url="https://example.com/",
        callback_token="secret token/+",
    )
    with patch("core.config.get_settings", return_value=settings):
        url = BaseHandler._build_callback_url(MagicMock(), "kie")

    assert url == (
        "https://example.com/api/webhook/kie?token=secret%20token%2F%2B"
    )


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_token() -> None:
    settings = MagicMock(callback_token="secret-token")
    with patch("api.routes.webhook.get_settings", return_value=settings):
        response = await handle_webhook("kie", _request(_success_payload(), "wrong"), MagicMock())

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_returns_before_processing_finishes() -> None:
    settings = MagicMock(callback_token="secret-token")
    processing_started = asyncio.Event()
    allow_finish = asyncio.Event()

    async def slow_process(*_args) -> bool:
        processing_started.set()
        await allow_finish.wait()
        return True

    service = MagicMock()
    service.get_task.return_value = {"status": "pending", "type": "image"}
    service.process_result = AsyncMock(side_effect=slow_process)

    with (
        patch("api.routes.webhook.get_settings", return_value=settings),
        patch("api.routes.webhook.TaskCompletionService", return_value=service),
    ):
        response = await handle_webhook("kie", _request(_success_payload()), MagicMock())
        await asyncio.wait_for(processing_started.wait(), timeout=0.2)

    assert response.status_code == 200
    assert not allow_finish.is_set()
    service.process_result.assert_awaited_once()

    allow_finish.set()
    await asyncio.gather(*list(_processing_tasks))


@pytest.mark.asyncio
async def test_webhook_skips_already_completed_task() -> None:
    settings = MagicMock(callback_token="secret-token")
    service = MagicMock()
    service.get_task.return_value = {"status": "completed", "type": "image"}
    service.process_result = AsyncMock()

    with (
        patch("api.routes.webhook.get_settings", return_value=settings),
        patch("api.routes.webhook.TaskCompletionService", return_value=service),
    ):
        response = await handle_webhook("kie", _request(_success_payload()), MagicMock())

    assert response.status_code == 200
    service.process_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_returns_not_found_without_starting_processing() -> None:
    settings = MagicMock(callback_token="secret-token")
    service = MagicMock()
    service.get_task.return_value = None
    service.process_result = AsyncMock()

    with (
        patch("api.routes.webhook.get_settings", return_value=settings),
        patch("api.routes.webhook.TaskCompletionService", return_value=service),
    ):
        response = await handle_webhook("kie", _request(_success_payload()), MagicMock())

    assert response.status_code == 404
    service.process_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_lock_blocks_later_second_entry() -> None:
    task = {"status": "pending", "external_task_id": "kie-task-1"}
    result = ImageGenerateResult(
        task_id="kie-task-1",
        status=TaskStatus.SUCCESS,
        image_urls=["https://kie.example/image.png"],
    )
    first_started = asyncio.Event()
    allow_first_finish = asyncio.Event()
    service = TaskCompletionService(MagicMock())
    service.get_task = MagicMock(return_value=task)

    async def locked_processing(*_args) -> bool:
        first_started.set()
        await allow_first_finish.wait()
        return True

    service._process_result_locked = AsyncMock(side_effect=locked_processing)

    with (
        patch("core.redis.RedisClient.acquire_lock", AsyncMock(side_effect=["token-1", None])),
        patch("core.redis.RedisClient.release_lock", AsyncMock(return_value=True)) as release,
    ):
        first = asyncio.create_task(service.process_result("kie-task-1", result))
        await asyncio.wait_for(first_started.wait(), timeout=0.2)
        second_result = await service.process_result("kie-task-1", result)
        allow_first_finish.set()
        first_result = await first

    assert first_result is True
    assert second_result is True
    assert service._process_result_locked.await_count == 1
    release.assert_awaited_once_with("task_completion:kie-task-1", "token-1")


@pytest.mark.asyncio
async def test_completion_returns_false_when_redis_is_unavailable() -> None:
    task = {"status": "pending", "external_task_id": "kie-task-1"}
    result = ImageGenerateResult(
        task_id="kie-task-1",
        status=TaskStatus.SUCCESS,
        image_urls=["https://kie.example/image.png"],
    )
    service = TaskCompletionService(MagicMock())
    service.get_task = MagicMock(return_value=task)
    service._process_result_locked = AsyncMock()

    with patch(
        "core.redis.RedisClient.acquire_lock",
        AsyncMock(side_effect=ConnectionError("redis unavailable")),
    ):
        processed = await service.process_result("kie-task-1", result)

    assert processed is False
    service._process_result_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_task_does_not_require_redis() -> None:
    task = {"status": "completed", "external_task_id": "kie-task-1"}
    result = ImageGenerateResult(
        task_id="kie-task-1",
        status=TaskStatus.SUCCESS,
        image_urls=["https://kie.example/image.png"],
    )
    service = TaskCompletionService(MagicMock())
    service.get_task = MagicMock(return_value=task)

    with (
        patch("core.redis.RedisClient.acquire_lock", AsyncMock()) as acquire,
        patch("services.task_limit_service.release_task_slot", AsyncMock()) as release_slot,
    ):
        processed = await service.process_result("kie-task-1", result)

    assert processed is True
    acquire.assert_not_awaited()
    release_slot.assert_awaited_once_with(task)


@pytest.mark.asyncio
async def test_lock_renewal_error_returns_without_escaping() -> None:
    service = TaskCompletionService(MagicMock())

    with (
        patch("services.task_completion_service.asyncio.sleep", AsyncMock()),
        patch(
            "core.redis.RedisClient.extend_lock",
            AsyncMock(side_effect=ConnectionError("redis unavailable")),
        ),
    ):
        await service._renew_completion_lock("task_completion:kie-task-1", "token-1")
