"""WecomCardEventHandler 单元测试 — 每个 event_key 的处理逻辑"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.wecom.card_event_handler import WecomCardEventHandler


def _make_reply_ctx():
    ctx = MagicMock()
    ctx.channel = "smart_robot"
    ctx.req_id = "test_req_id"
    ctx.ws_client = MagicMock()
    ctx.ws_client.send_template_card = AsyncMock()
    ctx.ws_client.send_update_card = AsyncMock()
    ctx.ws_client.send_reply = AsyncMock()
    return ctx


def _make_db():
    return MagicMock()


class TestStartChat:
    @pytest.mark.asyncio
    async def test_replies_text(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("start_chat", "t1", "button_interaction", None,
                             "u1", "c1", ctx)
        ctx.ws_client.send_reply.assert_called_once()


class TestShowHelp:
    @pytest.mark.asyncio
    async def test_sends_help_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("show_help", "t1", "button_interaction", None,
                             "u1", "c1", ctx)
        ctx.ws_client.send_template_card.assert_called_once()
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert card["card_type"] == "button_interaction"


class TestCheckCredits:
    @pytest.mark.asyncio
    async def test_sends_credits_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        with patch("services.credit_service.CreditService") as MockCS:
            MockCS.return_value.get_balance = AsyncMock(return_value=999)
            await handler.handle("check_credits", "t1", "button_interaction",
                                 None, "u1", "c1", ctx)
        ctx.ws_client.send_template_card.assert_called_once()
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert card["emphasis_content"]["title"] == "999"


class TestManageMemory:
    @pytest.mark.asyncio
    async def test_with_memories(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        memories = [{"memory": "test memory"}]
        with patch("services.memory_service.MemoryService") as MockMS:
            MockMS.return_value.get_all_memories = AsyncMock(return_value=memories)
            await handler.handle("manage_memory", "t1", "button_interaction",
                                 None, "u1", "c1", ctx)
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert card["card_type"] == "button_interaction"
        assert "共 1 条" in card["main_title"]["title"]

    @pytest.mark.asyncio
    async def test_empty_memories(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        with patch("services.memory_service.MemoryService") as MockMS:
            MockMS.return_value.get_all_memories = AsyncMock(return_value=[])
            await handler.handle("manage_memory", "t1", "button_interaction",
                                 None, "u1", "c1", ctx)
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert card["card_type"] == "text_notice"
        assert "暂无" in card["main_title"]["title"]


class TestClearAllMemory:
    @pytest.mark.asyncio
    async def test_clears_and_updates_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        with patch("services.memory_service.MemoryService") as MockMS:
            MockMS.return_value.delete_all_memories = AsyncMock()
            await handler.handle("clear_all_memory", "t1", "button_interaction",
                                 None, "u1", "c1", ctx)
            MockMS.return_value.delete_all_memories.assert_called_once_with("u1")
        ctx.ws_client.send_update_card.assert_called_once()


class TestSwitchModel:
    @pytest.mark.asyncio
    async def test_sends_model_select_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("switch_model", "t1", "button_interaction",
                             None, "u1", "c1", ctx)
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert card["card_type"] == "multiple_interaction"


class TestSubmitModel:
    @pytest.mark.asyncio
    async def test_valid_selection(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        selected = {
            "selected_item": [{
                "question_key": "model_select",
                "option_ids": {"option_id": ["deepseek-v3.2"]},
            }]
        }
        await handler.handle("submit_model", "t1", "multiple_interaction",
                             selected, "u1", "c1", ctx)
        ctx.ws_client.send_update_card.assert_called_once()
        card = ctx.ws_client.send_update_card.call_args[0][1]
        assert "已切换" in card["main_title"]["title"]

    @pytest.mark.asyncio
    async def test_no_selection(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("submit_model", "t1", "multiple_interaction",
                             None, "u1", "c1", ctx)
        ctx.ws_client.send_reply.assert_called_once()


class TestNewConversation:
    @pytest.mark.asyncio
    async def test_creates_and_confirms(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        with patch("services.conversation_service.ConversationService") as MockCS:
            MockCS.return_value.create_conversation = AsyncMock(
                return_value={"id": "new"}
            )
            await handler.handle("new_conversation", "t1", "button_interaction",
                                 None, "u1", "c1", ctx)
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert "新对话" in card["main_title"]["title"]


class TestThinkingMode:
    @pytest.mark.asyncio
    async def test_toggle_sends_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("toggle_thinking", "t1", "button_interaction",
                             None, "u1", "c1", ctx)
        card = ctx.ws_client.send_template_card.call_args[0][1]
        assert card["card_type"] == "button_interaction"

    @pytest.mark.asyncio
    async def test_deep_updates_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("thinking_deep", "t1", "button_interaction",
                             None, "u1", "c1", ctx)
        ctx.ws_client.send_update_card.assert_called_once()
        card = ctx.ws_client.send_update_card.call_args[0][1]
        assert "深度思考" in card["main_title"]["title"]

    @pytest.mark.asyncio
    async def test_fast_updates_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("thinking_fast", "t1", "button_interaction",
                             None, "u1", "c1", ctx)
        card = ctx.ws_client.send_update_card.call_args[0][1]
        assert "快速回复" in card["main_title"]["title"]


class TestGenConfirm:
    @pytest.mark.asyncio
    async def test_updates_card(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("gen_confirm", "t1", "button_interaction",
                             None, "u1", "c1", ctx)
        ctx.ws_client.send_update_card.assert_called_once()


class TestUnknownEvent:
    @pytest.mark.asyncio
    async def test_noop_key(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        # noop 不应调用任何方法
        await handler.handle("noop", "t1", "button_interaction",
                             None, "u1", "c1", ctx)
        ctx.ws_client.send_template_card.assert_not_called()
        ctx.ws_client.send_update_card.assert_not_called()
        ctx.ws_client.send_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_key_logs_warning(self):
        handler = WecomCardEventHandler(_make_db())
        ctx = _make_reply_ctx()
        await handler.handle("xyz_unknown", "t1", "button_interaction",
                             None, "u1", "c1", ctx)
        # 未知 key 不应崩溃，也不应发送任何消息
        ctx.ws_client.send_template_card.assert_not_called()


class TestExtractSelectedId:
    def test_valid_selection(self):
        selected = {
            "selected_item": [{
                "question_key": "model_select",
                "option_ids": {"option_id": ["deepseek-v3.2"]},
            }]
        }
        result = WecomCardEventHandler._extract_selected_id(selected, "model_select")
        assert result == "deepseek-v3.2"

    def test_wrong_question_key(self):
        selected = {
            "selected_item": [{
                "question_key": "other_key",
                "option_ids": {"option_id": ["val"]},
            }]
        }
        result = WecomCardEventHandler._extract_selected_id(selected, "model_select")
        assert result is None

    def test_empty_selection(self):
        result = WecomCardEventHandler._extract_selected_id(None, "model_select")
        assert result is None

    def test_empty_option_ids(self):
        selected = {
            "selected_item": [{
                "question_key": "model_select",
                "option_ids": {"option_id": []},
            }]
        }
        result = WecomCardEventHandler._extract_selected_id(selected, "model_select")
        assert result is None
