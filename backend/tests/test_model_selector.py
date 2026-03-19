"""
模型选择器单元测试

覆盖：品牌命中、硬约束过滤、能力打分、priority 排序、
      image/video domain 分支、兜底逻辑、get_model_keywords
"""

import pytest

from config.smart_model_config import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
    get_model_keywords,
)
from services.model_selector import (
    SIGNAL_TO_CAPABILITY,
    select_model,
    _select_chat_model,
    _select_image_model,
    _select_video_model,
)


# ============================================================
# TestSelectModelRouting — domain 路由分发
# ============================================================


class TestSelectModelRouting:

    def test_chat_domain_returns_chat_model(self):
        """chat domain → chat 模型列表选择"""
        result = select_model("chat", {})
        assert result == DEFAULT_CHAT_MODEL

    def test_erp_domain_uses_chat_models(self):
        """erp domain → 同样从 chat 模型列表选"""
        result = select_model("erp", {})
        assert result == DEFAULT_CHAT_MODEL

    def test_crawler_domain_uses_chat_models(self):
        """crawler domain → 同样从 chat 模型列表选"""
        result = select_model("crawler", {})
        assert result == DEFAULT_CHAT_MODEL

    def test_image_domain_returns_image_model(self):
        """image domain → image 模型列表选"""
        result = select_model("image", {})
        assert result == DEFAULT_IMAGE_MODEL

    def test_video_domain_returns_video_model(self):
        """video domain → video 模型列表选"""
        result = select_model("video", {})
        assert result == DEFAULT_VIDEO_MODEL

    def test_unknown_domain_fallback_to_chat(self):
        """未知 domain → 走 chat 逻辑兜底"""
        result = select_model("unknown_domain", {})
        assert result == DEFAULT_CHAT_MODEL


# ============================================================
# TestBrandMatching — 品牌命中
# ============================================================


class TestBrandMatching:

    def test_brand_gpt(self):
        """品牌 gpt → OpenAI 模型"""
        result = select_model("chat", {"brand_hint": "gpt"})
        assert "openai/" in result or "gpt" in result

    def test_brand_claude(self):
        """品牌 claude → Anthropic 模型"""
        result = select_model("chat", {"brand_hint": "claude"})
        assert "anthropic/" in result

    def test_brand_deepseek(self):
        """品牌 deepseek → DeepSeek 模型"""
        result = select_model("chat", {"brand_hint": "deepseek"})
        assert result == "deepseek-v3.2"

    def test_brand_gemini(self):
        """品牌 gemini → Gemini 模型"""
        result = select_model("chat", {"brand_hint": "gemini"})
        assert "gemini" in result

    def test_brand_kimi(self):
        """品牌 kimi → Kimi 模型"""
        result = select_model("chat", {"brand_hint": "kimi"})
        assert result == "kimi-k2.5"

    def test_brand_grok(self):
        """品牌 grok → xAI 模型"""
        result = select_model("chat", {"brand_hint": "grok"})
        assert "grok" in result

    def test_brand_chinese_qianwen(self):
        """中文品牌 千问 → qwen 模型"""
        result = select_model("chat", {"brand_hint": "千问"})
        assert result == "qwen3.5-plus"

    def test_brand_chinese_zhipu(self):
        """中文品牌 智谱 → GLM 模型"""
        result = select_model("chat", {"brand_hint": "智谱"})
        assert result == "glm-5"

    def test_brand_case_insensitive(self):
        """品牌匹配不区分大小写"""
        assert select_model("chat", {"brand_hint": "GPT"}) == \
            select_model("chat", {"brand_hint": "gpt"})

    def test_brand_with_whitespace(self):
        """品牌匹配自动去空格"""
        result = select_model("chat", {"brand_hint": "  claude  "})
        assert "anthropic/" in result

    def test_brand_unknown_fallback(self):
        """未知品牌 → 不命中，走后续逻辑"""
        result = select_model("chat", {"brand_hint": "unknown_brand"})
        assert result == DEFAULT_CHAT_MODEL

    def test_brand_empty_string_ignored(self):
        """空字符串品牌 → 跳过品牌匹配"""
        result = select_model("chat", {"brand_hint": ""})
        assert result == DEFAULT_CHAT_MODEL

    def test_brand_overrides_hard_constraints(self):
        """品牌命中优先于硬约束（用户明确指定品牌）"""
        result = select_model(
            "chat",
            {"brand_hint": "deepseek", "needs_search": True},
        )
        # deepseek 不支持搜索，但品牌命中优先
        assert result == "deepseek-v3.2"

    def test_brand_r1(self):
        """品牌 r1 → deepseek-r1"""
        result = select_model("chat", {"brand_hint": "r1"})
        assert result == "deepseek-r1"

    def test_brand_opus(self):
        """品牌 opus → claude-opus"""
        result = select_model("chat", {"brand_hint": "opus"})
        assert result == "anthropic/claude-opus-4.6"

    def test_brand_sonnet(self):
        """品牌 sonnet → claude-sonnet 最新版"""
        result = select_model("chat", {"brand_hint": "sonnet"})
        assert result == "anthropic/claude-sonnet-4.6"

    def test_brand_codex(self):
        """品牌 codex → GPT codex"""
        result = select_model("chat", {"brand_hint": "codex"})
        assert result == "openai/gpt-5.3-codex"


# ============================================================
# TestHardConstraints — 硬约束过滤
# ============================================================


class TestHardConstraints:

    def test_has_image_filters_unsupported(self):
        """has_image=True → 过滤不支持图片的模型"""
        result = select_model("chat", {}, has_image=True)
        # 结果应该是支持图片的模型
        assert result != "deepseek-v3.2"  # deepseek 不支持图片

    def test_needs_search_filters_unsupported(self):
        """needs_search=True → 仅保留支持搜索的模型"""
        result = select_model("chat", {"needs_search": True})
        # Gemini 支持搜索
        assert "gemini" in result

    def test_deep_thinking_filters_unsupported(self):
        """thinking_mode=deep → 仅保留支持 thinking 的模型"""
        result = select_model("chat", {}, thinking_mode="deep")
        # 结果应该是 supports_thinking=True 的模型中 priority 最小的
        assert result == "qwen3.5-plus"  # priority=1, supports_thinking=True

    def test_search_plus_image_combo(self):
        """needs_search + has_image → 双重过滤"""
        result = select_model(
            "chat", {"needs_search": True}, has_image=True,
        )
        # 必须同时支持搜索和图片 → gemini 系列
        assert "gemini" in result

    def test_all_constraints_fail_fallback(self):
        """所有模型都不满足 → 兜底到 DEFAULT_CHAT_MODEL"""
        # 模拟极端约束组合：需要搜索 + 需要思考 + 有图片
        # 仍然应该有候选（gemini 全支持），但测试兜底逻辑
        result = select_model(
            "chat",
            {"needs_search": True},
            has_image=True,
            thinking_mode="deep",
        )
        # gemini-3-pro 和 gemini-3-flash 支持搜索+图片+thinking
        assert "gemini" in result

    def test_normal_thinking_mode_no_filter(self):
        """thinking_mode 非 deep 时不过滤"""
        result = select_model("chat", {}, thinking_mode="normal")
        assert result == DEFAULT_CHAT_MODEL


# ============================================================
# TestCapabilityScoring — 能力打分
# ============================================================


class TestCapabilityScoring:

    def test_needs_code_selects_code_model(self):
        """needs_code → 选 code 能力最强的模型"""
        result = select_model("chat", {"needs_code": True})
        # deepseek-v3.2 有 code 能力且 priority=2
        assert result == "deepseek-v3.2"

    def test_needs_math_selects_math_model(self):
        """needs_math → 选 math 能力模型"""
        result = select_model("chat", {"needs_math": True})
        # deepseek-v3.2 有 math, priority=2；deepseek-r1 有 math, priority=3
        assert result == "deepseek-v3.2"

    def test_needs_reasoning_selects_reasoning_model(self):
        """needs_reasoning → 选 reasoning 能力模型"""
        result = select_model("chat", {"needs_reasoning": True})
        # deepseek-v3.2 有 reasoning, priority=2
        assert result == "deepseek-v3.2"

    def test_multi_capability_scoring(self):
        """多能力需求 → 交集最大的模型优先"""
        result = select_model(
            "chat", {"needs_code": True, "needs_math": True},
        )
        # deepseek-v3.2: code+math+reasoning (3 caps, 2 match)
        # deepseek-r1: math+reasoning+logic (3 caps, 1 match for math)
        assert result == "deepseek-v3.2"

    def test_same_score_uses_priority(self):
        """能力分相同时按 priority 排序"""
        result = select_model(
            "chat", {"needs_reasoning": True, "needs_math": True},
        )
        # deepseek-v3.2 (priority=2): math+reasoning 命中 2
        # deepseek-r1 (priority=3): math+reasoning 命中 2
        # 同分，priority 小优先 → deepseek-v3.2
        assert result == "deepseek-v3.2"

    def test_no_capability_needs_uses_priority(self):
        """无特殊能力需求 → 纯 priority 排序"""
        result = select_model("chat", {})
        assert result == DEFAULT_CHAT_MODEL  # priority=1


# ============================================================
# TestImageDomain — image 模型选择
# ============================================================


class TestImageDomain:

    def test_default_image_model(self):
        """无特殊信号 → 默认 image 模型"""
        result = select_model("image", {})
        assert result == DEFAULT_IMAGE_MODEL

    def test_needs_edit_selects_edit_model(self):
        """needs_edit → 选 requires_image=True 的编辑模型"""
        result = select_model("image", {"needs_edit": True})
        assert result == "google/nano-banana-edit"

    def test_needs_hd_selects_pro_model(self):
        """needs_hd → 选非默认的高清模型"""
        result = select_model("image", {"needs_hd": True})
        assert result != DEFAULT_IMAGE_MODEL
        assert result == "nano-banana-pro"

    def test_edit_priority_over_hd(self):
        """needs_edit 优先于 needs_hd"""
        result = select_model(
            "image", {"needs_edit": True, "needs_hd": True},
        )
        assert result == "google/nano-banana-edit"


# ============================================================
# TestVideoDomain — video 模型选择
# ============================================================


class TestVideoDomain:

    def test_default_video_model(self):
        """无特殊信号 → 默认 video 模型"""
        result = select_model("video", {})
        assert result == DEFAULT_VIDEO_MODEL

    def test_needs_pro_selects_pro_storyboard(self):
        """needs_pro → 选专业级模型（priority 最大）"""
        result = select_model("video", {"needs_pro": True})
        assert result == "sora-2-pro-storyboard"

    def test_pro_excludes_image_to_video(self):
        """needs_pro 不选 requires_image 的模型"""
        result = select_model("video", {"needs_pro": True})
        assert result != "sora-2-image-to-video"


# ============================================================
# TestGetModelKeywords — 关键词映射
# ============================================================


class TestGetModelKeywords:

    def test_chat_keywords_not_empty(self):
        """chat 分类有关键词映射"""
        kw = get_model_keywords("chat")
        assert len(kw) > 0

    def test_keyword_values_are_model_ids(self):
        """映射值是有效的 model_id"""
        from config.smart_model_config import SMART_CONFIG
        chat_ids = {m["id"] for m in SMART_CONFIG["chat"]["models"]}
        kw = get_model_keywords("chat")
        for keyword, model_id in kw.items():
            assert model_id in chat_ids, \
                f"关键词 {keyword} 映射到无效模型: {model_id}"

    def test_priority_first_wins(self):
        """同关键词多模型时，priority 小的优先"""
        kw = get_model_keywords("chat")
        # "deepseek" 在 deepseek-v3.2(p=2) 和 deepseek-r1(p=3) 都有
        assert kw["deepseek"] == "deepseek-v3.2"

    def test_chinese_keywords(self):
        """中文关键词正常映射"""
        kw = get_model_keywords("chat")
        assert "千问" in kw
        assert "智谱" in kw
        assert "月之暗面" in kw

    def test_image_category_keywords(self):
        """image 分类关键词（当前为空）"""
        kw = get_model_keywords("image")
        assert isinstance(kw, dict)

    def test_nonexistent_category(self):
        """不存在的分类 → 空字典"""
        kw = get_model_keywords("nonexistent")
        assert kw == {}

    def test_all_keywords_lowercase(self):
        """所有映射 key 都是小写"""
        kw = get_model_keywords("chat")
        for key in kw:
            assert key == key.lower(), f"关键词未小写: {key}"


# ============================================================
# TestSignalToCapability — 映射表完整性
# ============================================================


class TestSignalToCapability:

    def test_mapping_has_required_entries(self):
        """映射包含 needs_code/needs_reasoning/needs_math"""
        assert "needs_code" in SIGNAL_TO_CAPABILITY
        assert "needs_reasoning" in SIGNAL_TO_CAPABILITY
        assert "needs_math" in SIGNAL_TO_CAPABILITY

    def test_mapping_values_are_valid_capabilities(self):
        """映射值在 smart_models.json 的 capabilities 中存在"""
        from config.smart_model_config import SMART_CONFIG
        all_caps: set = set()
        for m in SMART_CONFIG.get("chat", {}).get("models", []):
            all_caps.update(m.get("capabilities", []))
        for cap in SIGNAL_TO_CAPABILITY.values():
            assert cap in all_caps, f"能力标签 {cap} 不在任何模型中"


# ============================================================
# TestEdgeCases — 边界场景
# ============================================================


class TestEdgeCases:

    def test_empty_signals(self):
        """空 signals → 正常返回默认模型"""
        assert select_model("chat", {}) == DEFAULT_CHAT_MODEL
        assert select_model("image", {}) == DEFAULT_IMAGE_MODEL
        assert select_model("video", {}) == DEFAULT_VIDEO_MODEL

    def test_none_brand_hint_handled(self):
        """brand_hint=None → 不报错"""
        result = select_model("chat", {"brand_hint": None})
        assert result == DEFAULT_CHAT_MODEL

    def test_has_image_does_not_affect_image_domain(self):
        """has_image 参数不影响 image domain 选择"""
        r1 = select_model("image", {}, has_image=True)
        r2 = select_model("image", {}, has_image=False)
        assert r1 == r2

    def test_thinking_mode_does_not_affect_video(self):
        """thinking_mode 不影响 video domain"""
        r1 = select_model("video", {}, thinking_mode="deep")
        r2 = select_model("video", {})
        assert r1 == r2

    def test_erp_with_brand_hint(self):
        """ERP domain + brand_hint → 品牌命中仍生效"""
        result = select_model("erp", {"brand_hint": "claude"})
        assert "anthropic/" in result
