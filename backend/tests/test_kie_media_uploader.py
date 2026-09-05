"""KIE 临时输入素材交付回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.adapters.kie.client import KieClient, KieMediaUploadError
from services.adapters.kie.image_adapter import KieImageAdapter
from services.adapters.kie.media_uploader import KieMediaUploader
from services.adapters.kie.video_adapter import KieVideoAdapter


SOURCE_URL = "https://cdn.example.com/workspace/org/demo/上传/素材图.png"
STAGED_URL = "https://tempfile.redpandaai.co/demo/staged-image.png"


def _own_cdn_settings():
    return patch("services.oss_service.settings")


@pytest.mark.asyncio
async def test_prepare_image_urls_uploads_once_and_preserves_duplicate_order():
    client = MagicMock()
    client.upload_file_stream = AsyncMock(return_value=STAGED_URL)
    uploader = KieMediaUploader(client)
    uploader._downloader.download = AsyncMock(return_value=(b"image-bytes", "image/png"))

    with _own_cdn_settings() as settings:
        settings.oss_cdn_domain = "cdn.example.com"
        settings.oss_bucket_name = None
        settings.oss_endpoint = None
        prepared = await uploader.prepare_image_urls([SOURCE_URL, SOURCE_URL])
        prepared_again = await uploader.prepare_image_urls([SOURCE_URL])

    assert prepared == [STAGED_URL, STAGED_URL]
    assert prepared_again == [STAGED_URL]
    uploader._downloader.download.assert_awaited_once()
    client.upload_file_stream.assert_awaited_once()
    assert client.upload_file_stream.await_args.kwargs["content"] == b"image-bytes"
    assert client.upload_file_stream.await_args.kwargs["content_type"] == "image/png"

    await uploader.close()


@pytest.mark.asyncio
async def test_prepare_image_urls_rejects_unknown_external_host_without_downloading():
    client = MagicMock()
    uploader = KieMediaUploader(client)
    uploader._downloader.download = AsyncMock()

    with _own_cdn_settings() as settings:
        settings.oss_cdn_domain = "cdn.example.com"
        settings.oss_bucket_name = None
        settings.oss_endpoint = None
        with pytest.raises(KieMediaUploadError, match="工作区已上传图片"):
            await uploader.prepare_image_urls(["https://third-party.example/image.png"])

    uploader._downloader.download.assert_not_awaited()
    await uploader.close()


@pytest.mark.asyncio
async def test_image_adapter_uses_staged_url_for_kie_create_task():
    client = MagicMock()
    client.create_task = AsyncMock(return_value=MagicMock(task_id="kie-image-task"))
    adapter = KieImageAdapter(client, "gpt-image-2-image-to-image")
    adapter.media_uploader.prepare_image_urls = AsyncMock(return_value=[STAGED_URL])

    result = await adapter.generate(
        prompt="edit image",
        image_urls=[SOURCE_URL],
        resolution="1K",
        wait_for_result=False,
    )

    assert result.task_id == "kie-image-task"
    assert client.create_task.await_args.args[0].input["input_urls"] == [STAGED_URL]
    adapter.media_uploader.prepare_image_urls.assert_awaited_once()


@pytest.mark.asyncio
async def test_video_adapter_uses_staged_url_for_kie_create_task():
    client = MagicMock()
    client.create_task = AsyncMock(return_value=MagicMock(task_id="kie-video-task"))
    adapter = KieVideoAdapter(client, "sora-2-image-to-video")
    adapter.media_uploader.prepare_image_urls = AsyncMock(return_value=[STAGED_URL])

    result = await adapter.generate(
        prompt="animate image",
        image_urls=[SOURCE_URL],
        wait_for_result=False,
    )

    assert result.task_id == "kie-video-task"
    assert client.create_task.await_args.args[0].input["image_urls"] == [STAGED_URL]
    adapter.media_uploader.prepare_image_urls.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_only_image_model_skips_media_staging():
    client = MagicMock()
    client.create_task = AsyncMock(return_value=MagicMock(task_id="kie-text-task"))
    adapter = KieImageAdapter(client, "google/nano-banana")
    adapter.media_uploader.prepare_image_urls = AsyncMock()

    await adapter.generate(
        prompt="generate an image",
        image_urls=[SOURCE_URL],
        wait_for_result=False,
    )

    adapter.media_uploader.prepare_image_urls.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_stream_upload_returns_download_url_and_sends_multipart():
    client = KieClient(api_key="test-key")
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "success": True,
        "code": 200,
        "data": {"downloadUrl": STAGED_URL},
    }
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.post = AsyncMock(return_value=response)

    with patch("services.adapters.kie.client.httpx.AsyncClient", return_value=session) as mock_http:
        returned_url = await client.upload_file_stream(
            content=b"image-bytes",
            file_name="random.png",
            content_type="image/png",
        )

    assert returned_url == STAGED_URL
    assert mock_http.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}
    post_kwargs = session.post.await_args.kwargs
    assert post_kwargs["data"]["uploadPath"] == "everydayai/input-media"
    assert post_kwargs["files"]["file"] == ("random.png", b"image-bytes", "image/png")
