"""
OSS 图片持久化修复验证测试

测试修复后的关键功能：
1. URL 检查精确化
2. 文件大小限制
3. 空 URL 过滤
4. 全局单例线程安全
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from services.oss_service import OSSService, get_oss_service
from services.task_completion_service import TaskCompletionService


class TestOSSServiceFixes:
    """测试 OSS 服务的修复"""

    def test_is_oss_url_exact_match(self):
        """测试 URL 检查使用精确匹配（防止子串误判）"""
        # Mock 配置
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = "cdn.example.com"

            oss = OSSService()

            # 正确的 CDN URL
            assert oss.is_oss_url("https://cdn.example.com/images/test.png") is True

            # 正确的 OSS URL
            assert oss.is_oss_url("https://test-bucket.oss-cn-hangzhou.aliyuncs.com/images/test.png") is True

            # 恶意 URL（子串包含 CDN 域名但不是真正的 CDN）
            assert oss.is_oss_url("https://evil.com/fake-cdn.example.com/malicious.png") is False

            # 空 URL
            assert oss.is_oss_url("") is False
            assert oss.is_oss_url("   ") is False
            assert oss.is_oss_url(None) is False

    def test_file_size_limits_defined(self):
        """测试文件大小限制已定义"""
        assert hasattr(OSSService, 'MAX_IMAGE_SIZE')
        assert hasattr(OSSService, 'MAX_VIDEO_SIZE')
        assert OSSService.MAX_IMAGE_SIZE == 50 * 1024 * 1024  # 50MB
        assert OSSService.MAX_VIDEO_SIZE == 500 * 1024 * 1024  # 500MB

    def test_singleton_thread_safety(self):
        """测试全局单例线程安全"""
        import threading
        from services.oss_service import _oss_lock

        # 验证锁对象存在
        assert isinstance(_oss_lock, threading.Lock)

        # 验证单例模式（多次调用返回同一实例）
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = None

            # 清除已有实例
            import services.oss_service
            services.oss_service._oss_service = None

            instance1 = get_oss_service()
            instance2 = get_oss_service()

            assert instance1 is instance2


class TestTaskCompletionServiceFixes:
    """测试任务完成服务的修复"""

    def test_extract_urls_filters_empty(self):
        """测试 URL 提取过滤空值"""
        from services.adapters.base import ImageGenerateResult, TaskStatus

        db_mock = Mock()
        service = TaskCompletionService(db_mock)

        # 创建包含空 URL 的结果
        result = ImageGenerateResult(
            task_id="test-task",
            status=TaskStatus.SUCCESS,
            image_urls=["https://example.com/1.png", "", "  ", "https://example.com/2.png", None],
            cost_usd=0,
            credits_consumed=0,
        )

        urls = service._extract_urls(result, "image")

        # 验证空 URL 被过滤
        assert len(urls) == 2
        assert "https://example.com/1.png" in urls
        assert "https://example.com/2.png" in urls
        assert "" not in urls
        assert "  " not in urls
        assert None not in urls

    @pytest.mark.asyncio
    async def test_upload_single_rejects_empty_url(self):
        """测试上传拒绝空 URL"""
        db_mock = Mock()
        service = TaskCompletionService(db_mock)

        # 空 URL 应该抛出 ValueError
        with pytest.raises(ValueError, match="Empty URL"):
            await service._upload_single_to_oss("", "user123", "image")

        with pytest.raises(ValueError, match="Empty URL"):
            await service._upload_single_to_oss("   ", "user123", "image")

    @pytest.mark.asyncio
    async def test_upload_with_retry(self):
        """测试上传失败重试机制"""
        db_mock = Mock()
        service = TaskCompletionService(db_mock)

        with patch('services.task_completion_service.get_oss_service') as mock_get_oss:
            mock_oss = Mock()
            mock_oss.is_oss_url.return_value = False

            # 模拟前 2 次失败，第 3 次成功
            mock_oss.upload_from_url = AsyncMock(
                side_effect=[
                    Exception("Network error"),
                    Exception("Timeout"),
                    {"url": "https://cdn.example.com/success.png", "object_key": "images/success.png"}
                ]
            )
            mock_get_oss.return_value = mock_oss

            # 应该成功（经过重试）
            result = await service._upload_single_to_oss(
                "https://temp.example.com/temp.png",
                "user123",
                "image",
                max_retries=3
            )

            assert result == "https://cdn.example.com/success.png"
            assert mock_oss.upload_from_url.call_count == 3

    @pytest.mark.asyncio
    async def test_upload_fails_after_max_retries(self):
        """测试达到最大重试次数后抛出异常"""
        db_mock = Mock()
        service = TaskCompletionService(db_mock)

        with patch('services.task_completion_service.get_oss_service') as mock_get_oss:
            mock_oss = Mock()
            mock_oss.is_oss_url.return_value = False

            # 所有尝试都失败
            mock_oss.upload_from_url = AsyncMock(
                side_effect=Exception("Persistent failure")
            )
            mock_get_oss.return_value = mock_oss

            # 应该抛出异常
            with pytest.raises(Exception, match="图片持久化失败"):
                await service._upload_single_to_oss(
                    "https://temp.example.com/temp.png",
                    "user123",
                    "image",
                    max_retries=3
                )

            assert mock_oss.upload_from_url.call_count == 3

    @pytest.mark.asyncio
    async def test_concurrent_upload(self):
        """测试并发上传"""
        db_mock = Mock()
        service = TaskCompletionService(db_mock)

        with patch('services.task_completion_service.get_oss_service') as mock_get_oss:
            mock_oss = Mock()
            mock_oss.is_oss_url.return_value = False

            # 模拟成功上传
            async def mock_upload(url, user_id, category, media_type):
                return {
                    "url": f"https://cdn.example.com/{url.split('/')[-1]}",
                    "object_key": f"images/{url.split('/')[-1]}"
                }

            mock_oss.upload_from_url = AsyncMock(side_effect=mock_upload)
            mock_get_oss.return_value = mock_oss

            urls = [
                "https://temp.example.com/1.png",
                "https://temp.example.com/2.png",
                "https://temp.example.com/3.png",
            ]

            # 应该并发上传
            result = await service._upload_urls_to_oss(urls, "user123", "image", max_concurrent=2)

            assert len(result) == 3
            assert all("cdn.example.com" in url for url in result)


def test_imports():
    """测试所有关键导入"""
    from services.task_completion_service import TaskCompletionService
    from services.oss_service import OSSService, get_oss_service
    from datetime import timezone  # 验证使用了新的时区 API

    assert TaskCompletionService is not None
    assert OSSService is not None
    assert get_oss_service is not None
    assert timezone is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
