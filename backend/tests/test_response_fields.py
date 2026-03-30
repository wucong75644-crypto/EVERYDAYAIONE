"""
返回字段注册表单元测试

覆盖：get_response_fields 查找逻辑、注册表完整性、字段数据结构
"""

from services.kuaimai.formatters import (
    _ACTION_RESPONSE_FIELDS,
    _RESPONSE_FIELDS_REGISTRY,
    get_response_fields,
)
from services.kuaimai.registry import TOOL_REGISTRIES


class TestGetResponseFields:
    """get_response_fields() 查找逻辑"""

    def test_lookup_by_formatter_name(self):
        """按 formatter 名精确查找"""
        fields = get_response_fields("format_order_list")
        assert fields is not None
        assert "main" in fields
        assert "tid" in fields["main"]

    def test_lookup_by_action_fallback(self):
        """generic_list 按 tool:action 兜底查找"""
        fields = get_response_fields(
            "format_generic_list", "erp_product_query", "cat_list",
        )
        assert fields is not None
        assert "cid" in fields["main"]

    def test_generic_without_action_returns_none(self):
        """generic_list 不传 action 返回 None"""
        fields = get_response_fields("format_generic_list")
        assert fields is None

    def test_unknown_formatter_returns_none(self):
        """未知 formatter 返回 None"""
        fields = get_response_fields("nonexistent_formatter")
        assert fields is None

    def test_formatter_priority_over_action(self):
        """专用 formatter 优先于 action 兜底"""
        fields = get_response_fields(
            "format_order_list", "erp_trade_query", "order_list",
        )
        # 应该返回 formatter 级别的结果（有 items）
        assert "items" in fields


class TestResponseFieldsDataStructure:
    """返回字段数据结构正确性"""

    def test_main_is_dict_of_str(self):
        """main 字段是 {str: str} 结构"""
        for name, fields in _RESPONSE_FIELDS_REGISTRY.items():
            main = fields.get("main", {})
            assert isinstance(main, dict), f"{name}: main not dict"
            for k, v in main.items():
                assert isinstance(k, str), f"{name}: key {k} not str"
                assert isinstance(v, str), f"{name}: val {v} not str"

    def test_items_has_items_key(self):
        """有 items 的条目必须有 items_key"""
        for name, fields in _RESPONSE_FIELDS_REGISTRY.items():
            if "items" in fields:
                assert "items_key" in fields, (
                    f"{name}: has items but no items_key"
                )

    def test_action_fields_structure(self):
        """action 级别字段也是 {str: str} 结构"""
        for key, fields in _ACTION_RESPONSE_FIELDS.items():
            main = fields.get("main", {})
            assert isinstance(main, dict), f"{key}: main not dict"
            for k, v in main.items():
                assert isinstance(k, str), f"{key}: key {k} not str"
                assert isinstance(v, str), f"{key}: val {v} not str"


class TestResponseFieldsCompleteness:
    """注册完整性：每个非写操作、非 generic_detail 的 action 都有返回字段"""

    def test_all_specific_formatters_registered(self):
        """所有专用 formatter 都在 RESPONSE_FIELDS 中"""
        # 收集 registry 中所有非 generic 的 formatter 名
        specific_formatters = set()
        for tool_name, registry in TOOL_REGISTRIES.items():
            if not isinstance(registry, dict):
                continue
            for action_name, entry in registry.items():
                if hasattr(entry, "formatter"):
                    fmt = entry.formatter
                    if fmt not in ("format_generic_list", "format_generic_detail"):
                        specific_formatters.add(fmt)

        missing = specific_formatters - set(_RESPONSE_FIELDS_REGISTRY.keys())
        assert not missing, f"Formatters missing from RESPONSE_FIELDS: {missing}"

    def test_generic_list_actions_covered(self):
        """所有 generic_list 读操作 action 都在 ACTION_RESPONSE_FIELDS 中"""
        uncovered = []
        for tool_name, registry in TOOL_REGISTRIES.items():
            if not isinstance(registry, dict):
                continue
            for action_name, entry in registry.items():
                if not hasattr(entry, "formatter"):
                    continue
                if entry.formatter != "format_generic_list":
                    continue
                if getattr(entry, "is_write", False):
                    continue
                key = f"{tool_name}:{action_name}"
                if key not in _ACTION_RESPONSE_FIELDS:
                    uncovered.append(key)

        assert not uncovered, (
            f"generic_list actions missing from ACTION_RESPONSE_FIELDS: {uncovered}"
        )

    def test_registry_count_sanity(self):
        """注册表数量合理性检查"""
        assert len(_RESPONSE_FIELDS_REGISTRY) >= 30, (
            f"Expected >=30 specific formatters, got {len(_RESPONSE_FIELDS_REGISTRY)}"
        )
        assert len(_ACTION_RESPONSE_FIELDS) >= 10, (
            f"Expected >=10 action overrides, got {len(_ACTION_RESPONSE_FIELDS)}"
        )
