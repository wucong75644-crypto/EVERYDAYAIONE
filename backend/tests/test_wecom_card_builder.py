"""WecomCardBuilder 单元测试 — 验证每种卡片的 JSON 结构"""

import pytest
from services.wecom.card_builder import WecomCardBuilder, WECOM_MODEL_OPTIONS


class TestWelcomeCard:
    def test_structure(self):
        card = WecomCardBuilder.welcome_card()
        assert card["card_type"] == "button_interaction"
        assert "main_title" in card
        assert "button_list" in card
        assert "task_id" in card

    def test_buttons(self):
        card = WecomCardBuilder.welcome_card()
        keys = [b["key"] for b in card["button_list"]]
        assert "start_chat" in keys
        assert "show_help" in keys
        assert "check_credits" in keys
        assert len(card["button_list"]) <= 6

    def test_task_id_unique(self):
        c1 = WecomCardBuilder.welcome_card()
        c2 = WecomCardBuilder.welcome_card()
        assert c1["task_id"] != c2["task_id"]

    def test_task_id_format(self):
        card = WecomCardBuilder.welcome_card()
        tid = card["task_id"]
        assert tid.startswith("card_")
        assert len(tid.encode()) <= 128


class TestHelpCard:
    def test_structure(self):
        card = WecomCardBuilder.help_card()
        assert card["card_type"] == "button_interaction"
        assert len(card["button_list"]) <= 6

    def test_all_buttons_have_key(self):
        card = WecomCardBuilder.help_card()
        for btn in card["button_list"]:
            assert "key" in btn
            assert "text" in btn
            assert "style" in btn


class TestCreditsCard:
    def test_structure(self):
        card = WecomCardBuilder.credits_card(1234)
        assert card["card_type"] == "text_notice"
        assert card["emphasis_content"]["title"] == "1234"

    def test_zero_balance(self):
        card = WecomCardBuilder.credits_card(0)
        assert card["emphasis_content"]["title"] == "0"


class TestCreditsInsufficientCard:
    def test_structure(self):
        card = WecomCardBuilder.credits_insufficient_card(100, 50, "图片")
        assert card["card_type"] == "button_interaction"
        assert "100" in card["main_title"]["desc"]
        assert "50" in card["main_title"]["desc"]
        assert "图片" in card["main_title"]["desc"]


class TestMemoryListCard:
    def test_with_memories(self):
        memories = [{"memory": f"记忆{i}"} for i in range(10)]
        card = WecomCardBuilder.memory_list_card(memories)
        assert card["card_type"] == "button_interaction"
        assert "共 10 条" in card["main_title"]["title"]
        # horizontal_content_list 最多 6 条
        assert len(card["horizontal_content_list"]) == 6

    def test_with_few_memories(self):
        memories = [{"memory": "test"}]
        card = WecomCardBuilder.memory_list_card(memories)
        assert len(card["horizontal_content_list"]) == 1

    def test_long_memory_truncated(self):
        memories = [{"memory": "a" * 100}]
        card = WecomCardBuilder.memory_list_card(memories)
        value = card["horizontal_content_list"][0]["value"]
        assert len(value) <= 30

    def test_clear_button(self):
        card = WecomCardBuilder.memory_list_card([{"memory": "x"}])
        keys = [b["key"] for b in card["button_list"]]
        assert "clear_all_memory" in keys


class TestMemoryEmptyCard:
    def test_structure(self):
        card = WecomCardBuilder.memory_empty_card()
        assert card["card_type"] == "text_notice"
        assert "暂无" in card["main_title"]["title"]


class TestModelSelectCard:
    def test_default_models(self):
        card = WecomCardBuilder.model_select_card()
        assert card["card_type"] == "multiple_interaction"
        assert len(card["select_list"]) == 1
        options = card["select_list"][0]["option_list"]
        assert len(options) == len(WECOM_MODEL_OPTIONS)
        assert len(options) <= 10

    def test_with_current_model(self):
        card = WecomCardBuilder.model_select_card(current_model="deepseek-v3.2")
        assert card["select_list"][0]["selected_id"] == "deepseek-v3.2"
        assert "deepseek-v3.2" in card["main_title"]["desc"]

    def test_custom_models(self):
        models = [{"id": "m1", "text": "Model 1"}, {"id": "m2", "text": "Model 2"}]
        card = WecomCardBuilder.model_select_card(models=models)
        assert len(card["select_list"][0]["option_list"]) == 2

    def test_submit_button(self):
        card = WecomCardBuilder.model_select_card()
        assert card["submit_button"]["key"] == "submit_model"


class TestThinkingModeCard:
    def test_deep_mode(self):
        card = WecomCardBuilder.thinking_mode_card("deep")
        assert "深度思考" in card["main_title"]["desc"]
        # 深度思考按钮应为 style=1
        deep_btn = next(b for b in card["button_list"] if b["key"] == "thinking_deep")
        assert deep_btn["style"] == 1

    def test_fast_mode(self):
        card = WecomCardBuilder.thinking_mode_card("fast")
        assert "快速回复" in card["main_title"]["desc"]
        fast_btn = next(b for b in card["button_list"] if b["key"] == "thinking_fast")
        assert fast_btn["style"] == 1


class TestNewConversationCard:
    def test_structure(self):
        card = WecomCardBuilder.new_conversation_card()
        assert card["card_type"] == "text_notice"
        assert "新对话" in card["main_title"]["title"]


class TestGenerationDoneCard:
    def test_structure(self):
        card = WecomCardBuilder.generation_done_card("图片", "一只可爱的猫")
        assert card["card_type"] == "button_interaction"
        assert "图片" in card["main_title"]["title"]
        keys = [b["key"] for b in card["button_list"]]
        assert "gen_confirm" in keys
        assert "gen_retry" in keys

    def test_long_prompt_truncated(self):
        long_prompt = "x" * 100
        card = WecomCardBuilder.generation_done_card("视频", long_prompt)
        assert len(card["main_title"]["desc"]) <= 50


class TestUpdateCards:
    def test_memory_cleared(self):
        card = WecomCardBuilder.memory_cleared_card()
        assert card["card_type"] == "button_interaction"
        assert "已清空" in card["main_title"]["title"]

    def test_model_switched(self):
        card = WecomCardBuilder.model_switched_card("DeepSeek V3.2")
        assert card["card_type"] == "text_notice"
        assert "DeepSeek V3.2" in card["main_title"]["desc"]

    def test_thinking_switched(self):
        card = WecomCardBuilder.thinking_switched_card("deep")
        assert "深度思考" in card["main_title"]["title"]

    def test_generation_confirmed(self):
        card = WecomCardBuilder.generation_confirmed_card("图片")
        assert "已确认" in card["main_title"]["title"]
