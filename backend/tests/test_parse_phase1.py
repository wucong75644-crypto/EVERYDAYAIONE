"""
Phase 1 响应解析单元测试

覆盖：AgentLoop._parse_phase1_response() 所有分支
- 6 种路由工具 → 正确 domain 映射
- 边界场景：空 choices / 无 tool_calls / JSON 解析失败 / 未知工具名
"""

import json

import pytest

from services.agent_loop import AgentLoop


def _make_loop() -> AgentLoop:
    """创建最小 AgentLoop 实例（无 DB 连接）"""
    return AgentLoop(db=None, user_id="u1", conversation_id="c1")


def _make_response(tool_name: str, arguments: dict) -> dict:
    """构造 LLM Phase 1 格式响应"""
    return {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "tc_1",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    },
                }],
            },
        }],
    }


# ============================================================
# 正常路由映射
# ============================================================


class TestPhase1DomainMapping:

    def test_route_chat(self):
        """route_chat → domain=chat, 信号透传"""
        loop = _make_loop()
        resp = _make_response("route_chat", {
            "needs_code": True, "brand_hint": "claude",
        })
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "chat"
        assert signals["needs_code"] is True
        assert signals["brand_hint"] == "claude"

    def test_route_erp(self):
        """route_erp → domain=erp"""
        loop = _make_loop()
        resp = _make_response("route_erp", {"system_prompt": "ERP助手"})
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "erp"
        assert signals["system_prompt"] == "ERP助手"

    def test_route_crawler(self):
        """route_crawler → domain=crawler"""
        loop = _make_loop()
        resp = _make_response("route_crawler", {
            "platform_hint": "xhs", "keywords": "防晒霜",
        })
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "crawler"
        assert signals["platform_hint"] == "xhs"

    def test_route_image(self):
        """route_image → domain=image"""
        loop = _make_loop()
        resp = _make_response("route_image", {
            "prompts": ["a cat"], "needs_hd": True,
        })
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "image"
        assert signals["needs_hd"] is True

    def test_route_video(self):
        """route_video → domain=video"""
        loop = _make_loop()
        resp = _make_response("route_video", {
            "prompt": "sunset timelapse", "needs_pro": True,
        })
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "video"
        assert signals["needs_pro"] is True

    def test_ask_user(self):
        """ask_user → domain=ask_user"""
        loop = _make_loop()
        resp = _make_response("ask_user", {
            "message": "请问你想查什么？", "reason": "need_info",
        })
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "ask_user"
        assert signals["reason"] == "need_info"


# ============================================================
# 边界场景 / 错误恢复
# ============================================================


class TestPhase1EdgeCases:

    def test_empty_choices(self):
        """空 choices → 兜底 chat"""
        loop = _make_loop()
        domain, signals = loop._parse_phase1_response({"choices": []})
        assert domain == "chat"
        assert signals == {}

    def test_no_choices_key(self):
        """无 choices 键 → 兜底 chat"""
        loop = _make_loop()
        domain, signals = loop._parse_phase1_response({})
        assert domain == "chat"
        assert signals == {}

    def test_no_tool_calls(self):
        """有 message 无 tool_calls → 兜底 chat"""
        loop = _make_loop()
        resp = {"choices": [{"message": {"content": "直接回复"}}]}
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "chat"
        assert signals == {}

    def test_invalid_json_arguments(self):
        """arguments JSON 格式错误 → 空 signals"""
        loop = _make_loop()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "tc_1",
                        "type": "function",
                        "function": {
                            "name": "route_chat",
                            "arguments": "{invalid json",
                        },
                    }],
                },
            }],
        }
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "chat"
        assert signals == {}

    def test_unknown_tool_name(self):
        """未知工具名 → 兜底 chat"""
        loop = _make_loop()
        resp = _make_response("route_unknown", {"key": "val"})
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "chat"

    def test_multiple_tool_calls_takes_first(self):
        """多个 tool_calls → 只取第一个"""
        loop = _make_loop()
        resp = {
            "choices": [{
                "message": {
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "route_erp",
                                "arguments": json.dumps({"a": 1}),
                            },
                        },
                        {
                            "id": "tc_2",
                            "type": "function",
                            "function": {
                                "name": "route_image",
                                "arguments": json.dumps({"b": 2}),
                            },
                        },
                    ],
                },
            }],
        }
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "erp"
        assert signals == {"a": 1}

    def test_empty_arguments(self):
        """空参数字符串 → 空 signals"""
        loop = _make_loop()
        resp = _make_response("route_chat", {})
        domain, signals = loop._parse_phase1_response(resp)
        assert domain == "chat"
        assert signals == {}
