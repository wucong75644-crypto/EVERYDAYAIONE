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
def workspace_dir(tmp_path):
    """模拟 workspace 目录结构：workspace_root/personal/<hash>/下载/"""
    import hashlib
    user_hash = hashlib.md5("u1".encode()).hexdigest()[:8]
    d = tmp_path / "personal" / user_hash / "下载"
    d.mkdir(parents=True)
    f = d / "report.xlsx"
    f.write_bytes(b"fake xlsx content" * 10)
    return d, tmp_path


class TestAutoUploadCDN:
    """CDN URL 生成（OSS sync 成功 → 返回 CDN URL）。"""

    @pytest.mark.asyncio
    async def test_cdn_url_generated(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.example.com"
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.return_value = "https://cdn.example.com/workspace/personal/e4774cdd/下载/report.xlsx"

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="report.xlsx",
                size=170,
                output_dir=str(output_dir),
                user_id="u1",
            )

        assert "[FILE]" in result
        assert "cdn.example.com" in result
        assert "report.xlsx" in result
        assert "✅" in result

    @pytest.mark.asyncio
    async def test_cdn_url_encodes_chinese(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.example.com"
        settings.file_workspace_root = str(ws_root)

        # 创建中文文件名
        f = Path(output_dir) / "销售报表.xlsx"
        f.write_bytes(b"test")

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.return_value = "https://cdn.example.com/workspace/personal/e4774cdd/%E4%B8%8B%E8%BD%BD/%E9%94%80%E5%94%AE%E6%8A%A5%E8%A1%A8.xlsx"

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="销售报表.xlsx",
                size=4,
                output_dir=str(output_dir),
                user_id="u1",
            )

        assert "[FILE]" in result
        assert "✅" in result


class TestAutoUploadOSS:
    """OSS sync 兜底。"""

    @pytest.mark.asyncio
    async def test_oss_sync_success(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = ""
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.return_value = "https://oss.example.com/workspace/file.xlsx"

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="report.xlsx",
                size=170,
                output_dir=str(output_dir),
                user_id="u1",
            )

        assert "[FILE]" in result
        assert "oss.example.com" in result
        mock_oss.sync_workspace_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_oss_sync_failure_with_cdn_fallback(self, workspace_dir):
        """OSS sync 失败但有 cdn_domain → 拼 CDN URL。"""
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.fallback.com"
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.side_effect = Exception("OSS down")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="report.xlsx",
                size=170,
                output_dir=str(output_dir),
                user_id="u1",
            )

        # 有 cdn_domain 时可以兜底拼 URL
        assert "[FILE]" in result
        assert "cdn.fallback.com" in result

    @pytest.mark.asyncio
    async def test_total_failure(self, workspace_dir):
        """OSS sync 失败且无 cdn_domain → 返回错误。"""
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = ""
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.side_effect = Exception("OSS down")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="report.xlsx",
                size=170,
                output_dir=str(output_dir),
                user_id="u1",
            )

        assert "❌" in result


class TestAutoUploadSizeLimit:
    """文件大小不影响上传（当前无大小限制，由 OSS 控制）。"""

    @pytest.mark.asyncio
    async def test_large_file_still_uploads(self, workspace_dir):
        """大文件只要 OSS sync 成功就能上传。"""
        output_dir, ws_root = workspace_dir

        # 创建大文件
        f = Path(output_dir) / "huge.csv"
        f.write_bytes(b"x" * 1000)

        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.example.com"
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.return_value = "https://cdn.example.com/workspace/huge.csv"

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="huge.csv",
                size=150 * 1024 * 1024,
                output_dir=str(output_dir),
                user_id="u1",
            )

        assert "❌" not in result
        assert "[FILE]" in result


class TestAutoUploadMimeType:
    """MIME 类型检测。"""

    @pytest.mark.asyncio
    async def test_xlsx_mime(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.test.com"
        settings.file_workspace_root = str(ws_root)

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.return_value = "https://cdn.test.com/workspace/report.xlsx"

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="report.xlsx", size=100,
                output_dir=str(output_dir), user_id="u1",
            )

        parts = result.split("|")
        assert any("spreadsheet" in p or "xlsx" in p for p in parts)

    @pytest.mark.asyncio
    async def test_csv_mime(self, workspace_dir):
        output_dir, ws_root = workspace_dir
        settings = MagicMock()
        settings.oss_cdn_domain = "cdn.test.com"
        settings.file_workspace_root = str(ws_root)

        f = Path(output_dir) / "data.csv"
        f.write_text("a,b\n1,2")

        mock_oss = AsyncMock()
        mock_oss.sync_workspace_file.return_value = "https://cdn.test.com/workspace/data.csv"

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            result = await auto_upload(
                filename="data.csv", size=7,
                output_dir=str(output_dir), user_id="u1",
            )

        parts = result.split("|")
        assert any("csv" in p or "text" in p for p in parts)
