from unittest.mock import patch

import pytest

from services.file_upload import download_url_to_workspace
from tests.test_file_upload import _make_downloader_mock, _patch_settings_and_oss


@pytest.mark.asyncio
async def test_runtime_media_name_uses_content_mime_not_header(tmp_path):
    settings, mock_oss = _patch_settings_and_oss(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    mock_dl = _make_downloader_mock(png, "image/jpeg")

    with patch("core.config.get_settings", return_value=settings), \
         patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
         patch("services.oss_service.get_oss_service", return_value=mock_oss):
        payload = await download_url_to_workspace(
            url="https://x/result", user_id="u1",
            suggested_stem="runtime_media_action",
            strict_content_mime=True, idempotent_name=True,
        )

    assert payload is not None
    assert payload["name"] == "runtime_media_action.png"
    assert payload["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_runtime_media_rejects_header_only_image_mime(tmp_path):
    settings, mock_oss = _patch_settings_and_oss(tmp_path)
    mock_dl = _make_downloader_mock(b"not-an-image", "image/png")

    with patch("core.config.get_settings", return_value=settings), \
         patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
         patch("services.oss_service.get_oss_service", return_value=mock_oss):
        payload = await download_url_to_workspace(
            url="https://x/result", user_id="u1",
            suggested_stem="runtime_media_action",
            strict_content_mime=True, idempotent_name=True,
        )

    assert payload is None


@pytest.mark.asyncio
async def test_runtime_video_name_uses_detected_mp4_payload(tmp_path):
    settings, mock_oss = _patch_settings_and_oss(tmp_path)
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"payload"
    mock_dl = _make_downloader_mock(mp4, "image/png")

    with patch("core.config.get_settings", return_value=settings), \
         patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
         patch("services.oss_service.get_oss_service", return_value=mock_oss):
        payload = await download_url_to_workspace(
            url="https://x/result", user_id="u1", media_type="video",
            suggested_stem="runtime_media_video",
            strict_content_mime=True, idempotent_name=True,
        )

    assert payload is not None
    assert payload["name"] == "runtime_media_video.mp4"
    assert payload["mime_type"] == "video/mp4"


@pytest.mark.asyncio
async def test_projection_storage_uses_explicit_non_secret_configuration(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    mock_dl = _make_downloader_mock(png, "image/png")

    with patch("core.config.get_settings", side_effect=AssertionError("dotenv")), \
         patch("services.http_downloader.HttpDownloader", return_value=mock_dl):
        payload = await download_url_to_workspace(
            url="https://x/result", user_id="u1",
            suggested_stem="runtime_media_action",
            strict_content_mime=True, idempotent_name=True,
            workspace_root=tmp_path, cdn_domain="cdn.example.test",
            use_configured_oss=False,
        )

    assert payload is not None
    assert payload["url"].startswith("https://cdn.example.test/workspace/")
