"""tool_args_validator 单元测试

覆盖场景：
1. 正常参数 → 原样通过
2. 幻觉参数 → 静默丢弃
3. 必填缺失 → 返回错误信息
4. 幻觉 + 必填缺失 → 丢弃幻觉 + 报必填
5. 空 args → 无必填时通过
6. 空 args → 有必填时报错
7. 工具不在 selected_tools 中 → 跳过校验
8. schema 无 properties → 跳过过滤
"""
import pytest

from services.agent.tool_args_validator import validate_tool_args


def _make_tool(
    name: str,
    properties: dict,
    required: list | None = None,
) -> dict:
    """构建 OpenAI function calling 格式的工具定义"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


TOOLS = [
    _make_tool(
        "local_db_export",
        {
            "doc_type": {
                "type": "string",
                "enum": ["order", "purchase"],
                "description": "数据类型",
            },
            "columns": {"type": "string", "description": "导出字段"},
            "days": {"type": "integer", "description": "最近N天"},
            "shop_name": {"type": "string", "description": "店铺名"},
        },
        required=["doc_type"],
    ),
    _make_tool(
        "local_shop_list",
        {
            "platform": {"type": "string", "description": "平台过滤"},
        },
        required=[],
    ),
]


class TestValidateToolArgs:
    """validate_tool_args 核心场景"""

    def test_normal_args_pass_through(self):
        """正常参数原样通过"""
        args = {"doc_type": "order", "days": 3}
        cleaned, err = validate_tool_args("local_db_export", args, TOOLS)
        assert err is None
        assert cleaned == {"doc_type": "order", "days": 3}

    def test_hallucinated_params_stripped(self):
        """幻觉参数被丢弃，合法参数保留"""
        args = {"doc_type": "order", "date": "2026-04-13", "foo": "bar"}
        cleaned, err = validate_tool_args("local_db_export", args, TOOLS)
        assert err is None
        assert cleaned == {"doc_type": "order"}
        assert "date" not in cleaned
        assert "foo" not in cleaned

    def test_missing_required_returns_error(self):
        """必填参数缺失 → 返回错误信息"""
        args = {"days": 3}
        cleaned, err = validate_tool_args("local_db_export", args, TOOLS)
        assert err is not None
        assert "doc_type" in err
        assert "必填" in err

    def test_hallucinated_and_missing_required(self):
        """幻觉参数被丢弃 + 必填缺失同时触发"""
        args = {"date": "2026-04-13"}
        cleaned, err = validate_tool_args("local_db_export", args, TOOLS)
        assert err is not None
        assert "doc_type" in err
        assert "date" not in cleaned

    def test_empty_args_no_required(self):
        """空参数 + 无必填 → 通过"""
        cleaned, err = validate_tool_args("local_shop_list", {}, TOOLS)
        assert err is None
        assert cleaned == {}

    def test_empty_args_with_required(self):
        """空参数 + 有必填 → 报错"""
        cleaned, err = validate_tool_args("local_db_export", {}, TOOLS)
        assert err is not None
        assert "doc_type" in err

    def test_unknown_tool_skips_validation(self):
        """工具不在 selected_tools → 跳过校验，原样返回"""
        args = {"whatever": 123, "hallucination": True}
        cleaned, err = validate_tool_args("unknown_tool", args, TOOLS)
        assert err is None
        assert cleaned == args

    def test_error_msg_includes_enum_hints(self):
        """错误信息包含 enum 可选值提示"""
        args = {}
        _, err = validate_tool_args("local_db_export", args, TOOLS)
        assert err is not None
        assert "order" in err
        assert "purchase" in err

    def test_schema_without_properties(self):
        """schema 无 properties 字段 → 不过滤"""
        tools = [{
            "type": "function",
            "function": {
                "name": "bare_tool",
                "parameters": {"type": "object"},
            },
        }]
        args = {"any_param": "value"}
        cleaned, err = validate_tool_args("bare_tool", args, tools)
        assert err is None
        assert cleaned == {}  # properties 为空 → 所有 key 都不在合法集合中

    def test_only_hallucinated_params_all_stripped(self):
        """全部是幻觉参数但无必填 → 通过，args 为空"""
        args = {"fake1": "a", "fake2": "b"}
        cleaned, err = validate_tool_args("local_shop_list", args, TOOLS)
        assert err is None
        assert cleaned == {}
