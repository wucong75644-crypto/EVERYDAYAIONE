"""media_tool_executor 单元测试 — 图片/视频生成 + 积分 lock/confirm/refund"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Mock adapter 返回值
@dataclass
class MockImageResult:
    task_id: str = "img_001"
    image_urls: List[str] = None
    fail_msg: Optional[str] = None

    def __post_init__(self):
        if self.image_urls is None:
            self.image_urls = []


@dataclass
class MockVideoResult:
    task_id: str = "vid_001"
    video_url: Optional[str] = None
    fail_msg: Optional[str] = None


def _make_executor():
    """创建带 mock db 的 ToolExecutor"""
    from services.tool_executor import ToolExecutor

    mock_db = MagicMock()
    # mock _lock_credits（CreditMixin 方法）
    mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
        data={"credits": 1000}
    )
    mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"id": "u1"}]
    )
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
    mock_db.rpc.return_value.execute.return_value = MagicMock(data={"refunded": True})

    exe = ToolExecutor(db=mock_db, user_id="u1", conversation_id="c1", org_id="org1")
    return exe


class TestGenerateImage:
    """_generate_image 测试"""

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_error(self):
        exe = _make_executor()
        result = await exe._generate_image({"prompt": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_success_returns_url_and_confirms(self):
        exe = _make_executor()
        mock_result = MockImageResult(image_urls=["https://cdn.example.com/cat.png"])
        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_image_adapter", return_value=mock_adapter), \
             patch("config.kie_models.calculate_image_cost", return_value={"user_credits": 18}):
            result = await exe._generate_image({"prompt": "a cute cat"})

        assert "https://cdn.example.com/cat.png" in result
        assert "图片已生成" in result

    @pytest.mark.asyncio
    async def test_adapter_failure_refunds_credits(self):
        exe = _make_executor()
        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(side_effect=Exception("API timeout"))
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_image_adapter", return_value=mock_adapter), \
             patch("config.kie_models.calculate_image_cost", return_value={"user_credits": 18}):
            result = await exe._generate_image({"prompt": "test"})

        assert "失败" in result
        # 验证 refund 被调用（通过 db.rpc）
        exe.db.rpc.assert_called()

    @pytest.mark.asyncio
    async def test_no_urls_in_result_refunds(self):
        exe = _make_executor()
        mock_result = MockImageResult(image_urls=[], fail_msg="内容审核不通过")
        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_image_adapter", return_value=mock_adapter), \
             patch("config.kie_models.calculate_image_cost", return_value={"user_credits": 18}):
            result = await exe._generate_image({"prompt": "test"})

        assert "内容审核不通过" in result

    @pytest.mark.asyncio
    async def test_insufficient_credits(self):
        from core.exceptions import InsufficientCreditsError

        exe = _make_executor()
        # 余额不足
        exe.db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={"credits": 5}
        )

        with patch("config.kie_models.calculate_image_cost", return_value={"user_credits": 18}):
            result = await exe._generate_image({"prompt": "test"})

        assert "积分不足" in result


class TestGenerateVideo:
    """_generate_video 测试"""

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_error(self):
        exe = _make_executor()
        result = await exe._generate_video({"prompt": ""})
        assert "不能为空" in result

    @pytest.mark.asyncio
    async def test_success_returns_url(self):
        exe = _make_executor()
        mock_result = MockVideoResult(video_url="https://cdn.example.com/demo.mp4")
        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_video_adapter", return_value=mock_adapter), \
             patch("config.kie_models.calculate_video_cost", return_value={"user_credits": 30}):
            result = await exe._generate_video({"prompt": "a sunset scene"})

        assert "https://cdn.example.com/demo.mp4" in result
        assert "视频已生成" in result

    @pytest.mark.asyncio
    async def test_adapter_failure_refunds(self):
        exe = _make_executor()
        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(side_effect=Exception("GPU error"))
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_video_adapter", return_value=mock_adapter), \
             patch("config.kie_models.calculate_video_cost", return_value={"user_credits": 30}):
            result = await exe._generate_video({"prompt": "test"})

        assert "失败" in result
        exe.db.rpc.assert_called()


class TestToolExecutorInheritance:
    """ToolExecutor 继承链验证"""

    def test_inherits_credit_mixin(self):
        from services.handlers.mixins.credit_mixin import CreditMixin
        from services.tool_executor import ToolExecutor
        assert issubclass(ToolExecutor, CreditMixin)

    def test_has_lock_credits_method(self):
        exe = _make_executor()
        assert hasattr(exe, "_lock_credits")
        assert hasattr(exe, "_confirm_deduct")
        assert hasattr(exe, "_refund_credits")

    def test_has_media_methods(self):
        exe = _make_executor()
        assert hasattr(exe, "_generate_image")
        assert hasattr(exe, "_generate_video")

    def test_has_erp_methods(self):
        exe = _make_executor()
        assert hasattr(exe, "_erp_dispatch")
        assert hasattr(exe, "_local_dispatch")
        assert hasattr(exe, "_get_erp_dispatcher")
