"""
电商图模式单元测试

覆盖：提示词解析、风格检测、品类检测、费用预估、ImageAgent 核心逻辑
设计文档：docs/document/TECH_电商图片Agent.md
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes.image_ecom import (
    _estimate_credits,
    _extract_style_directive,
    _is_style_adjustment,
    _parse_image_tasks,
)
from services.agent.image.image_processor import (
    detect_aspect_ratio,
    detect_dimensions,
    validate_image_bytes,
)
from services.agent.image.prompt_builder import PromptBuilder


# ============================================================
# _parse_image_tasks 测试
# ============================================================

class TestParseImageTasks:
    """测试提示词结构化拆分。"""

    def test_standard_format(self):
        content = (
            "请为以下商品生成淘宝电商图片：\n"
            "1. 白底主图 800×800：商品居中，纯白背景\n"
            "2. 场景图 800×800：浅色木桌，自然窗光\n"
            "平台：淘宝 | 共2张"
        )
        tasks = _parse_image_tasks(content)
        assert len(tasks) == 2
        assert tasks[0]["index"] == 1
        assert tasks[0]["type"] == "white_bg"
        assert tasks[0]["aspect_ratio"] == "1:1"
        assert tasks[1]["type"] == "scene"

    def test_vertical_image(self):
        content = "1. 竖图 750×1000：商品偏上，底部留白"
        tasks = _parse_image_tasks(content)
        assert len(tasks) == 1
        assert tasks[0]["aspect_ratio"] == "3:4"

    def test_detail_type(self):
        content = "1. 详情页卖点图：展示产品功能"
        tasks = _parse_image_tasks(content)
        assert tasks[0]["type"] == "detail"

    def test_fallback_on_bad_format(self):
        """格式破坏时兜底为1张。"""
        content = "随便写的没有编号的内容"
        tasks = _parse_image_tasks(content)
        assert len(tasks) == 1
        assert tasks[0]["index"] == 1
        assert tasks[0]["type"] == "main"

    def test_empty_content(self):
        tasks = _parse_image_tasks("")
        assert len(tasks) == 1


# ============================================================
# _is_style_adjustment 测试
# ============================================================

class TestStyleAdjustment:
    """测试风格调整检测（含误判防护）。"""

    def test_positive_adjustment(self):
        assert _is_style_adjustment("颜色暖一点")
        assert _is_style_adjustment("换个风格试试")
        assert _is_style_adjustment("更高级一点")
        assert _is_style_adjustment("换成国潮")

    def test_negative_satisfaction(self):
        """肯定句式不应判为调整。"""
        assert not _is_style_adjustment("这个风格很好")
        assert not _is_style_adjustment("不错，就这个")
        assert not _is_style_adjustment("满意")
        assert not _is_style_adjustment("保持这个风格")

    def test_unrelated_text(self):
        assert not _is_style_adjustment("帮我查一下销量")
        assert not _is_style_adjustment("做个白底主图")


# ============================================================
# PromptBuilder 测试
# ============================================================

class TestPromptBuilder:
    """测试四层提示词组装器。"""

    def setup_method(self):
        self.builder = PromptBuilder()

    def test_detect_category_cosmetics(self):
        assert self.builder.detect_category("淘宝口红主图") == "cosmetics"

    def test_detect_category_pets(self):
        assert self.builder.detect_category("猫粮白底图") == "pets"

    def test_detect_category_general(self):
        assert self.builder.detect_category("随便什么商品") == "general"

    def test_detect_category_longer_keyword_priority(self):
        """长关键词优先（"宠物服" 优先于 "服"）。"""
        result = self.builder.detect_category("宠物服装")
        assert result == "pets"  # "宠物" 比 "服" 长

    def test_detect_platform_from_text(self):
        assert self.builder.detect_platform("京东主图", "taobao") == "jd"
        assert self.builder.detect_platform("拼多多白底", "taobao") == "pdd"

    def test_detect_platform_default(self):
        assert self.builder.detect_platform("做个主图", "taobao") == "taobao"

    def test_style_matrix(self):
        assert self.builder.resolve_style_from_matrix("cosmetics", "pdd") == "xiaohongshu_style"
        assert self.builder.resolve_style_from_matrix("jewelry", "taobao") == "luxury"
        assert self.builder.resolve_style_from_matrix("general", "taobao") is None

    def test_build_system_prompt_four_layers(self):
        prompt = self.builder.build_system_prompt("cosmetics", "taobao", "fresh")
        assert "5要素" in prompt       # 第1层
        assert "美妆" in prompt        # 第2层
        assert "淘宝" in prompt        # 第3层
        assert "清新" in prompt        # 第4层

    def test_build_system_prompt_no_style(self):
        prompt = self.builder.build_system_prompt("electronics", "jd", None)
        assert "电子产品" in prompt
        assert "京东" in prompt

    def test_build_enhance_prompt(self):
        ep = self.builder.build_enhance_prompt("做个主图", "taobao", has_images=True)
        assert "用户需求" in ep
        assert "商品图片" in ep

    def test_build_enhance_prompt_multi_image(self):
        ep = self.builder.build_enhance_prompt("做个主图", "taobao", has_images=True, num_images=3)
        assert "第1张" in ep  # MULTI_IMAGE_GUIDE 内容

    def test_build_final_prompt_with_style(self):
        fp = self.builder.build_final_prompt("白底主图", "暖色调，大面积留白")
        assert "全局风格约束" in fp
        assert "暖色调" in fp

    def test_build_final_prompt_without_style(self):
        fp = self.builder.build_final_prompt("白底主图", "")
        assert fp == "白底主图"


# ============================================================
# 辅助函数测试
# ============================================================

class TestHelpers:

    def test_estimate_credits(self):
        result = _estimate_credits(3)
        assert result["image_count"] == 3
        assert result["estimated_credits"] == 24
        assert result["per_image_credits"] == 8

    def test_extract_style_directive(self):
        content = (
            "1. 白底主图 800×800：\n"
            "配色暖色调，主色米白\n"
            "光线自然侧光\n"
            "其他无关内容\n"
        )
        style = _extract_style_directive(content)
        assert "配色" in style
        assert "光线" in style

    def test_extract_style_directive_fallback(self):
        content = "没有任何风格关键词的内容" * 20
        style = _extract_style_directive(content)
        assert len(style) <= 250  # 兜底截取前200字符（中文字符计数）


# ============================================================
# ImageProcessor 测试
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
# ImageAgent 输入校验测试
# ============================================================

class TestImageAgentValidation:

    def setup_method(self):
        from services.agent.image.image_agent import ImageAgent
        self.agent = ImageAgent(db=None, user_id="test", conversation_id="test")

    def test_empty_task(self):
        err = self.agent._validate_input("", [])
        assert err is not None
        assert "空" in err

    def test_too_long_task(self):
        err = self.agent._validate_input("x" * 2001, [])
        assert err is not None
        assert "过长" in err

    def test_valid_task(self):
        err = self.agent._validate_input("白底主图 800×800：商品居中", [])
        assert err is None

    def test_invalid_url(self):
        err = self.agent._validate_input("白底主图", ["https://evil.com/img.jpg"])
        assert err is not None
        assert "不支持" in err

    def test_valid_url(self):
        err = self.agent._validate_input(
            "白底主图",
            ["https://cdn.everydayai.com.cn/img/test.jpg"],
        )
        assert err is None


# ============================================================
# ImageAgent._select_model 测试
# ============================================================

class TestImageAgentSelectModel:

    def setup_method(self):
        from services.agent.image.image_agent import ImageAgent
        self.agent = ImageAgent(db=None, user_id="test", conversation_id="test")

    @patch("core.config.get_settings")
    def test_select_text_to_image(self, mock_settings):
        """无参考图 → 文生图模型。"""
        mock_settings.return_value.image_agent_kie_model = "gpt-image-2-text-to-image"
        mock_settings.return_value.image_agent_kie_i2i_model = "gpt-image-2-image-to-image"
        assert self.agent._select_model([]) == "gpt-image-2-text-to-image"

    @patch("core.config.get_settings")
    def test_select_image_to_image(self, mock_settings):
        """有参考图 → 图生图模型。"""
        mock_settings.return_value.image_agent_kie_model = "gpt-image-2-text-to-image"
        mock_settings.return_value.image_agent_kie_i2i_model = "gpt-image-2-image-to-image"
        assert self.agent._select_model(["https://cdn/img.jpg"]) == "gpt-image-2-image-to-image"


# ============================================================
# ImageAgent._error_result 测试
# ============================================================

class TestImageAgentErrorResult:

    def setup_method(self):
        from services.agent.image.image_agent import ImageAgent
        self.agent = ImageAgent(db=None, user_id="test", conversation_id="test")

    def test_error_result_contains_retry_context(self):
        """失败结果必须含 retry_context。"""
        result = self.agent._error_result(
            "生成超时",
            task="白底主图 800×800：商品居中",
            image_urls=["https://cdn/img.jpg"],
            platform="taobao",
            style_directive="暖色调",
        )
        assert result.status == "error"
        assert result.collected_files is not None
        assert len(result.collected_files) == 1

        cf = result.collected_files[0]
        assert cf["failed"] is True
        assert cf["url"] is None
        assert cf["width"] == 800
        assert cf["height"] == 800
        assert "retry_context" in cf
        assert cf["retry_context"]["task"] == "白底主图 800×800：商品居中"
        assert cf["retry_context"]["platform"] == "taobao"
        assert cf["retry_context"]["style_directive"] == "暖色调"

    def test_error_result_vertical_dimensions(self):
        """竖图失败时尺寸应为 750×1000，不是 800×800。"""
        result = self.agent._error_result(
            "失败", task="竖图 750×1000：商品偏上",
            image_urls=[], platform="taobao", style_directive="",
        )
        cf = result.collected_files[0]
        assert cf["width"] == 750
        assert cf["height"] == 1000


# ============================================================
# ImageAgent.execute 集成测试（mock 外部依赖）
# ============================================================

class TestImageAgentExecute:
    """ImageAgent.execute 核心链路测试（mock KIE adapter + CreditMixin）。"""

    def _make_agent(self):
        from services.agent.image.image_agent import ImageAgent
        db = MagicMock()
        agent = ImageAgent(db=db, user_id="u1", conversation_id="c1", org_id="o1")
        return agent, db

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """正常生成：锁积分 → 调 KIE → 确认扣费 → 返回图片 URL。"""
        agent, db = self._make_agent()

        # mock KIE adapter
        mock_result = MagicMock()
        mock_result.image_urls = ["https://cdn/generated.png"]
        mock_result.fail_msg = None

        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with patch("services.agent.image.image_agent.create_image_adapter", return_value=mock_adapter), \
             patch("services.agent.image.image_agent.calculate_image_cost", return_value={"user_credits": 6}), \
             patch.object(agent, "_lock_credits", return_value="tx_123"), \
             patch.object(agent, "_confirm_deduct") as mock_confirm:

            result = await agent.execute(
                task="白底主图 800×800：商品居中",
                platform="taobao",
            )

        assert result.status == "success"
        assert result.collected_files[0]["url"] == "https://cdn/generated.png"
        mock_confirm.assert_called_once_with("tx_123")
        mock_adapter.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_generation_failure_refunds(self):
        """KIE 生成失败：退还积分 + 返回 failed ImagePart。"""
        agent, db = self._make_agent()

        mock_result = MagicMock()
        mock_result.image_urls = []
        mock_result.fail_msg = "内容违规"

        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(return_value=mock_result)
        mock_adapter.close = AsyncMock()

        with patch("services.agent.image.image_agent.create_image_adapter", return_value=mock_adapter), \
             patch("services.agent.image.image_agent.calculate_image_cost", return_value={"user_credits": 6}), \
             patch.object(agent, "_lock_credits", return_value="tx_456"), \
             patch.object(agent, "_refund_credits") as mock_refund:

            result = await agent.execute(task="白底主图", platform="taobao")

        assert result.status == "error"
        assert "内容违规" in result.summary
        assert result.collected_files[0]["failed"] is True
        assert result.collected_files[0]["retry_context"]["task"] == "白底主图"
        mock_refund.assert_called_once_with("tx_456")

    @pytest.mark.asyncio
    async def test_execute_insufficient_credits(self):
        """积分不足：返回错误，无 collected_files（不显示占位符）。"""
        from core.exceptions import InsufficientCreditsError
        agent, db = self._make_agent()

        with patch("services.agent.image.image_agent.calculate_image_cost", return_value={"user_credits": 100}), \
             patch.object(agent, "_lock_credits", side_effect=InsufficientCreditsError(required=100, current=5)):

            result = await agent.execute(task="白底主图", platform="taobao")

        assert result.status == "error"
        assert "积分" in result.summary or "不足" in result.summary
        # 积分不足不返回 failed ImagePart（不显示占位符）
        assert result.collected_files is None

    @pytest.mark.asyncio
    async def test_execute_adapter_exception_refunds(self):
        """KIE adapter 异常：退还积分 + 返回 failed ImagePart。"""
        agent, db = self._make_agent()

        mock_adapter = AsyncMock()
        mock_adapter.generate = AsyncMock(side_effect=ConnectionError("timeout"))
        mock_adapter.close = AsyncMock()

        with patch("services.agent.image.image_agent.create_image_adapter", return_value=mock_adapter), \
             patch("services.agent.image.image_agent.calculate_image_cost", return_value={"user_credits": 6}), \
             patch.object(agent, "_lock_credits", return_value="tx_789"), \
             patch.object(agent, "_refund_credits") as mock_refund:

            result = await agent.execute(task="场景图", platform="jd")

        assert result.status == "error"
        assert result.collected_files[0]["failed"] is True
        mock_refund.assert_called_once_with("tx_789")


# ============================================================
# _is_style_adjustment 边界测试
# ============================================================

class TestStyleAdjustmentEdgeCases:

    def test_empty_string(self):
        assert not _is_style_adjustment("")

    def test_mixed_satisfaction_and_adjustment(self):
        """包含"很好"又包含"暖一点"：肯定排除优先。"""
        assert not _is_style_adjustment("很好但暖一点")

    def test_pure_adjustment_no_satisfaction(self):
        assert _is_style_adjustment("调整一下配色")

    def test_ambiguous_style_mention(self):
        """提到"风格"但不是调整请求。"""
        assert not _is_style_adjustment("这个风格OK")


# ============================================================
# validate_image_bytes 测试
# ============================================================

class TestValidateImageBytes:

    def test_valid_png(self):
        """用 Pillow 生成合法 PNG。"""
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
# remove_background 降级测试
# ============================================================

class TestRemoveBackground:

    @pytest.mark.asyncio
    async def test_fallback_when_rembg_not_installed(self):
        """rembg 未安装时应降级返回原图。"""
        from services.agent.image.image_processor import remove_background
        original = b"fake image bytes"
        result = await remove_background(original)
        # rembg 大概率未安装在测试环境 → 降级返回原图
        assert result == original
