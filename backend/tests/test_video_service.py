"""
video_service 单元测试

测试视频生成服务的核心功能：
- 文生视频
- 图生视频
- 积分检查
- 任务查询
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.video_service import VideoService
from core.exceptions import InsufficientCreditsError, AppException
from tests.conftest import create_test_user


class TestVideoServiceTextToVideo:
    """文生视频测试"""

    @pytest.fixture
    def video_service(self, mock_db):
        with patch("services.video_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return VideoService(mock_db)

    @pytest.mark.asyncio
    async def test_generate_text_to_video_success(self, video_service, mock_db):
        """测试：文生视频成功"""
        # Arrange
        user = create_test_user(credits=500)
        mock_db.set_table_data("users", [user])

        with patch.object(video_service, "_get_user", return_value=user):
            with patch.object(video_service, "_estimate_video_credits", return_value=50):
                with patch.object(video_service, "_deduct_credits", return_value=None):
                    with patch("services.video_service.KieClient") as mock_client:
                        mock_adapter = AsyncMock()
                        mock_adapter.generate.return_value = {
                            "task_id": "video_123",
                            "status": "processing"
                        }
                        mock_client.return_value.__aenter__.return_value = MagicMock()

                        with patch("services.video_service.KieVideoAdapter", return_value=mock_adapter):
                            # Act
                            result = await video_service.generate_text_to_video(
                                user_id=user["id"],
                                prompt="一只猫在草地上奔跑",
                                model="sora-2-text-to-video",
                                wait_for_result=False
                            )

        # Assert
        assert result["task_id"] == "video_123"
        assert result["status"] == "processing"

    @pytest.mark.asyncio
    async def test_generate_text_to_video_insufficient_credits(self, video_service, mock_db):
        """测试：积分不足"""
        # Arrange
        user = create_test_user(credits=10)  # 积分不足
        mock_db.set_table_data("users", [user])

        with patch.object(video_service, "_get_user", return_value=user):
            with patch.object(video_service, "_estimate_video_credits", return_value=100):
                # Act & Assert
                with pytest.raises(InsufficientCreditsError):
                    await video_service.generate_text_to_video(
                        user_id=user["id"],
                        prompt="测试",
                        model="sora-2-text-to-video"
                    )


class TestVideoServiceImageToVideo:
    """图生视频测试"""

    @pytest.fixture
    def video_service(self, mock_db):
        with patch("services.video_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return VideoService(mock_db)

    @pytest.mark.asyncio
    async def test_generate_image_to_video_success(self, video_service, mock_db):
        """测试：图生视频成功"""
        # Arrange
        user = create_test_user(credits=500)
        mock_db.set_table_data("users", [user])

        with patch.object(video_service, "_get_user", return_value=user):
            with patch.object(video_service, "_estimate_video_credits", return_value=50):
                with patch.object(video_service, "_deduct_credits", return_value=None):
                    with patch("services.video_service.KieClient") as mock_client:
                        mock_adapter = AsyncMock()
                        mock_adapter.generate.return_value = {
                            "task_id": "i2v_123",
                            "status": "processing"
                        }
                        mock_client.return_value.__aenter__.return_value = MagicMock()

                        with patch("services.video_service.KieVideoAdapter", return_value=mock_adapter):
                            # Act
                            result = await video_service.generate_image_to_video(
                                user_id=user["id"],
                                prompt="让图片中的猫动起来",
                                image_url="https://example.com/cat.jpg",
                                model="sora-2-image-to-video",
                                wait_for_result=False
                            )

        # Assert
        assert result["task_id"] == "i2v_123"

    @pytest.mark.asyncio
    async def test_generate_image_to_video_insufficient_credits(self, video_service, mock_db):
        """测试：图生视频积分不足"""
        # Arrange
        user = create_test_user(credits=10)
        mock_db.set_table_data("users", [user])

        with patch.object(video_service, "_get_user", return_value=user):
            with patch.object(video_service, "_estimate_video_credits", return_value=100):
                # Act & Assert
                with pytest.raises(InsufficientCreditsError):
                    await video_service.generate_image_to_video(
                        user_id=user["id"],
                        prompt="测试",
                        image_url="https://example.com/image.jpg",
                        model="sora-2-image-to-video"
                    )


class TestVideoServiceQueryTask:
    """任务查询测试"""

    @pytest.fixture
    def video_service(self, mock_db):
        with patch("services.video_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return VideoService(mock_db)

    @pytest.mark.asyncio
    async def test_query_task_success(self, video_service):
        """测试：查询任务成功"""
        # Arrange
        with patch("services.video_service.KieClient") as mock_client:
            mock_adapter = AsyncMock()
            mock_adapter.query_task.return_value = {
                "task_id": "video_123",
                "status": "success",
                "video_url": "https://example.com/result.mp4"
            }
            mock_client.return_value.__aenter__.return_value = MagicMock()

            with patch("services.video_service.KieVideoAdapter", return_value=mock_adapter):
                # Act
                result = await video_service.query_task("video_123")

        # Assert
        assert result["status"] == "success"
        assert result["video_url"] is not None

    @pytest.mark.asyncio
    async def test_query_task_processing(self, video_service):
        """测试：任务处理中"""
        # Arrange
        with patch("services.video_service.KieClient") as mock_client:
            mock_adapter = AsyncMock()
            mock_adapter.query_task.return_value = {
                "task_id": "video_123",
                "status": "processing",
                "progress": 30
            }
            mock_client.return_value.__aenter__.return_value = MagicMock()

            with patch("services.video_service.KieVideoAdapter", return_value=mock_adapter):
                # Act
                result = await video_service.query_task("video_123")

        # Assert
        assert result["status"] == "processing"
        assert result["progress"] == 30

    @pytest.mark.asyncio
    async def test_query_task_failed(self, video_service):
        """测试：任务失败"""
        # Arrange
        with patch("services.video_service.KieClient") as mock_client:
            mock_adapter = AsyncMock()
            mock_adapter.query_task.return_value = {
                "task_id": "video_123",
                "status": "failed",
                "error": "生成失败"
            }
            mock_client.return_value.__aenter__.return_value = MagicMock()

            with patch("services.video_service.KieVideoAdapter", return_value=mock_adapter):
                # Act
                result = await video_service.query_task("video_123")

        # Assert
        assert result["status"] == "failed"
        assert "error" in result


class TestVideoServiceCredits:
    """积分估算测试"""

    @pytest.fixture
    def video_service(self, mock_db):
        with patch("services.video_service.settings") as mock_settings:
            mock_settings.kie_api_key = "test_key"
            return VideoService(mock_db)

    def test_estimate_credits_10_seconds(self, video_service):
        """测试：10秒视频积分估算"""
        credits = video_service._estimate_video_credits("sora-2-text-to-video", "10")
        assert credits > 0

    def test_estimate_credits_15_seconds(self, video_service):
        """测试：15秒视频积分估算"""
        credits_10 = video_service._estimate_video_credits("sora-2-text-to-video", "10")
        credits_15 = video_service._estimate_video_credits("sora-2-text-to-video", "15")

        # 15秒应该比10秒贵
        assert credits_15 > credits_10

    def test_estimate_credits_pro_model(self, video_service):
        """测试：Pro 模型积分估算"""
        basic_credits = video_service._estimate_video_credits("sora-2-text-to-video", "10")
        pro_credits = video_service._estimate_video_credits("sora-2-pro", "10")

        # Pro 模型应该更贵
        assert pro_credits >= basic_credits
