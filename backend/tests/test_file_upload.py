"""测试 file_upload — auto_upload CDN/OSS/限制"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.file_upload import auto_upload


@pytest.fixture()
def output_dir(tmp_path):
    """临时输出目录，含一个测试文件。"""
    d = tmp_path / "下载"
    d.mkdir()
    f = d / "report.xlsx"
    f.write_bytes(b"fake xlsx content" * 10)
    return str(d)


class TestAutoUploadCDN:
    """CDN 路径（ossfs）优先策略。"""

    @pytest.mark.asyncio
    async def test_cdn_url_generated(self, output_dir, tmp_path):
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.example.com"
        settings.file_workspace_root = str(tmp_path)

        with patch("core.config.get_settings", return_value=settings):
            result = await auto_upload(
                filename="report.xlsx",
                size=170,
                output_dir=output_dir,
                user_id="u1",
            )

        assert "[FILE]" in result
        assert "cdn.example.com" in result
        assert "report.xlsx" in result
        assert "✅" in result

    @pytest.mark.asyncio
    async def test_cdn_url_encodes_chinese(self, output_dir, tmp_path):
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.example.com"
        settings.file_workspace_root = str(tmp_path)

        # 创建中文文件名
        f = Path(output_dir) / "销售报表.xlsx"
        f.write_bytes(b"test")

        with patch("core.config.get_settings", return_value=settings):
            result = await auto_upload(
                filename="销售报表.xlsx",
                size=4,
                output_dir=output_dir,
                user_id="u1",
            )

        assert "[FILE]" in result
        # URL 中不应有原始中文字符（应该被 encode）
        assert "销售报表" not in result.split("[FILE]")[1].split("|")[0]


class TestAutoUploadOSS:
    """OSS 兜底上传。"""

    @pytest.mark.asyncio
    async def test_oss_fallback(self, output_dir):
        settings = MagicMock()
        settings.oss_cdn_domain = ""  # 无 CDN

        mock_oss = MagicMock()
        mock_oss.upload_bytes.return_value = {
            "url": "https://oss.example.com/file.xlsx",
            "size": 170,
        }

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="report.xlsx",
                size=170,
                output_dir=output_dir,
                user_id="u1",
            )

        assert "[FILE]" in result
        assert "oss.example.com" in result
        mock_oss.upload_bytes.assert_called_once()

    @pytest.mark.asyncio
    async def test_oss_upload_failure(self, output_dir):
        settings = MagicMock()
        settings.oss_cdn_domain = ""

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", side_effect=Exception("OSS down")):
            result = await auto_upload(
                filename="report.xlsx",
                size=170,
                output_dir=output_dir,
                user_id="u1",
            )

        assert "❌" in result


class TestAutoUploadSizeLimit:
    """100MB 限制。"""

    @pytest.mark.asyncio
    async def test_rejects_over_100mb(self, output_dir):
        settings = MagicMock()
        settings.oss_cdn_domain = ""

        with patch("core.config.get_settings", return_value=settings):
            result = await auto_upload(
                filename="huge.csv",
                size=150 * 1024 * 1024,  # 150MB
                output_dir=output_dir,
                user_id="u1",
            )

        assert "❌" in result
        assert "过大" in result or "100MB" in result or "CDN" in result

    @pytest.mark.asyncio
    async def test_accepts_under_100mb(self, output_dir):
        settings = MagicMock()
        settings.oss_cdn_domain = ""

        mock_oss = MagicMock()
        mock_oss.upload_bytes.return_value = {"url": "https://oss/f.xlsx", "size": 99}

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="report.xlsx",
                size=99 * 1024 * 1024,  # 99MB
                output_dir=output_dir,
                user_id="u1",
            )

        assert "❌" not in result


class TestAutoUploadMimeType:
    """MIME 类型检测。"""

    @pytest.mark.asyncio
    async def test_xlsx_mime(self, output_dir, tmp_path):
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.test.com"
        settings.file_workspace_root = str(tmp_path)

        with patch("core.config.get_settings", return_value=settings):
            result = await auto_upload(
                filename="data.xlsx", size=100,
                output_dir=output_dir, user_id="u1",
            )

        # xlsx MIME type
        parts = result.split("|")
        assert any("spreadsheet" in p or "xlsx" in p for p in parts)

    @pytest.mark.asyncio
    async def test_csv_mime(self, output_dir, tmp_path):
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.test.com"
        settings.file_workspace_root = str(tmp_path)

        f = Path(output_dir) / "data.csv"
        f.write_text("a,b\n1,2")

        with patch("core.config.get_settings", return_value=settings):
            result = await auto_upload(
                filename="data.csv", size=7,
                output_dir=output_dir, user_id="u1",
            )

        parts = result.split("|")
        assert any("csv" in p or "text" in p for p in parts)
