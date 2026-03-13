"""
Agent Loop 对话历史 + 内容构建 单元测试

覆盖：_get_recent_history、图片消息 prompt 注入、_build_user_content、_build_system_prompt
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_loop import AgentLoop


# ============================================================
# Helpers
# ============================================================

def _make_loop() -> AgentLoop:
    """创建 AgentLoop 实例（mock db）"""
    return AgentLoop(db=MagicMock(), user_id="u1", conversation_id="c1")


class TestGetRecentHistory:

    def _make_settings(
        self, limit: int = 10, max_chars: int = 3000, max_images: int = 8,
    ):
        s = MagicMock()
        s.agent_loop_brain_context_limit = limit
        s.agent_loop_brain_context_max_chars = max_chars
        s.agent_loop_brain_max_images = max_images
        return s

    @pytest.mark.asyncio
    async def test_normal_messages(self):
        """正常消息→返回结构化多模态消息列表"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "你好"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "你好！"}],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 2
        # 验证角色和内容结构
        roles = [m["role"] for m in result]
        assert "user" in roles
        assert "assistant" in roles
        # 验证内容是 content blocks 格式
        user_msg = [m for m in result if m["role"] == "user"][0]
        assert user_msg["content"][0]["type"] == "text"
        assert "你好" in user_msg["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_messages_with_images(self):
        """含图片消息→包含 image_url content block"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "帮我处理"},
                        {"type": "image", "url": "https://img.com/a.jpg"},
                    ],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        assert len(result) == 1
        blocks = result[0]["content"]
        # 验证文本 block
        text_blocks = [b for b in blocks if b["type"] == "text"]
        assert any("帮我处理" in b["text"] for b in text_blocks)
        # 验证图片 block（DB image → OpenAI image_url）
        img_blocks = [b for b in blocks if b["type"] == "image_url"]
        assert len(img_blocks) == 1
        assert img_blocks[0]["image_url"]["url"] == "https://img.com/a.jpg"

    @pytest.mark.asyncio
    async def test_empty_messages_returns_none(self):
        """空消息列表→None"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {"messages": []}

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is None

    @pytest.mark.asyncio
    async def test_char_limit_truncation(self):
        """超过 max_chars→截断"""
        loop = _make_loop()
        loop._settings = self._make_settings(max_chars=30)

        long_text = "A" * 50
        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": long_text}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "reply"}],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        # max_chars=30，第一条消息(50字)超限 → 只拿到 reply
        # reversed() 遍历：从旧到新，reply(5字) 先进入，然后 50字超限 break
        if result is not None:
            assert len(result) <= 1  # 不会返回完整 2 条

    @pytest.mark.asyncio
    async def test_service_error_returns_none(self):
        """MessageService 抛异常→None（不影响主流程）"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        with patch(
            "services.message_service.MessageService",
            side_effect=Exception("db error"),
        ):
            result = await loop._get_recent_history()

        assert result is None

    @pytest.mark.asyncio
    async def test_image_without_url_skipped(self):
        """image 类型但无 url→不生成 image_url block"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "test"},
                        {"type": "image", "url": ""},
                    ],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        blocks = result[0]["content"]
        img_blocks = [b for b in blocks if b["type"] == "image_url"]
        assert len(img_blocks) == 0


class TestHistoryImagePromptInjection:

    def _make_settings(self):
        s = MagicMock()
        s.agent_loop_brain_context_limit = 10
        s.agent_loop_brain_context_max_chars = 8000
        s.agent_loop_brain_max_images = 8
        return s

    @pytest.mark.asyncio
    async def test_assistant_image_injects_prompt(self):
        """assistant 图片消息→注入原始生成提示词文本"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "好的，来看看生成的图片"},
                        {"type": "image", "url": "https://img.com/robot.jpg"},
                    ],
                    "generation_params": {"prompt": "AI robot avatar, metallic"},
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        blocks = result[0]["content"]
        # 应该有 2 个文本 block：原始文本 + 注入的提示词
        assert len(blocks) == 2
        prompt_block = blocks[1]
        assert "[图片已生成，使用的提示词: AI robot avatar, metallic]" in prompt_block["text"]
        # 不应有 image_url block（assistant 不支持）
        img_blocks = [b for b in blocks if b.get("type") == "image_url"]
        assert len(img_blocks) == 0

    @pytest.mark.asyncio
    async def test_assistant_image_no_generation_params(self):
        """assistant 图片消息无 generation_params→不注入，只保留原始文本"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "图片来了"},
                        {"type": "image", "url": "https://img.com/a.jpg"},
                    ],
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        blocks = result[0]["content"]
        # 只有 1 个文本 block（原始文本），没有 prompt 注入
        assert len(blocks) == 1
        assert "图片来了" in blocks[0]["text"]

    @pytest.mark.asyncio
    async def test_assistant_text_only_no_injection(self):
        """assistant 纯文本消息→不注入任何额外内容"""
        loop = _make_loop()
        loop._settings = self._make_settings()

        mock_service = AsyncMock()
        mock_service.get_messages.return_value = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "你好！"}],
                    "generation_params": {"model": "gemini-3-pro"},
                },
            ],
        }

        with patch(
            "services.message_service.MessageService", return_value=mock_service,
        ):
            result = await loop._get_recent_history()

        assert result is not None
        blocks = result[0]["content"]
        assert len(blocks) == 1
        assert "你好！" in blocks[0]["text"]
        # 无图片→不会注入 prompt
        assert not any("图片已生成" in b.get("text", "") for b in blocks)


class TestBuildUserContent:

    def test_text_only(self):
        """纯文本→只有 text block"""
        loop = _make_loop()
        content = [TextPart(text="你好")]
        result = loop._build_user_content(content)
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "你好"

    def test_text_with_images(self):
        """文本+图片→text block + image_url blocks"""
        loop = _make_loop()
        content = [
            TextPart(text="分析图片"),
            ImagePart(url="https://img.com/a.jpg"),
            ImagePart(url="https://img.com/b.jpg"),
        ]
        result = loop._build_user_content(content)
        assert len(result) == 3
        assert result[0]["type"] == "text"
        text_blocks = [b for b in result if b["type"] == "text"]
        img_blocks = [b for b in result if b["type"] == "image_url"]
        assert len(text_blocks) == 1
        assert len(img_blocks) == 2
        assert img_blocks[0]["image_url"]["url"] == "https://img.com/a.jpg"

    def test_images_only(self):
        """只有图片（无文本）→只有 image_url blocks"""
        loop = _make_loop()
        content = [ImagePart(url="https://img.com/a.jpg")]
        result = loop._build_user_content(content)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    def test_image_without_url_skipped(self):
        """ImagePart.url=None→不生成 image_url block"""
        loop = _make_loop()
        content = [TextPart(text="test"), ImagePart(url=None)]
        result = loop._build_user_content(content)
        img_blocks = [b for b in result if b["type"] == "image_url"]
        assert len(img_blocks) == 0

    def test_file_adds_pdf_hint(self):
        """FilePart→文本前缀添加 PDF 提示"""
        from schemas.message import FilePart
        loop = _make_loop()
        content = [
            TextPart(text="解读文档"),
            FilePart(url="https://f.com/a.pdf", name="a.pdf", mime_type="application/pdf"),
        ]
        result = loop._build_user_content(content)
        text_block = result[0]
        assert "PDF文档" in text_block["text"]
        assert "解读文档" in text_block["text"]

    def test_multiple_files_count(self):
        """多个 FilePart→正确计数"""
        from schemas.message import FilePart
        loop = _make_loop()
        content = [
            TextPart(text="对比"),
            FilePart(url="u1", name="a.pdf", mime_type="application/pdf"),
            FilePart(url="u2", name="b.pdf", mime_type="application/pdf"),
        ]
        result = loop._build_user_content(content)
        assert "2份PDF" in result[0]["text"]

    def test_empty_content_returns_empty_text_block(self):
        """空内容→返回空 text block"""
        loop = _make_loop()
        result = loop._build_user_content([])
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert result[0]["text"] == ""

    def test_text_with_file_and_image(self):
        """文本+文件+图片→PDF提示 + text + image_url"""
        from schemas.message import FilePart
        loop = _make_loop()
        content = [
            TextPart(text="分析"),
            FilePart(url="u1", name="a.pdf", mime_type="application/pdf"),
            ImagePart(url="https://img.com/x.jpg"),
        ]
        result = loop._build_user_content(content)
        text_blocks = [b for b in result if b["type"] == "text"]
        img_blocks = [b for b in result if b["type"] == "image_url"]
        assert len(text_blocks) == 1
        assert "PDF" in text_blocks[0]["text"]
        assert len(img_blocks) == 1


class TestBuildSystemPrompt:

    @pytest.mark.asyncio
    async def test_empty_text_returns_base_prompt(self):
        """空文本→返回基础提示词，不查知识库"""
        loop = _make_loop()
        loop._settings = MagicMock()
        result = await loop._build_system_prompt([])
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        assert result == AGENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    @patch("services.knowledge_service.search_relevant", new_callable=AsyncMock)
    async def test_knowledge_injected(self, mock_search):
        """有知识→注入经验知识"""
        mock_search.return_value = [
            {"title": "经验1", "content": "内容1"},
            {"title": "经验2", "content": "内容2"},
        ]
        loop = _make_loop()
        loop._settings = MagicMock()
        result = await loop._build_system_prompt([TextPart(text="画猫")])
        assert "经验知识" in result
        assert "经验1" in result
        assert "内容2" in result

    @pytest.mark.asyncio
    @patch("services.knowledge_service.search_relevant", new_callable=AsyncMock)
    async def test_no_knowledge_returns_base(self, mock_search):
        """知识库无结果→返回基础提示词"""
        mock_search.return_value = []
        loop = _make_loop()
        loop._settings = MagicMock()
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        result = await loop._build_system_prompt([TextPart(text="你好")])
        assert result == AGENT_SYSTEM_PROMPT

    @pytest.mark.asyncio
    @patch("services.knowledge_service.search_relevant", new_callable=AsyncMock)
    async def test_knowledge_error_returns_base(self, mock_search):
        """知识服务异常→返回基础提示词（不影响主流程）"""
        mock_search.side_effect = Exception("db timeout")
        loop = _make_loop()
        loop._settings = MagicMock()
        from config.agent_tools import AGENT_SYSTEM_PROMPT
        result = await loop._build_system_prompt([TextPart(text="test")])
        assert result == AGENT_SYSTEM_PROMPT
