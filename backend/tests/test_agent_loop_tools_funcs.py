"""agent_loop_tools 模块级函数测试 — try_expand_tools / inject_phase1_model / get_action_enum"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import json
import pytest
from services.agent_loop_tools import get_action_enum, inject_phase1_model, try_expand_tools


# ============================================================
# get_action_enum
# ============================================================

class TestGetActionEnum:

    def test_extracts_enum(self):
        schema = {
            "function": {
                "name": "erp_trade_query",
                "parameters": {
                    "properties": {
                        "action": {"enum": ["order_list", "order_detail"]}
                    }
                }
            }
        }
        assert get_action_enum(schema) == ["order_list", "order_detail"]

    def test_no_action_returns_empty(self):
        schema = {"function": {"parameters": {"properties": {}}}}
        assert get_action_enum(schema) == []

    def test_empty_schema_returns_empty(self):
        assert get_action_enum({}) == []

    def test_no_enum_returns_empty(self):
        schema = {
            "function": {
                "parameters": {
                    "properties": {
                        "action": {"type": "string"}  # 无 enum
                    }
                }
            }
        }
        assert get_action_enum(schema) == []


# ============================================================
# inject_phase1_model
# ============================================================

class TestInjectPhase1Model:

    def test_injects_when_model_missing(self):
        holder = {"decision": {"arguments": {"system_prompt": "hi"}}}
        inject_phase1_model(holder, "gemini-3-pro")
        assert holder["decision"]["arguments"]["model"] == "gemini-3-pro"

    def test_no_override_when_model_exists(self):
        holder = {"decision": {"arguments": {"model": "gpt-4o"}}}
        inject_phase1_model(holder, "gemini-3-pro")
        assert holder["decision"]["arguments"]["model"] == "gpt-4o"

    def test_no_decision_noop(self):
        holder = {}
        inject_phase1_model(holder, "gemini-3-pro")
        assert "decision" not in holder

    def test_empty_model_gets_injected(self):
        holder = {"decision": {"arguments": {"model": ""}}}
        inject_phase1_model(holder, "gemini-3-pro")
        assert holder["decision"]["arguments"]["model"] == "gemini-3-pro"


# ============================================================
# try_expand_tools
# ============================================================

def _make_tool(name: str, actions: list = None) -> dict:
    """构造测试用工具 schema"""
    schema = {"function": {"name": name, "parameters": {"properties": {}}}}
    if actions is not None:
        schema["function"]["parameters"]["properties"]["action"] = {"enum": actions}
    return schema


def _make_tc(name: str, action: str = None) -> dict:
    """构造测试用 tool_call"""
    args = {"action": action} if action else {}
    return {"function": {"name": name, "arguments": json.dumps(args)}}


class TestTryExpandTools:

    def test_no_expansion_needed(self):
        t1 = _make_tool("erp_trade_query", ["order_list"])
        tc = _make_tc("erp_trade_query", "order_list")
        state = {"tool_expanded": False, "action_expanded": False}
        result = try_expand_tools([tc], [t1], [t1], state)
        assert result is None

    def test_tool_expansion_adds_missing_tool(self):
        t1 = _make_tool("tool_a")
        t2 = _make_tool("tool_b")
        tc = _make_tc("tool_b")  # 不在 current 中
        state = {"tool_expanded": False, "action_expanded": False}
        result = try_expand_tools([tc], [t1], [t1, t2], state)
        assert result is not None
        names = {t["function"]["name"] for t in result}
        assert "tool_b" in names
        assert state["tool_expanded"] is True

    def test_tool_expansion_only_once(self):
        t1 = _make_tool("tool_a")
        t2 = _make_tool("tool_b")
        t3 = _make_tool("tool_c")
        tc = _make_tc("tool_c")
        state = {"tool_expanded": True, "action_expanded": False}  # 已扩充过
        result = try_expand_tools([tc], [t1], [t1, t2, t3], state)
        assert result is None

    def test_action_expansion(self):
        t1_short = _make_tool("erp_trade_query", ["order_list"])
        t1_full = _make_tool("erp_trade_query", ["order_list", "order_detail", "express_query"])
        tc = _make_tc("erp_trade_query", "express_query")  # 不在 short enum 中
        state = {"tool_expanded": False, "action_expanded": False}
        result = try_expand_tools([tc], [t1_short], [t1_full], state)
        assert result is not None
        expanded_enum = get_action_enum(result[0])
        assert "express_query" in expanded_enum
        assert state["action_expanded"] is True

    def test_action_expansion_only_once(self):
        t1_short = _make_tool("erp_trade_query", ["order_list"])
        t1_full = _make_tool("erp_trade_query", ["order_list", "express_query"])
        tc = _make_tc("erp_trade_query", "express_query")
        state = {"tool_expanded": False, "action_expanded": True}  # 已扩充过
        result = try_expand_tools([tc], [t1_short], [t1_full], state)
        assert result is None

    def test_unknown_tool_not_in_all_ignored(self):
        t1 = _make_tool("tool_a")
        tc = _make_tc("tool_x")  # 不在 current 也不在 all
        state = {"tool_expanded": False, "action_expanded": False}
        result = try_expand_tools([tc], [t1], [t1], state)
        assert result is None

    def test_action_not_in_full_enum_ignored(self):
        t1 = _make_tool("erp_trade_query", ["order_list"])
        tc = _make_tc("erp_trade_query", "nonexistent_action")
        state = {"tool_expanded": False, "action_expanded": False}
        result = try_expand_tools([tc], [t1], [t1], state)
        assert result is None
