"""CommandHandler 单元测试 — 指令匹配/不匹配/边界"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.wecom.command_handler import CommandHandler


def _make_reply_ctx():
    ctx = MagicMock()
    ctx.channel = "smart_robot"
    ctx.req_id = "test_req_id"
    ctx.ws_client = MagicMock()
    ctx.ws_client.send_template_card = AsyncMock()
    ctx.ws_client.send_reply = AsyncMock()
    return ctx


def _make_db():
    return MagicMock()


class TestCommandMatching:
    """指令识别"""

    @pytest.mark.asyncio
    async def test_help_command(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("帮助", "u1", "c1", ctx)
        assert result is True
        ctx.ws_client.send_template_card.assert_called_once()

    @pytest.mark.asyncio
    async def test_help_english(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("help", "u1", "c1", ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_credits_command(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        with patch("services.credit_service.CreditService") as MockCS:
            MockCS.return_value.get_balance = AsyncMock(return_value=500)
            result = await handler.try_handle("查积分", "u1", "c1", ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_memory_command(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        with patch("services.memory_service.MemoryService") as MockMS:
            MockMS.return_value.get_all_memories = AsyncMock(return_value=[])
            result = await handler.try_handle("我的记忆", "u1", "c1", ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_new_conversation(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        with patch("services.conversation_service.ConversationService") as MockCS:
            MockCS.return_value.create_conversation = AsyncMock(
                return_value={"id": "new_conv"}
            )
            result = await handler.try_handle("新对话", "u1", "c1", ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_thinking_mode(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("深度思考", "u1", "c1", ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_switch_model(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("切换模型", "u1", "c1", ctx)
        assert result is True


class TestCommandNotMatching:
    """不应拦截的文本"""

    @pytest.mark.asyncio
    async def test_normal_chat(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("今天天气怎么样", "u1", "c1", ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_long_text_with_keyword(self):
        """长文本即使包含关键词也不拦截（>10字符保护）"""
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("帮助我写一篇关于AI的文章", "u1", "c1", ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_text(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("", "u1", "c1", ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_whitespace_only(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("   ", "u1", "c1", ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_partial_match_no_intercept(self):
        """"积分怎么用" 不应被拦截"""
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("积分怎么用", "u1", "c1", ctx)
        assert result is False


class TestDirectModelSwitch:
    """直接模型切换（"用xxx"）"""

    @pytest.mark.asyncio
    async def test_switch_known_model(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("用deepseek", "u1", "c1", ctx)
        assert result is True
        ctx.ws_client.send_template_card.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_unknown_model(self):
        handler = CommandHandler(_make_db())
        ctx = _make_reply_ctx()
        result = await handler.try_handle("用abc模型", "u1", "c1", ctx)
        assert result is True
        # 未知模型应回复文本提示
        ctx.ws_client.send_reply.assert_called_once()


class TestFuzzyModelMatch:
    """模型名模糊匹配"""

    def test_exact_id_match(self):
        result = CommandHandler._fuzzy_match_model("deepseek-v3.2")
        assert result == "deepseek-v3.2"

    def test_partial_match(self):
        result = CommandHandler._fuzzy_match_model("deepseek")
        assert result is not None
        assert "deepseek" in result

    def test_case_insensitive(self):
        result = CommandHandler._fuzzy_match_model("DeepSeek")
        assert result is not None

    def test_no_match(self):
        result = CommandHandler._fuzzy_match_model("不存在的模型xyz")
        assert result is None

    def test_chinese_name_match(self):
        result = CommandHandler._fuzzy_match_model("千问")
        assert result == "qwen3.5-plus"
