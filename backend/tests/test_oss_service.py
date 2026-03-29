"""
oss_service 完整单元测试

测试 OSS 服务的核心功能：
- 初始化和配置
- 从 URL 上传
- 上传字节数据
- 删除文件
- URL 生成和检查
- 文件存在性检查
- 辅助方法
"""

import sys
from pathlib import Path

# Python path fix: 避免与根目录的 tests/ 冲突
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

from services.oss_service import OSSService, get_oss_service


class TestOSSServiceInit:
    """测试 OSS 服务初始化"""

    def test_init_success(self):
        """测试：成功初始化"""
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = "cdn.example.com"

            with patch('services.oss_service.oss2.Bucket'):
                oss = OSSService()

                assert oss.cdn_domain == "cdn.example.com"
                assert oss.external_endpoint == "oss-cn-hangzhou.aliyuncs.com"

    def test_init_missing_config(self):
        """测试：配置不完整抛出异常"""
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = None  # 缺少 key
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"

            with pytest.raises(ValueError, match="OSS 配置不完整"):
                OSSService()

    def test_init_uses_internal_endpoint(self):
        """测试：优先使用内网端点"""
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_internal_endpoint = "oss-cn-hangzhou-internal.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_cdn_domain = None

            with patch('services.oss_service.oss2.Bucket') as mock_bucket:
                OSSService()

                # 验证使用了内网端点
                mock_bucket.assert_called_once()
                call_args = mock_bucket.call_args
                assert call_args[0][1] == "oss-cn-hangzhou-internal.aliyuncs.com"


class TestOSSServiceUploadFromURL:
    """测试从 URL 上传"""

    @pytest.fixture
    def oss_service(self):
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = "cdn.example.com"

            with patch('services.oss_service.oss2.Bucket'):
                return OSSService()

    @pytest.mark.asyncio
    async def test_upload_from_url_success(self, oss_service):
        """测试：成功从 URL 上传图片"""
        # Arrange
        test_url = "https://example.com/image.png"
        user_id = str(uuid4())
        image_content = b"fake_image_content"

        # Mock downloader.download 返回内容
        mock_downloader = AsyncMock()
        mock_downloader.download = AsyncMock(return_value=(image_content, "image/png"))
        oss_service._downloader = mock_downloader

        # Mock OSS upload
        mock_upload_result = Mock()
        mock_upload_result.etag = "test_etag"

        async def mock_to_thread(func, *args, **kwargs):
            return mock_upload_result

        with patch.object(oss_service, 'bucket') as mock_bucket:
            with patch('asyncio.to_thread', side_effect=mock_to_thread):
                # Act
                result = await oss_service.upload_from_url(
                    url=test_url,
                    user_id=user_id,
                    category="generated",
                    media_type="image"
                )

        # Assert
        assert "object_key" in result
        assert "url" in result
        assert "size" in result
        assert "content_type" in result
        assert result["size"] == len(image_content)
        assert "cdn.example.com" in result["url"]

    @pytest.mark.asyncio
    async def test_upload_from_url_file_too_large(self, oss_service):
        """测试：文件过大抛出异常（在下载过程中检测）"""
        # Arrange
        test_url = "https://example.com/large_image.png"
        user_id = str(uuid4())

        # Mock downloader.download 抛出文件过大异常
        mock_downloader = AsyncMock()
        mock_downloader.download = AsyncMock(
            side_effect=ValueError("image下载超限: >50.0MB")
        )
        oss_service._downloader = mock_downloader

        # Act & Assert
        with pytest.raises(ValueError, match="下载超限"):
            await oss_service.upload_from_url(
                url=test_url,
                user_id=user_id,
                media_type="image"
            )

    @pytest.mark.asyncio
    async def test_upload_from_url_with_fallback_extension(self, oss_service):
        """测试：不支持的格式会使用默认扩展名"""
        # Arrange - 使用不支持的 content-type，OSS 会 fallback 到 png
        test_url = "https://example.com/file"
        user_id = str(uuid4())
        content = b"fake_content"

        # Mock downloader.download 返回 bmp content-type
        mock_downloader = AsyncMock()
        mock_downloader.download = AsyncMock(return_value=(content, "image/bmp"))
        oss_service._downloader = mock_downloader

        mock_upload_result = Mock()
        mock_upload_result.etag = "test_etag"

        async def mock_to_thread(func, *args, **kwargs):
            return mock_upload_result

        with patch('asyncio.to_thread', side_effect=mock_to_thread):
            # Act - OSS 服务有 fallback 逻辑，会使用默认的 png
            result = await oss_service.upload_from_url(
                url=test_url,
                user_id=user_id,
                media_type="image"
            )

        # Assert - 验证使用了 fallback 扩展名 (png)
        assert result["object_key"].endswith(".png")


class TestOSSServiceUploadBytes:
    """测试上传字节数据"""

    @pytest.fixture
    def oss_service(self):
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = "cdn.example.com"

            with patch('services.oss_service.oss2.Bucket'):
                return OSSService()

    def test_upload_bytes_success(self, oss_service):
        """测试：成功上传字节数据"""
        # Arrange
        content = b"fake_image_data"
        user_id = str(uuid4())

        mock_result = Mock()
        mock_result.etag = "test_etag"

        with patch.object(oss_service, 'bucket') as mock_bucket:
            mock_bucket.put_object.return_value = mock_result

            # Act
            result = oss_service.upload_bytes(
                content=content,
                user_id=user_id,
                ext="png",
                category="uploaded"
            )

        # Assert
        assert "object_key" in result
        assert "url" in result
        assert "size" in result
        assert result["size"] == len(content)
        assert "cdn.example.com" in result["url"]

    def test_upload_bytes_unsupported_format(self, oss_service):
        """测试：不支持的格式抛出异常"""
        # Act & Assert
        with pytest.raises(ValueError, match="不支持的.*格式"):
            oss_service.upload_bytes(
                content=b"data",
                user_id="user_123",
                ext="bmp"  # 不支持
            )


class TestOSSServiceDelete:
    """测试删除功能"""

    @pytest.fixture
    def oss_service(self):
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = "cdn.example.com"

            with patch('services.oss_service.oss2.Bucket'):
                return OSSService()

    def test_delete_success(self, oss_service):
        """测试：成功删除文件"""
        # Arrange
        object_key = "images/generated/2026/01/27/test.png"

        with patch.object(oss_service, 'bucket') as mock_bucket:
            mock_bucket.delete_object.return_value = Mock()

            # Act
            result = oss_service.delete(object_key)

        # Assert
        assert result is True
        mock_bucket.delete_object.assert_called_once_with(object_key)

    def test_delete_by_url_success(self, oss_service):
        """测试：通过 URL 删除文件"""
        # Arrange
        url = "https://cdn.example.com/images/generated/2026/01/27/test.png"
        object_key = "images/generated/2026/01/27/test.png"

        with patch.object(oss_service, '_extract_object_key', return_value=object_key):
            with patch.object(oss_service, 'delete', return_value=True) as mock_delete:
                # Act
                result = oss_service.delete_by_url(url)

        # Assert
        assert result is True
        mock_delete.assert_called_once_with(object_key)

    def test_delete_by_url_invalid_url(self, oss_service):
        """测试：无效 URL 返回 False"""
        # Arrange
        url = "https://other-domain.com/image.png"

        with patch.object(oss_service, '_extract_object_key', return_value=None):
            # Act
            result = oss_service.delete_by_url(url)

        # Assert
        assert result is False


class TestOSSServiceURLGeneration:
    """测试 URL 生成和检查"""

    @pytest.fixture
    def oss_service(self):
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = "cdn.example.com"

            with patch('services.oss_service.oss2.Bucket'):
                return OSSService()

    def test_get_url_with_cdn(self, oss_service):
        """测试：使用 CDN 域名生成 URL"""
        # Arrange
        object_key = "images/test.png"

        # Act
        url = oss_service.get_url(object_key)

        # Assert
        assert url == "https://cdn.example.com/images/test.png"

    def test_get_url_without_cdn(self):
        """测试：不使用 CDN 时使用 OSS 域名"""
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = None  # 无 CDN

            with patch('services.oss_service.oss2.Bucket'):
                oss = OSSService()

                # Act
                url = oss.get_url("images/test.png")

        # Assert
        assert "test-bucket.oss-cn-hangzhou.aliyuncs.com" in url

    def test_is_oss_url_cdn_domain(self, oss_service):
        """测试：CDN 域名 URL 识别"""
        # Act & Assert
        assert oss_service.is_oss_url("https://cdn.example.com/images/test.png") is True
        assert oss_service.is_oss_url("http://cdn.example.com/images/test.png") is True

    def test_is_oss_url_oss_domain(self):
        """测试：OSS 域名 URL 识别"""
        # 需要重新创建 service 以确保 settings.oss_bucket_name 被正确引用
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = None

            with patch('services.oss_service.oss2.Bucket'):
                oss = OSSService()

                # Act & Assert
                assert oss.is_oss_url("https://test-bucket.oss-cn-hangzhou.aliyuncs.com/images/test.png") is True

    def test_is_oss_url_other_domain(self, oss_service):
        """测试：其他域名返回 False"""
        # Act & Assert
        assert oss_service.is_oss_url("https://other.com/image.png") is False
        assert oss_service.is_oss_url("") is False
        assert oss_service.is_oss_url(None) is False


class TestOSSServiceFileExists:
    """测试文件存在性检查"""

    @pytest.fixture
    def oss_service(self):
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = None

            with patch('services.oss_service.oss2.Bucket'):
                return OSSService()

    def test_exists_file_present(self, oss_service):
        """测试：文件存在返回 True"""
        # Arrange
        object_key = "images/test.png"

        with patch.object(oss_service, 'bucket') as mock_bucket:
            mock_bucket.object_exists.return_value = True

            # Act
            result = oss_service.exists(object_key)

        # Assert
        assert result is True

    def test_exists_file_absent(self, oss_service):
        """测试：文件不存在返回 False"""
        # Arrange
        object_key = "images/nonexistent.png"

        with patch.object(oss_service, 'bucket') as mock_bucket:
            mock_bucket.object_exists.return_value = False

            # Act
            result = oss_service.exists(object_key)

        # Assert
        assert result is False


class TestOSSServiceHelperMethods:
    """测试辅助方法"""

    @pytest.fixture
    def oss_service(self):
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = "cdn.example.com"

            with patch('services.oss_service.oss2.Bucket'):
                return OSSService()

    def test_generate_object_key(self, oss_service):
        """测试：生成对象键（散客 + 企业）"""
        import hashlib
        user_hash = hashlib.md5("user_123".encode()).hexdigest()[:8]

        # 散客（无 org_id）→ personal/{user_hash}/...
        object_key = oss_service._generate_object_key(
            user_id="user_123",
            category="generated",
            ext="png",
            prefix="images"
        )
        assert object_key.startswith(f"personal/{user_hash}/images/generated/")
        assert object_key.endswith(".png")
        assert user_hash in object_key

        # 企业用户 → org/{org_id}/...
        object_key_org = oss_service._generate_object_key(
            user_id="user_123",
            category="generated",
            ext="png",
            prefix="images",
            org_id="org_abc",
        )
        assert object_key_org.startswith("org/org_abc/images/generated/")
        assert object_key_org.endswith(".png")

    def test_get_extension_from_url(self, oss_service):
        """测试：从 URL 提取扩展名"""
        # Act
        ext = oss_service._get_extension(
            url="https://example.com/image.png",
            content_type="image/png",
            media_type="image"
        )

        # Assert
        assert ext == "png"

    def test_get_extension_from_content_type(self, oss_service):
        """测试：从 Content-Type 提取扩展名"""
        # Act
        ext = oss_service._get_extension(
            url="https://example.com/file",
            content_type="image/jpeg",
            media_type="image"
        )

        # Assert
        # OSS 服务将 jpeg 标准化为 jpg
        assert ext == "jpg"

    def test_extract_object_key_from_cdn(self, oss_service):
        """测试：从 CDN URL 提取对象键"""
        # Act
        object_key = oss_service._extract_object_key(
            "https://cdn.example.com/images/generated/2026/01/27/test.png"
        )

        # Assert
        assert object_key == "images/generated/2026/01/27/test.png"

    def test_extract_object_key_from_oss(self, oss_service):
        """测试：从 OSS URL 提取对象键"""
        # Act
        object_key = oss_service._extract_object_key(
            "https://test-bucket.oss-cn-hangzhou.aliyuncs.com/images/test.png"
        )

        # Assert
        assert object_key == "images/test.png"

    def test_extract_object_key_invalid_url(self, oss_service):
        """测试：无效 URL 返回 None"""
        # Act
        object_key = oss_service._extract_object_key("https://other.com/image.png")

        # Assert
        assert object_key is None


class TestOSSServiceFactory:
    """测试工厂函数"""

    def test_get_oss_service(self):
        """测试：get_oss_service 工厂函数"""
        with patch('services.oss_service.settings') as mock_settings:
            mock_settings.oss_access_key_id = "test_key"
            mock_settings.oss_access_key_secret = "test_secret"
            mock_settings.oss_endpoint = "oss-cn-hangzhou.aliyuncs.com"
            mock_settings.oss_bucket_name = "test-bucket"
            mock_settings.oss_internal_endpoint = None
            mock_settings.oss_cdn_domain = None

            with patch('services.oss_service.oss2.Bucket'):
                # Act
                oss = get_oss_service()

        # Assert
        assert isinstance(oss, OSSService)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
