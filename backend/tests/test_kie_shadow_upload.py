"""KIE 图片临时空间旁路探测测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.adapters.kie.client import KieClient
from services.adapters.kie.models import CreateTaskRequest


SOURCE_URL = "https://cdn.example.com/workspace/org/demo/input.png"
STAGED_URL = "https://tempfile.redpandaai.co/demo/shadow-input.png"
IMAGE_MODEL = "gpt-image-2-image-to-image"


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class _DownloadResponse:
    headers = {"content-type": "image/png"}

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size=8192):
        yield b"image-bytes"


class _UploadResponse:
    status_code = 200

    def json(self):
        return {
            "success": True,
            "code": 200,
            "data": {"downloadUrl": STAGED_URL},
        }


class _DownloadClient:
    def __init__(self):
        self.stream_calls = []

    def stream(self, method, url):
        self.stream_calls.append((method, url))
        return _AsyncContext(_DownloadResponse())


class _UploadClient:
    def __init__(self):
        self.post = AsyncMock(return_value=_UploadResponse())


@pytest.mark.asyncio
async def test_create_task_schedules_shadow_upload_without_changing_main_request():
    client = KieClient(api_key="test-key")
    main_http = MagicMock()
    main_http.post = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "code": 200,
                    "msg": "success",
                    "data": {"taskId": "task-123"},
                }
            ),
        )
    )
    request = CreateTaskRequest(
        model=IMAGE_MODEL,
        input={"input_urls": [SOURCE_URL, SOURCE_URL]},
    )

    with patch.object(client, "_get_client", return_value=main_http), patch.object(
        client, "_run_shadow_upload", new_callable=AsyncMock
    ) as shadow_upload:
        result = await client.create_task(request)
        await asyncio.gather(*client._shadow_upload_tasks)

    assert result.task_id == "task-123"
    main_http.post.assert_awaited_once()
    assert main_http.post.await_args.kwargs["json"] == request.model_dump(exclude_none=True)
    shadow_upload.assert_awaited_once_with(
        model=IMAGE_MODEL,
        source_urls=[SOURCE_URL],
    )


@pytest.mark.asyncio
async def test_shadow_upload_uses_environment_proxy_and_uploads_bytes():
    client = KieClient(api_key="test-key")
    download_client = _DownloadClient()
    upload_client = _UploadClient()

    fake_http_clients = [download_client, upload_client]
    with patch(
        "services.adapters.kie.client.httpx.AsyncClient",
        side_effect=lambda **kwargs: _AsyncContext(fake_http_clients.pop(0)),
    ) as async_client:
        await client._run_shadow_upload(IMAGE_MODEL, [SOURCE_URL])

    assert async_client.call_args_list[0].kwargs["trust_env"] is True
    assert async_client.call_args_list[1].kwargs["trust_env"] is True
    assert download_client.stream_calls == [("GET", SOURCE_URL)]
    upload_kwargs = upload_client.post.await_args.kwargs
    assert upload_kwargs["data"]["uploadPath"] == "everydayai/input-media"
    assert upload_kwargs["files"]["file"][1:] == (
        b"image-bytes",
        "image/png",
    )


def test_shadow_upload_skips_non_image_kie_requests():
    client = KieClient(api_key="test-key")
    image_request = CreateTaskRequest(
        model="sora-2-image-to-video",
        input={"image_urls": [SOURCE_URL]},
    )
    chat_request = CreateTaskRequest(
        model="gemini-3-pro",
        input={"input_urls": [SOURCE_URL]},
    )

    assert client._extract_shadow_image_urls(image_request) == []
    assert client._extract_shadow_image_urls(chat_request) == []

