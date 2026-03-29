"""
Phase 1/Phase 2 工具定义单元测试

覆盖：Phase 1 工具结构、PHASE1_TOOL_TO_DOMAIN 映射、
      Phase 2 domain 工具加载、domain prompt 构建、
      _slice_text_only 辅助函数、向后兼容导出
"""

import pytest

from config.phase_tools import (
    BASE_AGENT_PROMPT,
    PHASE1_SYSTEM_PROMPT,
    PHASE1_TOOL_TO_DOMAIN,
    PHASE1_TOOLS,
    build_domain_prompt,
    build_domain_tools,
    build_phase1_tools,
)
from services.agent_context import AgentContextMixin


# ============================================================
# TestPhase1Tools — Phase 1 工具结构
# ============================================================


class TestPhase1Tools:

    def test_phase1_has_seven_tools(self):
        """Phase 1 包含 7 个路由工具"""
        tools = build_phase1_tools()
        assert len(tools) == 7

    def test_phase1_tool_names(self):
        """Phase 1 工具名覆盖所有 domain"""
        names = {t["function"]["name"] for t in PHASE1_TOOLS}
        expected = {
            "route_chat", "route_erp", "route_crawler",
            "route_computer", "route_image", "route_video", "ask_user",
        }
        assert names == expected

    def test_phase1_no_model_enum(self):
        """Phase 1 工具无 model 参数（由规则选模型）"""
        for tool in PHASE1_TOOLS:
            props = tool["function"]["parameters"].get("properties", {})
            assert "model" not in props, (
                f"{tool['function']['name']} should not have model param"
            )

    def test_route_chat_has_brand_hint(self):
        """route_chat 包含 brand_hint 信号"""
        chat = next(
            t for t in PHASE1_TOOLS
            if t["function"]["name"] == "route_chat"
        )
        props = chat["function"]["parameters"]["properties"]
        assert "brand_hint" in props

    def test_route_chat_has_capability_signals(self):
        """route_chat 包含能力信号（needs_code/reasoning/search）"""
        chat = next(
            t for t in PHASE1_TOOLS
            if t["function"]["name"] == "route_chat"
        )
        props = chat["function"]["parameters"]["properties"]
        for key in ("needs_code", "needs_reasoning", "needs_search"):
            assert key in props, f"route_chat missing {key}"

    def test_route_chat_has_system_prompt(self):
        """route_chat 包含 system_prompt"""
        chat = next(
            t for t in PHASE1_TOOLS
            if t["function"]["name"] == "route_chat"
        )
        props = chat["function"]["parameters"]["properties"]
        assert "system_prompt" in props

    def test_route_image_has_domain_signals(self):
        """route_image 包含 image domain 信号"""
        img = next(
            t for t in PHASE1_TOOLS
            if t["function"]["name"] == "route_image"
        )
        props = img["function"]["parameters"]["properties"]
        for key in ("prompts", "aspect_ratio", "needs_edit", "needs_hd"):
            assert key in props, f"route_image missing {key}"

    def test_route_video_has_domain_signals(self):
        """route_video 包含 video domain 信号"""
        vid = next(
            t for t in PHASE1_TOOLS
            if t["function"]["name"] == "route_video"
        )
        props = vid["function"]["parameters"]["properties"]
        assert "prompt" in props
        assert "needs_pro" in props

    def test_route_crawler_has_signals(self):
        """route_crawler 包含 platform_hint 和 keywords"""
        crawler = next(
            t for t in PHASE1_TOOLS
            if t["function"]["name"] == "route_crawler"
        )
        props = crawler["function"]["parameters"]["properties"]
        assert "platform_hint" in props
        assert "keywords" in props

    def test_ask_user_has_reason_enum(self):
        """ask_user 的 reason 是 enum"""
        ask = next(
            t for t in PHASE1_TOOLS
            if t["function"]["name"] == "ask_user"
        )
        reason = ask["function"]["parameters"]["properties"]["reason"]
        assert "enum" in reason
        assert "need_info" in reason["enum"]
        assert "out_of_scope" in reason["enum"]

    def test_all_tools_have_function_type(self):
        """所有工具都是 function 类型"""
        for tool in PHASE1_TOOLS:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]


# ============================================================
# TestPhase1Prompt — Phase 1 系统提示词
# ============================================================


class TestPhase1Prompt:

    def test_prompt_not_empty(self):
        """提示词非空"""
        assert len(PHASE1_SYSTEM_PROMPT) > 0

    def test_prompt_mentions_all_domains(self):
        """提示词提到所有 domain 路由"""
        for route in (
            "route_chat", "route_erp", "route_crawler",
            "route_image", "route_video", "ask_user",
        ):
            assert route in PHASE1_SYSTEM_PROMPT

    def test_prompt_under_1000_chars(self):
        """提示词控制在 1000 字符以内（轻量）"""
        assert len(PHASE1_SYSTEM_PROMPT) < 1000

    def test_prompt_mentions_regeneration(self):
        """提示词包含重新生成规则"""
        assert "重新生成" in PHASE1_SYSTEM_PROMPT


# ============================================================
# TestPhase1ToolToDomain — 映射完整性
# ============================================================


class TestPhase1ToolToDomain:

    def test_all_phase1_tools_mapped(self):
        """每个 Phase 1 工具名都在映射中"""
        tool_names = {t["function"]["name"] for t in PHASE1_TOOLS}
        for name in tool_names:
            assert name in PHASE1_TOOL_TO_DOMAIN

    def test_mapping_values(self):
        """映射值覆盖所有 domain"""
        values = set(PHASE1_TOOL_TO_DOMAIN.values())
        expected = {"chat", "erp", "crawler", "computer", "image", "video", "ask_user"}
        assert values == expected


# ============================================================
# TestPhase2DomainTools — Phase 2 工具加载
# ============================================================


class TestPhase2DomainTools:

    def test_erp_tools_loaded(self):
        """ERP domain 加载完整工具链"""
        tools = build_domain_tools("erp")
        names = {t["function"]["name"] for t in tools}
        # 必须包含 ERP 工具 + route_to_chat + ask_user
        assert "route_to_chat" in names
        assert "ask_user" in names
        assert "local_product_identify" in names
        assert len(tools) >= 10

    def test_crawler_tools_loaded(self):
        """Crawler domain 加载 social_crawler + 出口"""
        tools = build_domain_tools("crawler")
        names = {t["function"]["name"] for t in tools}
        assert "social_crawler" in names
        assert "route_to_chat" in names
        assert "ask_user" in names

    def test_chat_domain_empty(self):
        """Chat domain 不需要 Phase 2 工具"""
        assert build_domain_tools("chat") == []

    def test_image_domain_empty(self):
        """Image domain 不需要 Phase 2 工具"""
        assert build_domain_tools("image") == []

    def test_video_domain_empty(self):
        """Video domain 不需要 Phase 2 工具"""
        assert build_domain_tools("video") == []

    def test_unknown_domain_empty(self):
        """未知 domain 返回空列表"""
        assert build_domain_tools("unknown") == []

    def test_phase2_route_to_chat_no_model(self):
        """Phase 2 route_to_chat 无 model 参数"""
        tools = build_domain_tools("erp")
        chat_tool = next(
            t for t in tools
            if t["function"]["name"] == "route_to_chat"
        )
        props = chat_tool["function"]["parameters"]["properties"]
        assert "model" not in props
        assert "system_prompt" in props

    def test_erp_has_code_execute(self):
        """ERP domain 包含代码执行工具"""
        tools = build_domain_tools("erp")
        names = {t["function"]["name"] for t in tools}
        assert "code_execute" in names

    def test_crawler_no_code_execute(self):
        """Crawler domain 不包含代码执行工具"""
        tools = build_domain_tools("crawler")
        names = {t["function"]["name"] for t in tools}
        assert "code_execute" not in names


# ============================================================
# TestPhase2DomainPrompt — Phase 2 提示词
# ============================================================


class TestPhase2DomainPrompt:

    def test_erp_prompt_contains_base(self):
        """ERP prompt 包含 BASE_AGENT_PROMPT"""
        prompt = build_domain_prompt("erp")
        assert BASE_AGENT_PROMPT in prompt

    def test_erp_prompt_contains_erp_rules(self):
        """ERP prompt 包含 ERP 路由规则"""
        prompt = build_domain_prompt("erp")
        assert len(prompt) > len(BASE_AGENT_PROMPT)

    def test_crawler_prompt_contains_base(self):
        """Crawler prompt 包含 BASE_AGENT_PROMPT"""
        prompt = build_domain_prompt("crawler")
        assert BASE_AGENT_PROMPT in prompt

    def test_chat_prompt_empty(self):
        """Chat domain 无 Phase 2 提示词"""
        assert build_domain_prompt("chat") == ""

    def test_image_prompt_empty(self):
        """Image domain 无 Phase 2 提示词"""
        assert build_domain_prompt("image") == ""

    def test_video_prompt_empty(self):
        assert build_domain_prompt("video") == ""

    def test_unknown_prompt_empty(self):
        assert build_domain_prompt("unknown") == ""


# ============================================================
# TestSliceTextOnly — 历史切片
# ============================================================


class TestSliceTextOnly:

    @staticmethod
    def _slice(history, limit=3):
        return AgentContextMixin._slice_text_only(history, limit=limit)

    def test_none_input(self):
        """None → None"""
        assert self._slice(None) is None

    def test_empty_list(self):
        """空列表 → None"""
        assert self._slice([]) is None

    def test_slices_last_n(self):
        """切最后 N 条"""
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": f"msg{i}"},
            ]}
            for i in range(5)
        ]
        result = self._slice(history, limit=3)
        assert len(result) == 3
        assert result[0]["content"][0]["text"] == "msg2"

    def test_strips_image_blocks(self):
        """剥离 image_url blocks"""
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "hello"},
                {"type": "image_url", "image_url": {"url": "http://x"}},
            ]},
        ]
        result = self._slice(history, limit=3)
        assert len(result) == 1
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["type"] == "text"

    def test_filters_image_only_messages(self):
        """纯图片消息被过滤"""
        history = [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "http://x"}},
            ]},
        ]
        assert self._slice(history) is None

    def test_preserves_role(self):
        """保留消息 role"""
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "q"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "a"},
            ]},
        ]
        result = self._slice(history)
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_limit_exceeds_length(self):
        """limit 大于历史长度 → 返回全部"""
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "msg"},
            ]},
        ]
        result = self._slice(history, limit=10)
        assert len(result) == 1

    def test_mixed_messages(self):
        """混合消息：有文本的保留，纯图片的过滤"""
        history = [
            {"role": "user", "content": [
                {"type": "text", "text": "text msg"},
            ]},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "http://x"}},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "reply"},
            ]},
        ]
        result = self._slice(history, limit=3)
        # 纯图片消息被过滤，剩 2 条
        assert len(result) == 2
