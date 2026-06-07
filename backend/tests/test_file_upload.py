"""测试 file_upload — upload_to_payload 返回 dict 双轨字段"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.file_upload import upload_to_payload


@pytest.fixture()
def workspace_dir(tmp_path):
    """模拟 workspace 目录结构: workspace_root/personal/<hash>/下载/"""
    import hashlib
    user_hash = hashlib.md5("u1".encode()).hexdigest()[:8]
    d = tmp_path / "personal" / user_hash / "下载"
    d.mkdir(parents=True)
    f = d / "report.xlsx"
    f.write_bytes(b"fake xlsx content" * 10)
    return d, tmp_path


class TestUploadToPayload:
    """upload_to_payload 返回双轨 dict (url + workspace_path)"""

    @pytest.mark.asyncio
    async def test_returns_dict_with_url(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.example.com"
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.return_value = (
            "https://cdn.example.com/workspace/personal/x/下载/report.xlsx"
        )

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await upload_to_payload(
                filename="report.xlsx",
                size=170,
                output_dir=str(output_dir),
                user_id="u1",
            )

        assert result is not None
        assert result["url"].startswith("https://cdn.example.com/")
        assert result["name"] == "report.xlsx"
        assert result["size"] == 170
        assert "mime_type" in result

    @pytest.mark.asyncio
    async def test_oss_sync_fail_falls_back_to_cdn_url(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.example.com"
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.side_effect = Exception("OSS down")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await upload_to_payload(
                filename="report.xlsx",
                size=170,
                output_dir=str(output_dir),
                user_id="u1",
            )

        assert result is not None
        # 兜底直接拼 CDN domain
        assert "cdn.example.com" in result["url"]

    @pytest.mark.asyncio
    async def test_no_cdn_domain_returns_none(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = ""  # 无 CDN
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.side_effect = Exception("OSS down")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await upload_to_payload(
                filename="report.xlsx",
                size=170,
                output_dir=str(output_dir),
                user_id="u1",
            )

        # 无 CDN 兜底且 OSS sync 失败 → 返回 None
        assert result is None
