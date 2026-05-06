"""ChatGenerateMixin.generate_complete 单元测试"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from schemas.message import ContentPart, ImagePart, TextPart, VideoPart


def _make_handler():
    """创建带 mock db 的 ChatHandler 实例"""
    from services.handlers.chat_handler import ChatHandler
    h = ChatHandler(db=MagicMock())
    h.org_id = "test_org"
    return h


@dataclass
class MockChunk:
    content: Optional[str] = None
    thinking_content: Optional[str] = None
    tool_calls: Optional[list] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    credits_consumed: Optional[int] = None
    finish_reason: Optional[str] = None


class TestGenerateCompleteBasic:
    """generate_complete 基本行为"""

    @pytest.mark.asyncio
    async def test_returns_text_part_on_simple_chat(self):
        """无工具调用 → 返回 TextPart"""
        handler = _make_handler()

        async def mock_stream(*args, **kwargs):
            yield MockChunk(content="你好，有什么可以帮你？")

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(handler, "_build_llm_messages", new_callable=AsyncMock, return_value=[]), \
             patch.object(handler, "_build_memory_prompt", new_callable=AsyncMock, return_value=None), \
             patch.object(handler, "_extract_text_content", return_value="你好"):
            gen_result = await handler.generate_complete(
                content=[TextPart(text="你好")],
                user_id="u1",
                conversation_id="c1",
            )

        result = gen_result.parts
        assert len(result) >= 1
        assert any(isinstance(p, TextPart) for p in result)
        text = next(p for p in result if isinstance(p, TextPart))
        assert "你好" in text.text

    @pytest.mark.asyncio
    async def test_returns_error_text_on_exception(self):
        """adapter 异常 → 返回错误提示 TextPart"""
        handler = _make_handler()

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = MagicMock(side_effect=RuntimeError("API down"))
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(handler, "_build_llm_messages", new_callable=AsyncMock, return_value=[]), \
             patch.object(handler, "_build_memory_prompt", new_callable=AsyncMock, return_value=None), \
             patch.object(handler, "_extract_text_content", return_value="test"):
            gen_result = await handler.generate_complete(
                content=[TextPart(text="test")],
                user_id="u1",
                conversation_id="c1",
            )

        result = gen_result.parts
        assert len(result) == 1
        assert isinstance(result[0], TextPart)
        assert "问题" in result[0].text

    @pytest.mark.asyncio
    async def test_extracts_image_url_as_imagepart(self):
        """回复中包含图片 URL → 提取为 ImagePart"""
        handler = _make_handler()

        async def mock_stream(*args, **kwargs):
            yield MockChunk(content="图片已生成：\nhttps://cdn.example.com/cat.png")

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = mock_stream
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(handler, "_build_llm_messages", new_callable=AsyncMock, return_value=[]), \
             patch.object(handler, "_build_memory_prompt", new_callable=AsyncMock, return_value=None), \
             patch.object(handler, "_extract_text_content", return_value="画猫"):
            gen_result = await handler.generate_complete(
                content=[TextPart(text="画猫")],
                user_id="u1",
                conversation_id="c1",
            )

        result = gen_result.parts
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(images) == 1
        assert "cat.png" in images[0].url

    @pytest.mark.asyncio
    async def test_adapter_always_closed(self):
        """无论成功失败，adapter 都会关闭"""
        handler = _make_handler()

        mock_adapter = MagicMock()
        mock_adapter.stream_chat = MagicMock(side_effect=RuntimeError("boom"))
        mock_adapter.close = AsyncMock()

        with patch("services.adapters.factory.create_chat_adapter", return_value=mock_adapter), \
             patch.object(handler, "_build_llm_messages", new_callable=AsyncMock, return_value=[]), \
             patch.object(handler, "_build_memory_prompt", new_callable=AsyncMock, return_value=None), \
             patch.object(handler, "_extract_text_content", return_value="test"):
            await handler.generate_complete(
                content=[TextPart(text="test")], user_id="u1", conversation_id="c1",
            )

        mock_adapter.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_default_model_when_none(self):
        """model_id=None → 使用 DEFAULT_MODEL_ID"""
        handler = _make_handler()
        captured_model = None

        def capture_adapter(model_id, **kwargs):
            nonlocal captured_model
            captured_model = model_id
            adapter = MagicMock()

            async def mock_stream(*a, **kw):
                yield MockChunk(content="ok")
            adapter.stream_chat = mock_stream
            adapter.close = AsyncMock()
            return adapter

        with patch("services.adapters.factory.create_chat_adapter", side_effect=capture_adapter), \
             patch.object(handler, "_build_llm_messages", new_callable=AsyncMock, return_value=[]), \
             patch.object(handler, "_build_memory_prompt", new_callable=AsyncMock, return_value=None), \
             patch.object(handler, "_extract_text_content", return_value="test"):
            await handler.generate_complete(
                content=[TextPart(text="test")], user_id="u1", conversation_id="c1",
            )

        from services.adapters.factory import DEFAULT_MODEL_ID
        assert captured_model == DEFAULT_MODEL_ID
