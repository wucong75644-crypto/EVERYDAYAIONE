"""
Agent Result Builder + 文本工具 单元测试

覆盖：build_chat_result / build_final_result / build_graceful_timeout、
      _extract_text、_build_routing_confirmation、_extract_generation_prompt
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import json
from unittest.mock import MagicMock

import pytest

from schemas.message import GenerationType, TextPart, ImagePart
from services.agent_types import AgentResult
from services.agent_loop import AgentLoop
from services.agent_result_builder import (
    build_chat_result,
    build_final_result,
    build_graceful_timeout,
)


# ============================================================
# Helpers
# ============================================================

def _make_loop() -> AgentLoop:
    """创建 AgentLoop 实例（mock db）"""
    return AgentLoop(db=MagicMock(), user_id="u1", conversation_id="c1")


# ============================================================
# TestBuildResults
# ============================================================


class TestBuildResults:
    """测试 agent_result_builder 的构建函数"""

    def test_chat_result_with_context(self):
        result = build_chat_result(
            "回复", ["ctx1", "ctx2"], turns=2, tokens=500,
        )
        assert result.generation_type == GenerationType.CHAT
        assert result.search_context == "ctx1\nctx2"
        assert result.direct_reply == "回复"

    def test_chat_result_no_context(self):
        result = build_chat_result(
            "", [], turns=1, tokens=100,
        )
        assert result.search_context is None
        assert result.direct_reply is None  # empty string → None

    def test_final_result_route_to_chat(self):
        """route_to_chat → CHAT result"""
        holder = {
            "decision": {
                "tool_name": "route_to_chat",
                "arguments": {
                    "system_prompt": "翻译",
                    "model": "gpt-4",
                    "needs_google_search": True,
                },
            },
        }
        result = build_final_result(holder, ["ctx"], turns=1, tokens=100)
        assert result.generation_type == GenerationType.CHAT
        assert result.system_prompt == "翻译"
        assert result.model == "gpt-4"
        assert result.search_context == "ctx"
        assert result.tool_params["_needs_google_search"] is True

    def test_final_result_route_to_image_single(self):
        """route_to_image 单图 → IMAGE result"""
        holder = {
            "decision": {
                "tool_name": "route_to_image",
                "arguments": {
                    "prompts": [{"prompt": "cat", "aspect_ratio": "1:1"}],
                    "model": "flux",
                },
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.IMAGE
        assert result.tool_params["prompt"] == "cat"
        assert result.tool_params["aspect_ratio"] == "1:1"
        assert result.render_hints is not None

    def test_final_result_route_to_image_batch(self):
        """route_to_image 批量 → batch_prompts"""
        prompts = [{"prompt": "a"}, {"prompt": "b"}]
        holder = {
            "decision": {
                "tool_name": "route_to_image",
                "arguments": {"prompts": prompts, "model": "flux"},
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.IMAGE
        assert result.batch_prompts == prompts

    def test_final_result_route_to_video(self):
        """route_to_video → VIDEO result"""
        holder = {
            "decision": {
                "tool_name": "route_to_video",
                "arguments": {"prompt": "waves", "model": "vidu"},
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.generation_type == GenerationType.VIDEO
        assert result.render_hints is not None

    def test_final_result_ask_user(self):
        """ask_user → direct_reply"""
        holder = {
            "decision": {
                "tool_name": "ask_user",
                "arguments": {"message": "需要更多信息", "reason": "need_info"},
            },
        }
        result = build_final_result(holder, ["ctx"], turns=1, tokens=100)
        assert result.direct_reply == "需要更多信息"
        assert result.tool_params["_ask_reason"] == "need_info"
        assert result.search_context == "ctx"

    def test_final_result_no_decision_fallback(self):
        """无路由决策→fallback chat"""
        result = build_final_result({}, ["ctx"], turns=1, tokens=100)
        assert result.generation_type == GenerationType.CHAT

    def test_final_result_unknown_tool_fallback(self):
        """未知路由工具名→fallback chat"""
        holder = {
            "decision": {
                "tool_name": "unknown_tool",
                "arguments": {"foo": "bar"},
            },
        }
        result = build_final_result(holder, ["ctx"], turns=1, tokens=100)
        assert result.generation_type == GenerationType.CHAT

    def test_final_result_image_missing_aspect_ratio(self):
        """route_to_image 单图无 aspect_ratio→默认 1:1"""
        holder = {
            "decision": {
                "tool_name": "route_to_image",
                "arguments": {
                    "prompts": [{"prompt": "a cat"}],
                    "model": "flux",
                },
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.tool_params["aspect_ratio"] == "1:1"

    def test_final_result_chat_no_google_search(self):
        """route_to_chat 未指定 needs_google_search→默认 False"""
        holder = {
            "decision": {
                "tool_name": "route_to_chat",
                "arguments": {
                    "system_prompt": "助手",
                    "model": "gemini-3-pro",
                },
            },
        }
        result = build_final_result(holder, [], turns=1, tokens=100)
        assert result.tool_params["_needs_google_search"] is False

    def test_graceful_timeout_with_context(self):
        """graceful timeout + context→chat with context"""
        result = build_graceful_timeout(["搜索结果"], turns=3, tokens=3000)
        assert result.generation_type == GenerationType.CHAT
        assert result.search_context == "搜索结果"

    def test_graceful_timeout_empty(self):
        """graceful timeout + 全空→DEFAULT_CHAT_MODEL"""
        result = build_graceful_timeout([], turns=3, tokens=3000)
        assert result.generation_type == GenerationType.CHAT
        assert result.model != ""  # 应该有默认模型


# ============================================================
# TestExtractText
# ============================================================


class TestExtractText:

    def test_single_text(self):
        loop = _make_loop()
        assert loop._extract_text([TextPart(text="hello")]) == "hello"

    def test_multiple_text(self):
        loop = _make_loop()
        content = [TextPart(text="hello"), TextPart(text="world")]
        assert loop._extract_text(content) == "hello world"

    def test_mixed_with_image(self):
        loop = _make_loop()
        content = [TextPart(text="分析"), ImagePart(url="http://img.jpg")]
        assert loop._extract_text(content) == "分析"

    def test_empty(self):
        loop = _make_loop()
        assert loop._extract_text([]) == ""


# ============================================================
# TestBuildRoutingConfirmation
# ============================================================


class TestBuildRoutingConfirmation:

    def test_route_to_chat(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation(
            "route_to_chat", {"model": "gpt-4"},
        )
        assert "gpt-4" in msg

    def test_route_to_image(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation(
            "route_to_image", {"prompts": [{"prompt": "a"}, {"prompt": "b"}]},
        )
        assert "2 张图片" in msg

    def test_route_to_video(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation("route_to_video", {})
        assert "视频" in msg

    def test_ask_user(self):
        loop = _make_loop()
        msg = loop._build_routing_confirmation("ask_user", {})
        assert "询问" in msg

    def test_unknown_tool(self):
        """未知路由工具→返回默认确认"""
        loop = _make_loop()
        msg = loop._build_routing_confirmation("unknown_tool", {})
        assert msg == "已确认"


# ============================================================
# TestExtractGenerationPrompt — 原始生成提示词提取
# ============================================================


class TestExtractGenerationPrompt:

    def test_dict_with_prompt(self):
        """generation_params 是 dict 且包含 prompt→正常提取"""
        msg = {"generation_params": {"prompt": "AI robot avatar", "model": "flux"}}
        result = AgentLoop._extract_generation_prompt(msg)
        assert result == "AI robot avatar"

    def test_json_string_with_prompt(self):
        """generation_params 是 JSON 字符串→解析后提取"""
        msg = {"generation_params": json.dumps({"prompt": "cute cat", "model": "flux"})}
        result = AgentLoop._extract_generation_prompt(msg)
        assert result == "cute cat"

    def test_no_generation_params(self):
        """无 generation_params→None"""
        assert AgentLoop._extract_generation_prompt({}) is None

    def test_none_generation_params(self):
        """generation_params=None→None"""
        assert AgentLoop._extract_generation_prompt({"generation_params": None}) is None

    def test_empty_prompt(self):
        """prompt 为空字符串→None"""
        msg = {"generation_params": {"prompt": "", "model": "flux"}}
        assert AgentLoop._extract_generation_prompt(msg) is None

    def test_whitespace_prompt(self):
        """prompt 为纯空白→None"""
        msg = {"generation_params": {"prompt": "   ", "model": "flux"}}
        assert AgentLoop._extract_generation_prompt(msg) is None

    def test_no_prompt_key(self):
        """generation_params 无 prompt 字段→None"""
        msg = {"generation_params": {"model": "flux"}}
        assert AgentLoop._extract_generation_prompt(msg) is None

    def test_invalid_json_string(self):
        """generation_params 是非法 JSON 字符串→None"""
        msg = {"generation_params": "not-json"}
        assert AgentLoop._extract_generation_prompt(msg) is None

    def test_strips_whitespace(self):
        """prompt 前后空白被去除"""
        msg = {"generation_params": {"prompt": "  hello world  "}}
        assert AgentLoop._extract_generation_prompt(msg) == "hello world"
