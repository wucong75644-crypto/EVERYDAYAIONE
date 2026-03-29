"""
OSS 上传高失败率修复 - 单元测试

覆盖以下修复点：
1. 连接池复用（HttpDownloader.get_client）
2. 细粒度超时配置（connect=10, read=60/120）
3. 错误类型保留（TimeoutException / HTTPStatusError 不被包装为 ValueError）
4. HTTP 403/404/410 → ValueError（不可重试）
5. 批量上传部分成功（return_exceptions=True）
6. Full Jitter 退避
7. 不可重试错误跳过重试
8. close 方法
"""

import sys
from pathlib import Path

# Python path fix
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx

from services.http_downloader import HttpDownloader


# ============================================================
# HttpDownloader 连接池测试
# ============================================================

class TestHTTPClientPool:
    """测试 HTTP 客户端连接池"""

    @pytest.fixture
    def downloader(self):
        return HttpDownloader()

    @pytest.mark.asyncio
    async def test_client_reuse(self, downloader):
        """多次调用 get_client() 返回同一实例"""
        client1 = await downloader.get_client()
        client2 = await downloader.get_client()
        assert client1 is client2
        await downloader.close()

    @pytest.mark.asyncio
    async def test_client_recreate_after_close(self, downloader):
        """客户端关闭后重新创建"""
        client1 = await downloader.get_client()
        await downloader.close()
        assert downloader._client is None

        client2 = await downloader.get_client()
        assert client2 is not client1
        await downloader.close()

    @pytest.mark.asyncio
    async def test_client_timeout_config(self, downloader):
        """默认超时配置：connect=10, read=60, write=10, pool=10"""
        client = await downloader.get_client()
        timeout = client.timeout
        assert timeout.connect == 10.0
        assert timeout.read == 60.0
        assert timeout.write == 10.0
        assert timeout.pool == 10.0
        await downloader.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, downloader):
        """close 可多次调用不报错"""
        await downloader.get_client()
        await downloader.close()
        await downloader.close()  # 第二次不应报错
        assert downloader._client is None


# ============================================================
# 错误类型保留测试
# ============================================================

class TestErrorPreservation:
    """测试下载错误不被包装为通用 ValueError"""

    @pytest.fixture
    def downloader(self):
        return HttpDownloader()

    @pytest.mark.asyncio
    async def test_timeout_preserves_type(self, downloader):
        """超时错误保留 TimeoutException 类型（不被包装为 ValueError）"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.head = AsyncMock(return_value=Mock(headers={}))
        mock_client.stream = Mock(side_effect=httpx.ReadTimeout("read timeout"))

        downloader._client = mock_client

        with pytest.raises(httpx.TimeoutException):
            await downloader.download(
                url="https://example.com/image.png",
                user_id="user_123",
                media_type="image",
                max_size=50 * 1024 * 1024,
            )

    @pytest.mark.asyncio
    async def test_http_403_raises_valueerror(self, downloader):
        """HTTP 403 → ValueError（不可重试）"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.head = AsyncMock(return_value=Mock(headers={}))

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.headers = {}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403 Forbidden", request=Mock(), response=mock_response
        )

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__.return_value = mock_response
        mock_stream_ctx.__aexit__.return_value = None
        mock_client.stream = Mock(return_value=mock_stream_ctx)

        downloader._client = mock_client

        with pytest.raises(ValueError, match="URL 已失效.*403"):
            await downloader.download(
                url="https://example.com/expired.png",
                user_id="user_123",
                media_type="image",
                max_size=50 * 1024 * 1024,
            )

    @pytest.mark.asyncio
    async def test_http_404_raises_valueerror(self, downloader):
        """HTTP 404 → ValueError（不可重试）"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.head = AsyncMock(return_value=Mock(headers={}))

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.headers = {}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=Mock(), response=mock_response
        )

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__.return_value = mock_response
        mock_stream_ctx.__aexit__.return_value = None
        mock_client.stream = Mock(return_value=mock_stream_ctx)

        downloader._client = mock_client

        with pytest.raises(ValueError, match="URL 已失效.*404"):
            await downloader.download(
                url="https://example.com/gone.png",
                user_id="user_123",
                media_type="image",
                max_size=50 * 1024 * 1024,
            )

    @pytest.mark.asyncio
    async def test_http_500_preserves_type(self, downloader):
        """HTTP 500 → 保留 HTTPStatusError（可重试）"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.head = AsyncMock(return_value=Mock(headers={}))

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.headers = {}
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error", request=Mock(), response=mock_response
        )

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__.return_value = mock_response
        mock_stream_ctx.__aexit__.return_value = None
        mock_client.stream = Mock(return_value=mock_stream_ctx)

        downloader._client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            await downloader.download(
                url="https://example.com/server-error.png",
                user_id="user_123",
                media_type="image",
                max_size=50 * 1024 * 1024,
            )


# ============================================================
# 批量上传部分成功测试
# ============================================================

class TestBatchPartialSuccess:
    """测试批量上传部分成功模式"""

    @pytest.mark.asyncio
    async def test_partial_success_uses_temp_url(self):
        """4 张图 1 张失败 → 3 个 OSS URL + 1 个原始临时 URL"""
        from services.task_completion_service import TaskCompletionService

        db_mock = MagicMock()
        service = TaskCompletionService(db_mock)

        urls = [
            "https://kie.com/img1.png",
            "https://kie.com/img2.png",
            "https://kie.com/img3.png",
            "https://kie.com/img4.png",
        ]

        async def mock_upload(url, user_id, media_type, max_retries=3, org_id=None):
            if url == "https://kie.com/img3.png":
                raise Exception("timeout")
            return f"https://cdn.example.com/oss_{url.split('/')[-1]}"

        with patch.object(service, '_upload_single_to_oss', side_effect=mock_upload):
            result = await service._upload_urls_to_oss(urls, "user_123", "image")

        assert len(result) == 4
        assert result[0] == "https://cdn.example.com/oss_img1.png"
        assert result[1] == "https://cdn.example.com/oss_img2.png"
        assert result[2] == "https://kie.com/img3.png"  # 降级使用原始 URL
        assert result[3] == "https://cdn.example.com/oss_img4.png"

    @pytest.mark.asyncio
    async def test_all_success(self):
        """全部成功时正常返回 OSS URL"""
        from services.task_completion_service import TaskCompletionService

        db_mock = MagicMock()
        service = TaskCompletionService(db_mock)

        urls = ["https://kie.com/img1.png", "https://kie.com/img2.png"]

        async def mock_upload(url, user_id, media_type, max_retries=3, org_id=None):
            return f"https://cdn.example.com/oss_{url.split('/')[-1]}"

        with patch.object(service, '_upload_single_to_oss', side_effect=mock_upload):
            result = await service._upload_urls_to_oss(urls, "user_123", "image")

        assert result == [
            "https://cdn.example.com/oss_img1.png",
            "https://cdn.example.com/oss_img2.png",
        ]

    @pytest.mark.asyncio
    async def test_empty_urls(self):
        """空 URL 列表返回空列表"""
        from services.task_completion_service import TaskCompletionService

        db_mock = MagicMock()
        service = TaskCompletionService(db_mock)

        result = await service._upload_urls_to_oss([], "user_123", "image")
        assert result == []


# ============================================================
# Full Jitter 退避 + 不可重试错误测试
# ============================================================

class TestRetryWithJitter:
    """测试 Full Jitter 退避和不可重试错误"""

    @pytest.mark.asyncio
    async def test_valueerror_skips_retry(self):
        """ValueError（URL 已失效）不触发重试，立即失败"""
        from services.task_completion_service import TaskCompletionService

        db_mock = MagicMock()
        service = TaskCompletionService(db_mock)

        call_count = 0

        async def mock_upload_from_url(**kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("image URL 已失效(HTTP 403)")

        mock_oss = MagicMock()
        mock_oss.is_oss_url.return_value = False
        mock_oss.upload_from_url = mock_upload_from_url

        with patch('services.task_completion_service.get_oss_service', return_value=mock_oss):
            with pytest.raises(ValueError, match="URL 已失效"):
                await service._upload_single_to_oss(
                    url="https://kie.com/expired.png",
                    user_id="user_123",
                    media_type="image",
                    max_retries=3,
                )

        # 只调用了 1 次，没有重试
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_retries(self):
        """可重试错误使用重试（前 2 次失败，第 3 次成功）"""
        from services.task_completion_service import TaskCompletionService

        db_mock = MagicMock()
        service = TaskCompletionService(db_mock)

        call_count = 0

        async def mock_upload_from_url(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ReadTimeout("timeout")
            return {"object_key": "images/test.png", "url": "https://cdn.example.com/test.png"}

        mock_oss = MagicMock()
        mock_oss.is_oss_url.return_value = False
        mock_oss.upload_from_url = mock_upload_from_url

        with patch('services.task_completion_service.get_oss_service', return_value=mock_oss):
            result = await service._upload_single_to_oss(
                url="https://kie.com/img.png",
                user_id="user_123",
                media_type="image",
                max_retries=3,
            )

        assert result == "https://cdn.example.com/test.png"
        assert call_count == 3  # 2 次失败 + 1 次成功

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        """所有重试耗尽后抛出异常"""
        from services.task_completion_service import TaskCompletionService

        db_mock = MagicMock()
        service = TaskCompletionService(db_mock)

        async def mock_upload_from_url(**kwargs):
            raise httpx.ConnectTimeout("connect timeout")

        mock_oss = MagicMock()
        mock_oss.is_oss_url.return_value = False
        mock_oss.upload_from_url = mock_upload_from_url

        with patch('services.task_completion_service.get_oss_service', return_value=mock_oss):
            with pytest.raises(Exception, match="媒体持久化失败.*已重试3次"):
                await service._upload_single_to_oss(
                    url="https://kie.com/img.png",
                    user_id="user_123",
                    media_type="image",
                    max_retries=3,
                )


# ============================================================
# 视频超时配置测试
# ============================================================

class TestVideoTimeout:
    """测试视频使用更长的 read timeout"""

    @pytest.fixture
    def downloader(self):
        return HttpDownloader()

    @pytest.mark.asyncio
    async def test_video_read_timeout_120s(self, downloader):
        """视频下载使用 read=120s 的超时"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.head = AsyncMock(return_value=Mock(headers={}))

        captured_timeout = None

        async def mock_aiter_bytes(chunk_size):
            yield b"video_content"

        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "video/mp4"}
        mock_response.raise_for_status = Mock()
        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__.return_value = mock_response
        mock_stream_ctx.__aexit__.return_value = None

        def capture_stream(*args, **kwargs):
            nonlocal captured_timeout
            captured_timeout = kwargs.get('timeout')
            return mock_stream_ctx

        mock_client.stream = capture_stream
        downloader._client = mock_client

        await downloader.download(
            url="https://example.com/video.mp4",
            user_id="user_123",
            media_type="video",
            max_size=500 * 1024 * 1024,
        )

        assert captured_timeout is not None
        assert captured_timeout.read == 120.0

    @pytest.mark.asyncio
    async def test_image_read_timeout_60s(self, downloader):
        """图片下载使用 read=60s 的超时"""
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.head = AsyncMock(return_value=Mock(headers={}))

        captured_timeout = None

        async def mock_aiter_bytes(chunk_size):
            yield b"image_content"

        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.raise_for_status = Mock()
        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__.return_value = mock_response
        mock_stream_ctx.__aexit__.return_value = None

        def capture_stream(*args, **kwargs):
            nonlocal captured_timeout
            captured_timeout = kwargs.get('timeout')
            return mock_stream_ctx

        mock_client.stream = capture_stream
        downloader._client = mock_client

        await downloader.download(
            url="https://example.com/image.png",
            user_id="user_123",
            media_type="image",
            max_size=50 * 1024 * 1024,
        )

        assert captured_timeout is not None
        assert captured_timeout.read == 60.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
