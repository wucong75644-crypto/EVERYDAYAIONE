"""
EcomImageHandler v2 单元测试

覆盖：批量 prompt 构建逻辑、白底图参考图精简、强制 i2i 模型、兜底处理。
不调用实际的 KIE API——mock ImageHandler.start 验证传入的 params。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.handlers.ecom_image_handler import EcomImageHandler, _I2I_MODEL


class TestEcomImageHandlerStart:
    """测试 EcomImageHandler.start 的批量构建逻辑。"""

    def _make_handler(self):
        handler = EcomImageHandler(db=MagicMock())
        return handler

    def _make_content_with_images(self, urls: list[str]):
        """构建包含图片的 content 列表。"""
        return [{"type": "image", "url": url} for url in urls]

    @pytest.mark.asyncio
    async def test_batch_prompts_built_correctly(self):
        """image_task_meta 正确转换为 _batch_prompts。"""
        handler = self._make_handler()
        meta = [
            {"prompt": "Preserve... hero image", "aspect_ratio": "1:1", "has_text": True, "image_type": "marketing"},
            {"prompt": "Preserve... white bg", "aspect_ratio": "1:1", "has_text": False, "image_type": "white_bg"},
        ]
        content = self._make_content_with_images(["https://cdn/p1.jpg"])
        params: dict = {"image_task_meta": meta}

        with patch.object(EcomImageHandler.__bases__[0], "start", new_callable=AsyncMock, return_value="task_123") as mock_start:
            result = await handler.start("msg1", "conv1", "user1", content, params, MagicMock())

        assert result == "task_123"
        mock_start.assert_called_once()
        call_params = mock_start.call_args[0][4]  # params argument

        # 验证模型强制 i2i
        assert call_params["model"] == _I2I_MODEL

        # 验证 _batch_prompts
        bp = call_params["_batch_prompts"]
        assert len(bp) == 2
        assert bp[0]["prompt"] == "Preserve... hero image"
        assert bp[1]["prompt"] == "Preserve... white bg"

    @pytest.mark.asyncio
    async def test_white_bg_only_primary_ref(self):
        """白底图只传产品主图，不传风格参考图。"""
        handler = self._make_handler()
        meta = [
            {"prompt": "hero", "aspect_ratio": "1:1", "has_text": True, "image_type": "marketing"},
            {"prompt": "white", "aspect_ratio": "1:1", "has_text": False, "image_type": "white_bg"},
        ]
        content = self._make_content_with_images(["https://cdn/p1.jpg", "https://cdn/p2.jpg"])
        params: dict = {
            "image_task_meta": meta,
            "product_image_urls": ["https://cdn/p1.jpg", "https://cdn/p2.jpg"],
            "style_ref_urls": ["https://cdn/s1.jpg"],
        }

        with patch.object(EcomImageHandler.__bases__[0], "start", new_callable=AsyncMock, return_value="ok") as mock_start:
            await handler.start("msg1", "conv1", "user1", content, params, MagicMock())

        bp = mock_start.call_args[0][4]["_batch_prompts"]

        # 营销图：产品图 + 风格参考图
        assert len(bp[0]["image_urls"]) == 3  # p1 + p2 + s1
        # 白底图：仅产品主图
        assert len(bp[1]["image_urls"]) == 1
        assert bp[1]["image_urls"][0] == "https://cdn/p1.jpg"

    @pytest.mark.asyncio
    async def test_no_meta_triggers_phase1(self):
        """无 image_task_meta → Phase 1（异步方案策划），立刻返回 task_id。"""
        handler = self._make_handler()
        params: dict = {}  # 无 meta
        metadata = MagicMock()
        metadata.client_task_id = "task_plan_123"

        with patch.object(handler, "_phase1_plan", new_callable=AsyncMock) as mock_plan:
            result = await handler.start("msg1", "conv1", "user1", [], params, metadata)

        # Phase 1 立刻返回 task_id，不等方案生成完成
        assert result == "task_plan_123"
        # _phase1_plan 在后台被调用（通过 asyncio.create_task）
        await asyncio.sleep(0.01)  # 让 create_task 有机会执行

    @pytest.mark.asyncio
    async def test_empty_prompts_skipped(self):
        """空 prompt 的 item 被跳过。"""
        handler = self._make_handler()
        meta = [
            {"prompt": "valid prompt", "aspect_ratio": "1:1", "image_type": "marketing"},
            {"prompt": "", "aspect_ratio": "1:1", "image_type": "scene"},
            {"description": "fallback desc", "aspect_ratio": "1:1", "image_type": "detail"},
        ]
        content = self._make_content_with_images(["https://cdn/p1.jpg"])
        params: dict = {"image_task_meta": meta}

        with patch.object(EcomImageHandler.__bases__[0], "start", new_callable=AsyncMock, return_value="ok") as mock_start:
            await handler.start("msg1", "conv1", "user1", content, params, MagicMock())

        bp = mock_start.call_args[0][4]["_batch_prompts"]
        # 空 prompt 跳过，description 兜底有效
        assert len(bp) == 2
        assert bp[0]["prompt"] == "valid prompt"
        assert bp[1]["prompt"] == "fallback desc"
