"""
工具智能筛选器单元测试

覆盖：
- Level 1 同义词扩展
- Level 2 tags 子串匹配
- action 筛选（子串匹配 + 权重）
- action schema 过滤
- 全量降级
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, patch

from config.tool_registry import expand_synonyms, TOOL_REGISTRY
from services.tool_selector import (
    select_tools,
    _score_actions,
    _filter_tool_schema_actions,
    _match_tool_tags,
    select_and_filter_tools,
)


# ============================================================
# Level 1: 同义词扩展
# ============================================================


class TestSynonymExpansion:

    def test_single_keyword(self):
        """单个关键词扩展"""
        result = expand_synonyms("卖了多少")
        assert "销量" in result
        assert "订单" in result

    def test_multiple_keywords(self):
        """多个关键词同时扩展"""
        result = expand_synonyms("退货到哪了")
        assert "售后" in result or "退货" in result
        assert "物流" in result

    def test_no_match(self):
        """无匹配时返回空集"""
        result = expand_synonyms("你好")
        assert len(result) == 0

    def test_colloquial(self):
        """口语表达扩展"""
        result = expand_synonyms("缺货了吗")
        assert "库存" in result

    def test_platform_alias(self):
        """平台简称扩展"""
        result = expand_synonyms("淘宝卖了多少")
        assert "天猫" in result
        assert "奇门" in result


# ============================================================
# Level 2: 工具筛选
# ============================================================


class TestToolSelection:

    def test_stock_query_selects_local_first(self):
        """库存查询优先选中本地工具"""
        entries, _ = select_tools("erp", "库存多少")
        names = [e.name for e in entries if not e.always_include]
        assert "local_stock_query" in names
        # local 应在 remote 前面
        local_idx = names.index("local_stock_query")
        if "erp_product_query" in names:
            remote_idx = names.index("erp_product_query")
            assert local_idx < remote_idx

    def test_aftersale_query(self):
        """售后查询命中售后工具"""
        entries, _ = select_tools("erp", "退货退款情况")
        names = [e.name for e in entries if not e.always_include]
        assert "local_aftersale_query" in names

    def test_always_include_present(self):
        """常驻工具始终包含"""
        entries, _ = select_tools("erp", "库存")
        names = [e.name for e in entries]
        assert "route_to_chat" in names
        assert "ask_user" in names
        assert "code_execute" in names

    def test_synonym_expands_hits(self):
        """同义词扩展增加命中"""
        entries_raw, _ = select_tools("erp", "爆单了")
        names = [e.name for e in entries_raw if not e.always_include]
        # "爆单" → ["订单", "销量", "统计"] → 命中订单/统计相关工具
        assert any("order" in n or "stats" in n or "global" in n for n in names)

    def test_empty_input_returns_all(self):
        """空输入仍返回工具（含常驻）"""
        entries, _ = select_tools("erp", "")
        assert len(entries) > 0


# ============================================================
# Action 筛选
# ============================================================


class TestActionScoring:

    def test_stock_actions_for_stock_query(self):
        """库存查询筛选出 stock 相关 action"""
        actions = _score_actions(
            "erp_product_query", "库存多少",
            {"库存多少", "库存", "统计", "数量"},
        )
        assert actions is not None
        assert "stock_status" in actions

    def test_logistics_actions(self):
        """物流查询筛选出物流 action"""
        actions = _score_actions(
            "erp_trade_query", "物流到哪了",
            {"物流到哪了", "物流", "快递"},
        )
        assert actions is not None
        assert "express_query" in actions

    def test_min_3_actions(self):
        """命中不足时兜底至少 3 个 action"""
        actions = _score_actions(
            "erp_product_query", "xyz完全不匹配",
            {"xyz完全不匹配"},
        )
        assert actions is not None
        assert len(actions) >= 3

    def test_nonexistent_tool_returns_none(self):
        """不存在的工具返回 None"""
        actions = _score_actions("fake_tool", "test", {"test"})
        assert actions is None


# ============================================================
# Schema 过滤
# ============================================================


class TestSchemaFiltering:

    def test_filter_reduces_enum(self):
        """过滤后 action enum 变小"""
        schema = {
            "function": {
                "name": "test_tool",
                "parameters": {
                    "properties": {
                        "action": {
                            "enum": ["a", "b", "c", "d", "e"],
                            "description": "a=描述A, b=描述B, c=描述C, d=描述D, e=描述E",
                        }
                    }
                }
            }
        }
        result = _filter_tool_schema_actions(schema, ["a", "c"])
        new_enum = result["function"]["parameters"]["properties"]["action"]["enum"]
        assert new_enum == ["a", "c"]

    def test_filter_preserves_original(self):
        """过滤不修改原始 schema（深拷贝）"""
        schema = {
            "function": {
                "name": "test",
                "parameters": {
                    "properties": {
                        "action": {
                            "enum": ["a", "b", "c"],
                            "description": "a=A, b=B, c=C",
                        }
                    }
                }
            }
        }
        _filter_tool_schema_actions(schema, ["a"])
        assert len(schema["function"]["parameters"]["properties"]["action"]["enum"]) == 3

    def test_no_action_prop_passthrough(self):
        """无 action 属性的 schema 直接返回"""
        schema = {
            "function": {
                "name": "local_tool",
                "parameters": {"properties": {"code": {"type": "string"}}}
            }
        }
        result = _filter_tool_schema_actions(schema, ["anything"])
        assert result["function"]["name"] == "local_tool"


# ============================================================
# 全流程集成
# ============================================================


class TestSelectAndFilter:

    @pytest.mark.asyncio
    async def test_returns_filtered_tools(self):
        """全流程返回筛选后的工具列表"""
        from config.phase_tools import build_domain_tools
        all_tools = build_domain_tools("erp")
        result = await select_and_filter_tools("erp", "库存多少", all_tools)
        assert len(result) < len(all_tools)
        names = {t["function"]["name"] for t in result}
        assert "local_stock_query" in names

    @pytest.mark.asyncio
    async def test_empty_input_returns_all(self):
        """空输入降级返回全量工具"""
        all_tools = [{"function": {"name": "test"}}]
        result = await select_and_filter_tools("erp", "", all_tools)
        assert result == all_tools

    @pytest.mark.asyncio
    async def test_action_filtered_in_result(self):
        """结果中远程工具的 action enum 被过滤"""
        from config.phase_tools import build_domain_tools
        all_tools = build_domain_tools("erp")
        result = await select_and_filter_tools("erp", "库存多少", all_tools)
        for tool in result:
            if tool["function"]["name"] == "erp_product_query":
                action_enum = (
                    tool["function"]["parameters"]
                    ["properties"]["action"]["enum"]
                )
                # 应该比原始 enum 小
                for orig in all_tools:
                    if orig["function"]["name"] == "erp_product_query":
                        orig_enum = (
                            orig["function"]["parameters"]
                            ["properties"]["action"]["enum"]
                        )
                        assert len(action_enum) < len(orig_enum)
                        break
                break
