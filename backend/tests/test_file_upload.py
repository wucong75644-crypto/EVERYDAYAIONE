"""测试 file_upload — upload_to_payload 返回 dict 双轨字段"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import asyncio

import httpx
import pytest

from services.file_upload import (
    build_oss_thumbnail_url,
    download_url_to_workspace,
    persist_media_urls_to_workspace,
    upload_to_payload,
)


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


# ============================================================
# download_url_to_workspace — 远程 URL 下载到工作区并产出双轨 dict
# ============================================================


@pytest.fixture()
def ws_root(tmp_path):
    """模拟 workspace_root,用户 personal/<hash> 子目录稍后由函数自动创建。"""
    return tmp_path


def _patch_settings_and_oss(ws_root_path, oss_url: str | None = "https://cdn.example.com/x.png"):
    """统一注入 settings + oss_service mock。"""
    settings = MagicMock()
    settings.oss_cdn_domain = "cdn.example.com"
    settings.file_workspace_root = str(ws_root_path)

    mock_oss = AsyncMock()
    if oss_url:
        mock_oss.sync_workspace_file.return_value = oss_url
    else:
        mock_oss.sync_workspace_file.return_value = None

    return settings, mock_oss


def _make_downloader_mock(content: bytes, content_type: str = "image/png"):
    """构造一个返回固定内容的 HttpDownloader mock。"""
    mock = MagicMock()
    mock.download = AsyncMock(return_value=(content, content_type))
    mock.close = AsyncMock()
    return mock


class TestDownloadUrlToWorkspace:
    """download_url_to_workspace 的核心路径与边界。"""

    @pytest.mark.asyncio
    async def test_success_returns_dual_track_payload(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(
            ws_root,
            oss_url="https://cdn.everydayai.com.cn/workspace/x.png",
        )
        mock_dl = _make_downloader_mock(b"PNG" * 100, "image/png")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://kie-cdn.example/abc.png",
                user_id="u1",
                org_id=None,
            )

        assert payload is not None
        assert payload["kind"] == "image"
        assert payload["url"].startswith("https://")
        assert payload["original_url"] == payload["url"]
        assert payload["preview_url"] == payload["url"]
        assert payload["download_url"] == payload["url"]
        assert payload["thumbnail_url"].startswith(payload["url"])
        assert "x-oss-process=image/resize" in payload["thumbnail_url"]
        assert "workspace_path" in payload
        # 默认子目录
        assert payload["workspace_path"].startswith("下载/AI图片/")
        # 行业标准命名格式
        assert payload["name"].startswith("IMG_")
        assert payload["name"].endswith(".png")
        assert payload["size"] == 300

    @pytest.mark.asyncio
    async def test_video_uses_vid_prefix_and_subdir(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = _make_downloader_mock(b"MP4" * 50, "video/mp4")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://kie-cdn.example/clip.mp4",
                user_id="u1",
                media_type="video",
            )

        assert payload is not None
        assert payload["kind"] == "video"
        assert payload["name"].startswith("VID_")
        assert payload["name"].endswith(".mp4")
        assert payload["workspace_path"].startswith("下载/AI视频/")

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = MagicMock()
        mock_dl.download = AsyncMock(
            side_effect=ValueError("image URL 已失效(HTTP 404)")
        )
        mock_dl.close = AsyncMock()

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://expired.example/x.png",
                user_id="u1",
            )

        assert payload is None
        # 仍然 close 了 downloader (资源释放)
        mock_dl.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mime_not_in_whitelist_returns_none(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = _make_downloader_mock(b"oops", "application/octet-stream")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://kie-cdn.example/x.bin",
                user_id="u1",
            )

        assert payload is None

    @pytest.mark.asyncio
    async def test_filename_index_renders_with_idx(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = _make_downloader_mock(b"PNG" * 10, "image/png")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://x/y.png", user_id="u1", idx=5,
            )

        assert payload is not None
        # 文件名末尾应包含 _005.png
        assert "_005.png" in payload["name"]

    @pytest.mark.asyncio
    async def test_meta_sidecar_written_as_hidden_file(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = _make_downloader_mock(b"PNG" * 10, "image/png")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://x/y.png",
                user_id="u1",
                meta={"prompt": "a cat", "model": "kie-banana"},
            )

        assert payload is not None
        # 落盘文件
        import hashlib, json as _json
        user_hash = hashlib.md5("u1".encode()).hexdigest()[:8]
        download_dir = ws_root / "personal" / user_hash / "下载" / "AI图片"
        files = list(download_dir.iterdir())
        # 应有 1 个 image + 1 个 hidden .meta.json
        non_hidden = [f for f in files if not f.name.startswith(".")]
        hidden = [f for f in files if f.name.startswith(".")]
        assert len(non_hidden) == 1
        assert len(hidden) == 1
        meta_data = _json.loads(hidden[0].read_text())
        assert meta_data["prompt"] == "a cat"
        assert meta_data["model"] == "kie-banana"
        assert meta_data["source_url"] == "https://x/y.png"

    @pytest.mark.asyncio
    async def test_path_traversal_subdir_rejected(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = _make_downloader_mock(b"PNG", "image/png")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://x/y.png",
                user_id="u1",
                subdir="../../../etc",
            )

        assert payload is None

    @pytest.mark.asyncio
    async def test_org_user_uses_org_subtree(self, ws_root):
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = _make_downloader_mock(b"PNG", "image/png")

        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            payload = await download_url_to_workspace(
                url="https://x/y.png",
                user_id="u-ent-1",
                org_id="org-A",
            )

        assert payload is not None
        # 验证文件落到 workspace/org/org-A/u-ent-1/下载/AI图片/
        org_dir = ws_root / "org" / "org-A" / "u-ent-1" / "下载" / "AI图片"
        assert org_dir.exists()
        non_hidden = [f for f in org_dir.iterdir() if not f.name.startswith(".")]
        assert len(non_hidden) == 1

    @pytest.mark.asyncio
    async def test_filename_collision_auto_suffix(self, ws_root):
        """同名文件已存在时附加 _N 后缀,而不是覆盖。"""
        settings, mock_oss = _patch_settings_and_oss(ws_root)
        mock_dl = _make_downloader_mock(b"PNG", "image/png")

        # 用固定 suggested_name 强制冲突
        with patch("core.config.get_settings", return_value=settings), \
             patch("services.http_downloader.HttpDownloader", return_value=mock_dl), \
             patch("services.oss_service.get_oss_service", return_value=mock_oss):
            p1 = await download_url_to_workspace(
                url="https://x/a.png", user_id="u1",
                suggested_name="fixed.png",
            )
            p2 = await download_url_to_workspace(
                url="https://x/a.png", user_id="u1",
                suggested_name="fixed.png",
            )

        assert p1 is not None and p2 is not None
        assert p1["name"] == "fixed.png"
        assert p2["name"] == "fixed_1.png"


class TestPersistMediaUrlsToWorkspace:
    """persist_media_urls_to_workspace 公共并发 helper。"""

    @pytest.mark.asyncio
    async def test_empty_urls_returns_empty(self):
        result = await persist_media_urls_to_workspace(
            urls=[], user_id="u1",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_order_preserved_and_extra_fields_merged(self):
        """返回顺序与输入一致 + extra_fields 注入每个 payload。"""
        urls = [f"https://cdn/img{i}.png" for i in range(3)]

        async def fake_download(*, url, user_id, org_id, media_type, idx, meta):
            return {
                "kind": "image", "url": f"oss://cdn/{idx}", "workspace_path": f"a/b{idx}.png",
                "name": f"b{idx}.png", "mime_type": "image/png", "size": 100,
            }

        with patch(
            "services.file_upload.download_url_to_workspace", new=AsyncMock(side_effect=fake_download)
        ):
            payloads = await persist_media_urls_to_workspace(
                urls=urls, user_id="u1",
                extra_fields={"width": 800, "height": 800, "alt": "test"},
            )

        assert len(payloads) == 3
        # 顺序保持(workspace_path 末尾数字 = 输入 idx)
        for i, p in enumerate(payloads, start=1):
            assert p["workspace_path"].endswith(f"b{i}.png")
            # extra_fields 被注入
            assert p["width"] == 800
            assert p["alt"] == "test"

    @pytest.mark.asyncio
    async def test_fallback_when_download_returns_none(self):
        """单张下载失败时降级保留原 url + extra_fields,顺序仍正确。"""
        urls = ["https://cdn/a.png", "https://cdn/b.png"]

        async def fake_download(*, url, user_id, org_id, media_type, idx, meta):
            if idx == 1:
                return None  # 模拟首张落盘失败
            return {
                "kind": "image", "url": "oss://cdn/2", "workspace_path": "下载/AI图片/x.png",
                "name": "x.png", "mime_type": "image/png", "size": 50,
            }

        with patch(
            "services.file_upload.download_url_to_workspace", new=AsyncMock(side_effect=fake_download)
        ):
            payloads = await persist_media_urls_to_workspace(
                urls=urls, user_id="u1",
                extra_fields={"width": 100, "height": 100, "alt": "x"},
            )

        assert payloads[0] == {
            "kind": "image", "url": "https://cdn/a.png",
            "original_url": "https://cdn/a.png",
            "preview_url": "https://cdn/a.png",
            "download_url": "https://cdn/a.png",
            "thumbnail_url": "https://cdn/a.png",
            "width": 100, "height": 100, "alt": "x",
        }
        assert payloads[1]["workspace_path"] == "下载/AI图片/x.png"
        assert payloads[1]["width"] == 100  # extra_fields 仍合入成功 payload

    @pytest.mark.asyncio
    async def test_concurrency_limited_by_semaphore(self):
        """max_concurrency=2 时最多 2 个下载并行(不会全部串行也不会无限并发)。"""
        urls = [f"https://cdn/img{i}.png" for i in range(5)]
        in_flight = 0
        peak = 0
        lock = asyncio.Lock()

        async def fake_download(*, url, user_id, org_id, media_type, idx, meta):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return {
                "kind": "image", "url": f"oss/{idx}", "workspace_path": f"p/{idx}.png",
                "name": f"{idx}.png", "mime_type": "image/png", "size": 10,
            }

        with patch(
            "services.file_upload.download_url_to_workspace", new=AsyncMock(side_effect=fake_download)
        ):
            await persist_media_urls_to_workspace(
                urls=urls, user_id="u1", max_concurrency=2,
            )

        assert peak <= 2, f"semaphore failed | peak={peak}"
        assert peak >= 2, f"实际并发不足,可能退化为串行 | peak={peak}"


class TestBuildOssThumbnailUrl:
    """缩略图 URL 仅对项目 OSS/CDN 生效。"""

    def test_project_cdn_adds_oss_process(self):
        result = build_oss_thumbnail_url(
            "https://cdn.everydayai.com.cn/workspace/a.png",
            width=160,
        )

        assert result == (
            "https://cdn.everydayai.com.cn/workspace/a.png"
            "?x-oss-process=image/resize,w_160,m_lfit"
        )

    def test_external_cdn_returns_original_url(self):
        url = "https://kie-cdn.example.com/generated/a.png"

        assert build_oss_thumbnail_url(url) == url
