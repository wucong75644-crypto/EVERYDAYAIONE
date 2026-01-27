"""
image_service 单元测试

测试图像生成服务的核心功能：
- 图像生成
- 图像编辑
- 积分检查
- 任务查询
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.image_service import ImageService
from core.exceptions import InsufficientCreditsError, AppException
from tests.conftest import create_test_user


class TestImageServiceGenerate:
    """图像生成测试"""

    @pytest.fixture
    def image_service(self, mock_db):
        with patch("services.image_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return ImageService(mock_db)

    @pytest.mark.asyncio
    async def test_generate_image_success(self, image_service, mock_db):
        """测试：图像生成成功"""
        # Arrange
        user = create_test_user(credits=100)
        mock_db.set_table_data("users", [user])

        # Mock _get_user
        with patch.object(image_service, "_get_user", return_value=user):
            # Mock _deduct_credits
            with patch.object(image_service, "_deduct_credits", return_value=None):
                # Mock KieClient
                with patch("services.image_service.KieClient") as mock_client:
                    mock_adapter = AsyncMock()
                    mock_adapter.generate.return_value = {
                        "task_id": "task_123",
                        "status": "processing"
                    }
                    mock_client.return_value.__aenter__.return_value = MagicMock()

                    with patch("services.image_service.KieImageAdapter", return_value=mock_adapter):
                        # Act
                        result = await image_service.generate_image(
                            user_id=user["id"],
                            prompt="一只可爱的猫咪",
                            model="google/nano-banana",
                            wait_for_result=False
                        )

        # Assert
        assert result["task_id"] == "task_123"
        assert result["status"] == "processing"

    @pytest.mark.asyncio
    async def test_generate_image_insufficient_credits(self, image_service, mock_db):
        """测试：积分不足"""
        # Arrange
        user = create_test_user(credits=1)  # 积分不足
        mock_db.set_table_data("users", [user])

        with patch.object(image_service, "_get_user", return_value=user):
            with patch.object(image_service, "_estimate_credits", return_value=10):
                # Act & Assert
                with pytest.raises(InsufficientCreditsError):
                    await image_service.generate_image(
                        user_id=user["id"],
                        prompt="测试",
                        model="google/nano-banana"
                    )

    @pytest.mark.asyncio
    async def test_generate_image_api_error(self, image_service, mock_db):
        """测试：API 调用失败"""
        # Arrange
        user = create_test_user(credits=100)
        mock_db.set_table_data("users", [user])

        from services.adapters.kie.client import KieAPIError

        with patch.object(image_service, "_get_user", return_value=user):
            with patch.object(image_service, "_deduct_credits", return_value=None):
                with patch("services.image_service.KieClient") as mock_client:
                    mock_client.return_value.__aenter__.side_effect = KieAPIError("API error")

                    # Act & Assert
                    with pytest.raises(AppException) as exc_info:
                        await image_service.generate_image(
                            user_id=user["id"],
                            prompt="测试",
                            model="google/nano-banana"
                        )

                    assert "图像生成失败" in str(exc_info.value)


class TestImageServiceEdit:
    """图像编辑测试"""

    @pytest.fixture
    def image_service(self, mock_db):
        with patch("services.image_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return ImageService(mock_db)

    @pytest.mark.asyncio
    async def test_edit_image_success(self, image_service, mock_db):
        """测试：图像编辑成功"""
        # Arrange
        user = create_test_user(credits=100)
        mock_db.set_table_data("users", [user])

        with patch.object(image_service, "_get_user", return_value=user):
            with patch.object(image_service, "_deduct_credits", return_value=None):
                with patch("services.image_service.KieClient") as mock_client:
                    mock_adapter = AsyncMock()
                    mock_adapter.edit.return_value = {
                        "task_id": "edit_123",
                        "status": "processing"
                    }
                    mock_client.return_value.__aenter__.return_value = MagicMock()

                    with patch("services.image_service.KieImageAdapter", return_value=mock_adapter):
                        # Act
                        result = await image_service.edit_image(
                            user_id=user["id"],
                            prompt="添加背景",
                            image_urls=["https://example.com/image.jpg"],
                            wait_for_result=False
                        )

        # Assert
        assert result["task_id"] == "edit_123"

    @pytest.mark.asyncio
    async def test_edit_image_insufficient_credits(self, image_service, mock_db):
        """测试：编辑时积分不足"""
        # Arrange
        user = create_test_user(credits=1)
        mock_db.set_table_data("users", [user])

        with patch.object(image_service, "_get_user", return_value=user):
            with patch.object(image_service, "_estimate_credits", return_value=10):
                # Act & Assert
                with pytest.raises(InsufficientCreditsError):
                    await image_service.edit_image(
                        user_id=user["id"],
                        prompt="测试编辑",
                        image_urls=["https://example.com/image.jpg"]
                    )


class TestImageServiceQueryTask:
    """任务查询测试"""

    @pytest.fixture
    def image_service(self, mock_db):
        with patch("services.image_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return ImageService(mock_db)

    @pytest.mark.asyncio
    async def test_query_task_success(self, image_service):
        """测试：查询任务成功"""
        # Arrange
        with patch("services.image_service.KieClient") as mock_client:
            mock_adapter = AsyncMock()
            mock_adapter.query_task.return_value = {
                "task_id": "task_123",
                "status": "success",
                "image_urls": ["https://example.com/result.jpg"]
            }
            mock_client.return_value.__aenter__.return_value = MagicMock()

            with patch("services.image_service.KieImageAdapter", return_value=mock_adapter):
                # Act
                result = await image_service.query_task("task_123")

        # Assert
        assert result["status"] == "success"
        assert len(result["image_urls"]) == 1

    @pytest.mark.asyncio
    async def test_query_task_still_processing(self, image_service):
        """测试：任务仍在处理"""
        # Arrange
        with patch("services.image_service.KieClient") as mock_client:
            mock_adapter = AsyncMock()
            mock_adapter.query_task.return_value = {
                "task_id": "task_123",
                "status": "processing",
                "progress": 50
            }
            mock_client.return_value.__aenter__.return_value = MagicMock()

            with patch("services.image_service.KieImageAdapter", return_value=mock_adapter):
                # Act
                result = await image_service.query_task("task_123")

        # Assert
        assert result["status"] == "processing"
        assert result["progress"] == 50


class TestImageServiceCredits:
    """积分估算测试"""

    @pytest.fixture
    def image_service(self, mock_db):
        with patch("services.image_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return ImageService(mock_db)

    def test_estimate_credits_basic(self, image_service):
        """测试：基础积分估算"""
        # 测试默认模型的积分估算
        credits = image_service._estimate_credits("google/nano-banana")
        assert credits > 0

    def test_estimate_credits_pro_model(self, image_service):
        """测试：Pro 模型积分估算"""
        # Pro 模型应该消耗更多积分
        basic_credits = image_service._estimate_credits("google/nano-banana")
        pro_credits = image_service._estimate_credits("google/nano-banana-pro", "4k")

        # Pro 模型应该更贵
        assert pro_credits >= basic_credits
