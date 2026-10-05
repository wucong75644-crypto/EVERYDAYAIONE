"""测试 MediaToolMixin — 纯单元测试（不依赖 ToolExecutor import 链）"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Mock pydantic_settings 以避免环境依赖
if "pydantic_settings" not in sys.modules:
    sys.modules["pydantic_settings"] = MagicMock()

import pytest

from services.agent.agent_result import AgentResult
from services.media_tool_executor import MediaToolMixin


class FakeMedia(MediaToolMixin):
    """模拟宿主类属性"""

    def __init__(self):
        self.db = MagicMock()
        self.user_id = "u1"
        self.org_id = "org1"

    def _lock_credits(self, **kwargs):
        return "tx_123"

    def _confirm_deduct(self, tx_id):
        pass

    def _refund_credits(self, tx_id):
        pass


@pytest.fixture
def media():
    return FakeMedia()


# ── _generate_image ──


class TestGenerateImageAcceptance:
    @pytest.mark.asyncio
    async def test_receipt_is_pending_and_has_no_local_credit_or_provider_io(self, media):
        media._lock_credits = MagicMock()
        receipt={"task_id":"child","status":"submitted","submission_state":"queued"}
        args={"mode":"text_to_image","prompt":"  exact  "}
        with patch("services.handlers.image_handler.ImageHandler.accept_chat_image",new=AsyncMock(return_value=receipt)) as accept, \
             patch("services.adapters.factory.create_image_adapter") as provider:
            result=await media._generate_image(args)
        accept.assert_awaited_once_with(media,args)
        assert result.metadata["accepted"] is True and result.metadata["completed"] is False
        media._lock_credits.assert_not_called(); provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_input_or_closed_gate_does_not_fallback(self, media):
        with patch("services.handlers.image_handler.ImageHandler.accept_chat_image",new=AsyncMock(side_effect=PermissionError("disabled"))), \
             patch("services.adapters.factory.create_image_adapter") as provider:
            with pytest.raises(PermissionError): await media._generate_image({"prompt":"cat"})
        provider.assert_not_called()


# ── _generate_video ──


class TestGenerateVideoValidation:

    @pytest.mark.asyncio
    async def test_empty_prompt(self, media):
        result = await media._generate_video({"prompt": ""})
        assert isinstance(result, AgentResult)
        assert result.is_failure

    @pytest.mark.asyncio
    async def test_success(self, media):
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.video_url = "https://cdn/v.mp4"
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with patch("config.kie_models.calculate_video_cost",
                   return_value={"user_credits": 20}), \
             patch("services.adapters.factory.create_video_adapter",
                   return_value=mock_adapter):
            result = await media._generate_video({"prompt": "dog running"})

        assert result.status == "success"
        assert "https://cdn/v.mp4" in result.summary

    @pytest.mark.asyncio
    async def test_failure_refunds(self, media):
        mock_adapter = MagicMock()
        mock_result = MagicMock()
        mock_result.video_url = None
        mock_result.fail_msg = "timeout"
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()
        media._refund_credits = MagicMock()

        with patch("config.kie_models.calculate_video_cost",
                   return_value={"user_credits": 20}), \
             patch("services.adapters.factory.create_video_adapter",
                   return_value=mock_adapter):
            result = await media._generate_video({"prompt": "dog"})

        assert result.is_failure
        media._refund_credits.assert_called_once()
