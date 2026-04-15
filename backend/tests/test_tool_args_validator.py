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
9. 类型校验：string→object 成功/失败
10. 类型校验：string→int 成功/失败, float→int
11. 类型校验：string→bool 成功/失败
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
    # 用于类型校验测试：覆盖 object / integer / boolean
    _make_tool(
        "erp_product_query",
        {
            "action": {"type": "string", "description": "操作名"},
            "params": {"type": "object", "description": "操作参数"},
            "page": {"type": "integer", "description": "页码"},
            "page_size": {"type": "integer", "description": "每页条数"},
        },
        required=["action"],
    ),
    _make_tool(
        "local_stock_query",
        {
            "product_code": {"type": "string", "description": "商品编码"},
            "low_stock": {"type": "boolean", "description": "低库存过滤"},
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

    def test_missing_required_guides_to_ask_user(self):
        """必填缺失错误信息包含 ask_user 引导"""
        args = {"days": 3}
        _, err = validate_tool_args("local_db_export", args, TOOLS)
        assert err is not None
        assert "ask_user" in err
        assert "禁止自行猜测" in err

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


class TestTypeCoercionObject:
    """object 类型校验：string→dict"""

    def test_string_to_dict_success(self):
        """JSON 字符串自动反序列化为 dict"""
        args = {"action": "product_list", "params": '{"keyword": "T恤"}'}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is None
        assert cleaned["params"] == {"keyword": "T恤"}

    def test_string_to_dict_nested(self):
        """嵌套 JSON 字符串也能正确还原"""
        args = {"action": "product_list", "params": '{"keyword": "鞋", "start_date": "2026-04-01"}'}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is None
        assert cleaned["params"]["keyword"] == "鞋"
        assert cleaned["params"]["start_date"] == "2026-04-01"

    def test_string_not_json_returns_error(self):
        """非 JSON 字符串 → 返回错误"""
        args = {"action": "product_list", "params": "keyword=T恤"}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is not None
        assert "object" in err

    def test_string_json_array_returns_error(self):
        """JSON 数组字符串（不是 dict）→ 返回错误"""
        args = {"action": "product_list", "params": '[1, 2, 3]'}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is not None
        assert "object" in err

    def test_dict_passes_through(self):
        """正常 dict 不被修改"""
        args = {"action": "product_list", "params": {"keyword": "T恤"}}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is None
        assert cleaned["params"] == {"keyword": "T恤"}


class TestTypeCoercionInteger:
    """integer 类型校验：string→int, float→int"""

    def test_string_to_int_success(self):
        """字符串 "20" 自动转为 int 20"""
        args = {"action": "product_list", "page": "2", "page_size": "20"}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is None
        assert cleaned["page"] == 2
        assert isinstance(cleaned["page"], int)
        assert cleaned["page_size"] == 20

    def test_float_to_int_success(self):
        """float 20.0 自动转为 int 20"""
        args = {"action": "product_list", "page": 1.0}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is None
        assert cleaned["page"] == 1
        assert isinstance(cleaned["page"], int)

    def test_string_non_numeric_returns_error(self):
        """非数字字符串 → 返回错误"""
        args = {"action": "product_list", "page": "abc"}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is not None
        assert "integer" in err

    def test_int_passes_through(self):
        """正常 int 不被修改"""
        args = {"action": "product_list", "page": 3}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is None
        assert cleaned["page"] == 3

    def test_bool_as_int_passes_through(self):
        """bool 是 int 子类，不触发 integer 校验"""
        args = {"action": "product_list", "page": True}
        cleaned, err = validate_tool_args("erp_product_query", args, TOOLS)
        assert err is None
        assert cleaned["page"] is True


class TestTypeCoercionBoolean:
    """boolean 类型校验：string→bool（最危险场景）"""

    def test_string_true_to_bool(self):
        """字符串 "true" 转为 True"""
        args = {"low_stock": "true"}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is None
        assert cleaned["low_stock"] is True

    def test_string_false_to_bool(self):
        """字符串 "false" 转为 False（核心：防止 "false" 被当 truthy）"""
        args = {"low_stock": "false"}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is None
        assert cleaned["low_stock"] is False

    def test_string_TRUE_case_insensitive(self):
        """大小写不敏感"""
        args = {"low_stock": "TRUE"}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is None
        assert cleaned["low_stock"] is True

    def test_string_False_case_insensitive(self):
        """混合大小写也能识别"""
        args = {"low_stock": "False"}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is None
        assert cleaned["low_stock"] is False

    def test_string_1_to_true(self):
        """字符串 "1" 转为 True"""
        args = {"low_stock": "1"}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is None
        assert cleaned["low_stock"] is True

    def test_string_0_to_false(self):
        """字符串 "0" 转为 False"""
        args = {"low_stock": "0"}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is None
        assert cleaned["low_stock"] is False

    def test_string_invalid_returns_error(self):
        """无法识别的字符串 → 返回错误"""
        args = {"low_stock": "yes"}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is not None
        assert "boolean" in err

    def test_bool_passes_through(self):
        """正常 bool 不被修改"""
        args = {"low_stock": False}
        cleaned, err = validate_tool_args("local_stock_query", args, TOOLS)
        assert err is None
        assert cleaned["low_stock"] is False
