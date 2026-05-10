"""
电商图模式单元测试（v2）

覆盖：方案解析、文案同步、费用预估、PromptBuilder v2、ImageAgent 核心逻辑
设计文档：docs/document/TECH_电商图片Agent_v2.md
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.image_ecom import (
    _build_multimodal_content,
    _estimate_credits,
    _parse_design_plan,
    sync_text_to_prompt,
)
from services.agent.image.image_processor import (
    detect_aspect_ratio,
    detect_dimensions,
    validate_image_bytes,
)
from services.agent.image.platform_rules import (
    format_platform_prompt,
    get_platform_rules,
)
from services.agent.image.prompt_builder import PromptBuilder


# ============================================================
# _parse_design_plan 测试（v2：JSON 解析替代 regex）
# ============================================================

class TestParseDesignPlan:
    """测试千问输出的设计方案 JSON 解析。"""

    def test_valid_json(self):
        content = '{"product_insight": "收纳盒", "visual_strategy": "暖色", "images": [{"role": "钩子图", "prompt": "p1"}]}'
        plan = _parse_design_plan(content)
        assert plan["product_insight"] == "收纳盒"
        assert len(plan["images"]) == 1
        assert plan["images"][0]["role"] == "钩子图"

    def test_json_with_wrapper_text(self):
        """千问在 JSON 前后加了解释文字。"""
        content = '好的，以下是方案：\n{"product_insight": "test", "images": [{"role": "a"}]}\n请确认。'
        plan = _parse_design_plan(content)
        assert plan["product_insight"] == "test"

    def test_fallback_on_invalid_content(self):
        """完全无法解析时返回解析失败标记。"""
        plan = _parse_design_plan("这是纯文本，没有JSON")
        assert plan["_parse_failed"] is True
        assert len(plan["images"]) == 0

    def test_empty_content(self):
        plan = _parse_design_plan("")
        assert plan["_parse_failed"] is True
        assert len(plan["images"]) == 0

    def test_multi_image_plan(self):
        content = '{"product_insight": "x", "visual_strategy": "y", "images": [{"role": "a"}, {"role": "b"}, {"role": "c"}]}'
        plan = _parse_design_plan(content)
        assert len(plan["images"]) == 3


# ============================================================
# sync_text_to_prompt 测试
# ============================================================

class TestSyncTextToPrompt:
    """测试用户编辑文案同步到 prompt。"""

    def test_replace_both(self):
        prompt = 'title "一盒搞定" in white. sub "56色分类收纳" gray.'
        result = sync_text_to_prompt(prompt, "大容量", "装下200瓶")
        assert '"大容量"' in result
        assert '"装下200瓶"' in result
        assert "一盒搞定" not in result

    def test_replace_title_only(self):
        prompt = 'title "一盒搞定" in white. No subtitle.'
        result = sync_text_to_prompt(prompt, "新标题", "")
        assert '"新标题"' in result

    def test_no_chinese_in_prompt(self):
        """白底图 prompt 无中文 → 不替换。"""
        prompt = "Pure white background. No text. No watermark."
        result = sync_text_to_prompt(prompt, "test", "test")
        assert result == prompt

    def test_empty_inputs(self):
        prompt = 'title "一盒搞定" in white.'
        result = sync_text_to_prompt(prompt, "", "")
        assert result == prompt


# ============================================================
# PromptBuilder v2 测试
# ============================================================

class TestPromptBuilderV2:
    """测试三层提示词组装器。"""

    def setup_method(self):
        self.builder = PromptBuilder()

    def test_build_system_prompt_contains_three_layers(self):
        prompt = self.builder.build_system_prompt("taobao")
        assert "gpt-image-2" in prompt       # 第1层（执行规则）
        assert "淘宝" in prompt              # 第2层（平台规则）
        assert "品类营销要点" in prompt       # 第3层（品类启发）
        assert "Preserve" in prompt          # prompt 结构

    def test_build_system_prompt_platform_switch(self):
        pdd = self.builder.build_system_prompt("pdd")
        jd = self.builder.build_system_prompt("jd")
        assert "拼多多" in pdd
        assert "京东" in jd
        assert "拼多多" not in jd

    def test_build_system_prompt_unknown_platform_fallback(self):
        prompt = self.builder.build_system_prompt("unknown_platform")
        assert "淘宝" in prompt  # 降级到淘宝

    def test_build_user_message_full(self):
        um = self.builder.build_user_message(
            product_name="收纳盒",
            platform="taobao",
            product_image_count=3,
            style_ref_count=2,
            selling_points="大容量",
            price_info="¥39.9",
            target_user="宝妈",
            image_size="800x800",
            generate_detail=True,
        )
        assert "收纳盒" in um
        assert "大容量" in um
        assert "¥39.9" in um
        assert "宝妈" in um
        assert "风格参考图：2张" in um
        assert "详情页" in um

    def test_build_user_message_minimal(self):
        um = self.builder.build_user_message(product_name="产品A")
        assert "产品A" in um
        assert "不要生成促销图" in um
        assert "风格参考图" not in um

    def test_build_final_prompt_with_style(self):
        fp = self.builder.build_final_prompt("test prompt", "warm tones")
        assert "Visual style context:" in fp
        assert "warm tones" in fp
        assert "test prompt" in fp

    def test_build_final_prompt_without_style(self):
        fp = self.builder.build_final_prompt("test prompt", "")
        assert fp == "test prompt"


# ============================================================
# platform_rules 测试
# ============================================================

class TestPlatformRules:

    def test_all_platforms_exist(self):
        for p in ["taobao", "jd", "pdd", "douyin", "xiaohongshu", "ali1688"]:
            rules = get_platform_rules(p)
            assert "label" in rules
            assert "main_image" in rules
            assert "styles" in rules

    def test_tmall_reuses_taobao(self):
        assert get_platform_rules("tmall") is get_platform_rules("taobao")

    def test_unknown_fallback(self):
        assert get_platform_rules("unknown")["label"] == "淘宝/天猫"

    def test_format_prompt_contains_key_sections(self):
        prompt = format_platform_prompt("pdd")
        assert "拼多多" in prompt
        assert "风格特征" in prompt
        assert "硬性规则" in prompt

    def test_taobao_hard_rules(self):
        prompt = format_platform_prompt("taobao")
        assert "白底" in prompt


# ============================================================
# 辅助函数测试
# ============================================================

class TestHelpers:

    def test_estimate_credits(self):
        result = _estimate_credits(3)
        assert result["image_count"] == 3
        assert result["estimated_credits"] == 24
        assert result["per_image_credits"] == 8

    def test_build_multimodal_product_and_style(self):
        """产品图 + 风格参考图 → 正确顺序拼接。"""
        parts = _build_multimodal_content("hello", ["p1.jpg", "p2.jpg"], ["s1.jpg"])
        assert len(parts) == 4
        assert parts[0] == {"type": "text", "text": "hello"}
        assert parts[1]["image_url"]["url"] == "p1.jpg"
        assert parts[2]["image_url"]["url"] == "p2.jpg"
        assert parts[3]["image_url"]["url"] == "s1.jpg"

    def test_build_multimodal_no_style_ref(self):
        """无风格参考图 → 只有文字+产品图。"""
        parts = _build_multimodal_content("text", ["p1.jpg"], [])
        assert len(parts) == 2

    def test_build_multimodal_no_images(self):
        """无图片 → 只有文字。"""
        parts = _build_multimodal_content("text", [], [])
        assert len(parts) == 1
        assert parts[0]["type"] == "text"


# ============================================================
# ImageProcessor 测试（不变）
# ============================================================

class TestImageProcessor:

    def test_detect_dimensions_vertical(self):
        assert detect_dimensions("竖图 750×1000", "taobao") == (750, 1000)

    def test_detect_dimensions_3_4(self):
        assert detect_dimensions("3:4 商品图", "taobao") == (750, 1000)

    def test_detect_dimensions_pdd(self):
        assert detect_dimensions("白底主图", "pdd") == (480, 480)

    def test_detect_dimensions_default(self):
        assert detect_dimensions("普通主图", "taobao") == (800, 800)

    def test_detect_aspect_ratio(self):
        assert detect_aspect_ratio("竖图 750×1000") == "3:4"
        assert detect_aspect_ratio("普通主图") == "1:1"
        assert detect_aspect_ratio("横图 16:9") == "16:9"


# ============================================================
# ImageAgent 测试（不变）
# ============================================================

class TestImageAgentValidation:

    def setup_method(self):
        from services.agent.image.image_agent import ImageAgent
        self.agent = ImageAgent(db=None, user_id="test", conversation_id="test")

    def test_empty_task(self):
        err = self.agent._validate_input("", [])
        assert err is not None and "空" in err

    def test_too_long_task(self):
        err = self.agent._validate_input("x" * 2001, [])
        assert err is not None and "过长" in err

    def test_valid_task(self):
        assert self.agent._validate_input("白底主图", []) is None

    def test_invalid_url(self):
        err = self.agent._validate_input("主图", ["https://evil.com/img.jpg"])
        assert err is not None and "不支持" in err

    def test_valid_url(self):
        assert self.agent._validate_input("主图", ["https://cdn.everydayai.com.cn/img/t.jpg"]) is None


class TestImageAgentSelectModel:

    def setup_method(self):
        from services.agent.image.image_agent import ImageAgent
        self.agent = ImageAgent(db=None, user_id="test", conversation_id="test")

    @patch("core.config.get_settings")
    def test_select_text_to_image(self, mock_settings):
        mock_settings.return_value.image_agent_kie_model = "gpt-image-2-text-to-image"
        mock_settings.return_value.image_agent_kie_i2i_model = "gpt-image-2-image-to-image"
        assert self.agent._select_model([]) == "gpt-image-2-text-to-image"

    @patch("core.config.get_settings")
    def test_select_image_to_image(self, mock_settings):
        mock_settings.return_value.image_agent_kie_model = "gpt-image-2-text-to-image"
        mock_settings.return_value.image_agent_kie_i2i_model = "gpt-image-2-image-to-image"
        assert self.agent._select_model(["https://cdn/img.jpg"]) == "gpt-image-2-image-to-image"


class TestImageAgentErrorResult:

    def setup_method(self):
        from services.agent.image.image_agent import ImageAgent
        self.agent = ImageAgent(db=None, user_id="test", conversation_id="test")

    def test_error_result_contains_retry_context(self):
        result = self.agent._error_result(
            "生成超时", task="白底主图 800×800：商品居中",
            image_urls=["https://cdn/img.jpg"], platform="taobao", style_directive="暖色调",
        )
        assert result.status == "error"
        cf = result.collected_files[0]
        assert cf["failed"] is True
        assert cf["retry_context"]["task"] == "白底主图 800×800：商品居中"


class TestImageAgentExecute:

    def _make_agent(self):
        from services.agent.image.image_agent import ImageAgent
        return ImageAgent(db=MagicMock(), user_id="u1", conversation_id="c1", org_id="o1"), MagicMock()

    @pytest.mark.asyncio
    async def test_execute_success(self):
        agent, _ = self._make_agent()
        mock_result = MagicMock(image_urls=["https://cdn/gen.png"], fail_msg=None)
        mock_adapter = AsyncMock(generate=AsyncMock(return_value=mock_result), close=AsyncMock())

        with patch("services.agent.image.image_agent.create_image_adapter", return_value=mock_adapter), \
             patch("services.agent.image.image_agent.calculate_image_cost", return_value={"user_credits": 6}), \
             patch.object(agent, "_lock_credits", return_value="tx_1"), \
             patch.object(agent, "_confirm_deduct") as mock_confirm:
            result = await agent.execute(task="白底主图", platform="taobao")

        assert result.status == "success"
        assert result.collected_files[0]["url"] == "https://cdn/gen.png"
        mock_confirm.assert_called_once_with("tx_1")

    @pytest.mark.asyncio
    async def test_execute_failure_refunds(self):
        agent, _ = self._make_agent()
        mock_result = MagicMock(image_urls=[], fail_msg="违规")
        mock_adapter = AsyncMock(generate=AsyncMock(return_value=mock_result), close=AsyncMock())

        with patch("services.agent.image.image_agent.create_image_adapter", return_value=mock_adapter), \
             patch("services.agent.image.image_agent.calculate_image_cost", return_value={"user_credits": 6}), \
             patch.object(agent, "_lock_credits", return_value="tx_2"), \
             patch.object(agent, "_refund_credits") as mock_refund:
            result = await agent.execute(task="白底主图", platform="taobao")

        assert result.status == "error"
        mock_refund.assert_called_once_with("tx_2")

    @pytest.mark.asyncio
    async def test_execute_insufficient_credits(self):
        from core.exceptions import InsufficientCreditsError
        agent, _ = self._make_agent()

        with patch("services.agent.image.image_agent.calculate_image_cost", return_value={"user_credits": 100}), \
             patch.object(agent, "_lock_credits", side_effect=InsufficientCreditsError(required=100, current=5)):
            result = await agent.execute(task="白底主图", platform="taobao")

        assert result.status == "error"
        assert result.collected_files is None


# ============================================================
# validate_image_bytes 测试（不变）
# ============================================================

class TestValidateImageBytes:

    def test_valid_png(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, format="PNG")
        assert validate_image_bytes(buf.getvalue()) is True

    def test_invalid_bytes(self):
        assert validate_image_bytes(b"not an image") is False

    def test_empty_bytes(self):
        assert validate_image_bytes(b"") is False


# ============================================================
# remove_background 降级测试（不变）
# ============================================================

class TestRemoveBackground:

    @pytest.mark.asyncio
    async def test_fallback_when_rembg_not_installed(self):
        from services.agent.image.image_processor import remove_background
        original = b"fake image bytes"
        result = await remove_background(original)
        assert result == original
