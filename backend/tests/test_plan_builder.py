"""
PlanBuilder 工具函数单元测试。

覆盖: _sanitize_params 参数透传 + _fill_codes_for_params L2 补全 + 域路由冲突检测
设计文档: docs/document/TECH_ERPAgent架构简化.md / TECH_意图完整性校验层.md
"""
import json
import sys
from pathlib import Path

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from unittest.mock import MagicMock

from services.agent.plan_builder import (
    _fill_codes_for_params,
    _sanitize_params,
    parse_extract_response,
    get_capability_manifest,
    build_extract_prompt,
    _DOMAIN_DOC_TYPES,
    _DOMAIN_DEFAULT_DOC_TYPE,
)


# ============================================================
# _sanitize_params 透传测试
# ============================================================


class TestSanitizeParamsPassthrough:
    """_sanitize_params 应透传 product_code/order_no/include_invalid。"""

    def test_product_code_passed_through(self):
        result = _sanitize_params({"product_code": "DBTXL01"})
        assert result["product_code"] == "DBTXL01"

    def test_order_no_passed_through(self):
        result = _sanitize_params({"order_no": "126036803257340376"})
        assert result["order_no"] == "126036803257340376"

    def test_include_invalid_true(self):
        result = _sanitize_params({"include_invalid": True})
        assert result["include_invalid"] is True

    def test_include_invalid_false(self):
        result = _sanitize_params({"include_invalid": False})
        assert result["include_invalid"] is False

    def test_include_invalid_non_bool_passthrough(self):
        """str 类型透传（下游 truthy 判断兼容）。"""
        result = _sanitize_params({"include_invalid": "yes"})
        assert result["include_invalid"] == "yes"

    def test_empty_product_code_not_passed(self):
        result = _sanitize_params({"product_code": ""})
        assert "product_code" not in result

    def test_empty_order_no_not_passed(self):
        result = _sanitize_params({"order_no": ""})
        assert "order_no" not in result

    def test_all_new_fields_together(self):
        result = _sanitize_params({
            "mode": "detail",
            "product_code": "ABC123",
            "order_no": "999888777666",
            "include_invalid": True,
        })
        assert result["product_code"] == "ABC123"
        assert result["order_no"] == "999888777666"
        assert result["include_invalid"] is True
        assert result["mode"] == "export"  # detail→export mapping

    def test_existing_fields_still_work(self):
        result = _sanitize_params({
            "mode": "summary",
            "doc_type": "order",
            "platform": "taobao",
            "group_by": "shop",
            "time_range": "2026-04-17 ~ 2026-04-17",
            "time_col": "pay_time",
        })
        assert result["mode"] == "summary"
        assert result["doc_type"] == "order"
        assert result["platform"] == "taobao"
        assert result["group_by"] == ["shop"]  # str→list 转换
        assert result["time_range"] == "2026-04-17 ~ 2026-04-17"
        assert result["time_col"] == "pay_time"

    # ── is_scalping 透传（刷单导出 Bug 修复验证）──

    def test_is_scalping_true_passed_through(self):
        result = _sanitize_params({"is_scalping": True})
        assert result["is_scalping"] is True

    def test_is_scalping_false_passed_through(self):
        result = _sanitize_params({"is_scalping": False})
        assert result["is_scalping"] is False

    def test_scalping_with_export_mode(self):
        """刷单导出场景：is_scalping + include_invalid + export 全部透传。"""
        result = _sanitize_params({
            "doc_type": "order",
            "mode": "export",
            "time_range": "2026-04-18 ~ 2026-04-18",
            "is_scalping": True,
            "include_invalid": True,
        })
        assert result["is_scalping"] is True
        assert result["include_invalid"] is True
        assert result["mode"] == "export"

    # ── 简单类型透传 / 非简单类型阻断 ──

    def test_unknown_str_param_passed_through(self):
        """LLM 提取的未知 str 参数透传给下游（被 **_kwargs 吸收）。"""
        result = _sanitize_params({"some_future_param": "hello"})
        assert result["some_future_param"] == "hello"

    def test_int_param_passed_through(self):
        result = _sanitize_params({"limit": 100})
        assert result["limit"] == 100

    def test_float_param_passed_through(self):
        result = _sanitize_params({"threshold": 0.5})
        assert result["threshold"] == 0.5

    def test_dict_param_blocked(self):
        """dict 类型不透传，防止注入。"""
        result = _sanitize_params({"evil": {"inject": True}})
        assert "evil" not in result

    def test_nested_list_blocked(self):
        """嵌套 list 不透传。"""
        result = _sanitize_params({"evil": [[1, 2]]})
        assert "evil" not in result

    def test_empty_list_not_passed(self):
        result = _sanitize_params({"tags": []})
        assert "tags" not in result

    def test_str_list_passed_through(self):
        """纯字符串列表透传。"""
        result = _sanitize_params({"tags": ["a", "b"]})
        assert result["tags"] == ["a", "b"]

    # ── group_by str→list 转换（Bug 1 修复验证）──

    def test_group_by_string_to_list(self):
        result = _sanitize_params({"group_by": "product"})
        assert result["group_by"] == ["product"]

    def test_group_by_list_unchanged(self):
        result = _sanitize_params({"group_by": ["shop", "platform"]})
        assert result["group_by"] == ["shop", "platform"]

    def test_group_by_empty_not_passed(self):
        result = _sanitize_params({"group_by": ""})
        assert "group_by" not in result

    # ── fields 白名单校验 ──

    def test_fields_string_to_list(self):
        result = _sanitize_params({"fields": "remark"})
        assert result["fields"] == ["remark"]

    def test_fields_list_filtered(self):
        result = _sanitize_params({"fields": ["remark", "invalid_col", "cost"]})
        assert result["fields"] == ["remark", "cost"]

    def test_fields_all_invalid_removed(self):
        result = _sanitize_params({"fields": ["xxx", "yyy"]})
        assert "fields" not in result

    def test_fields_empty_not_passed(self):
        result = _sanitize_params({"fields": []})
        assert "fields" not in result


# ============================================================
# ERPAgent._fill_platform L2 意图补全测试
# ============================================================


class TestFillPlatform:
    """测试 ERPAgent._fill_platform 静态方法。"""

    def _fill(self, params: dict, query: str) -> None:
        from services.agent.erp_agent import ERPAgent
        ERPAgent._fill_platform(params, query)

    def test_single_cn_platform_fills(self):
        params: dict = {}
        self._fill(params, "昨天淘宝的订单统计")
        assert params["platform"] == "tb"

    def test_douyin_alias(self):
        params: dict = {}
        self._fill(params, "抖店订单")
        assert params["platform"] == "fxg"

    def test_tianmao_maps_to_tb(self):
        params: dict = {}
        self._fill(params, "天猫店铺的售后")
        assert params["platform"] == "tb"

    def test_ai_already_extracted_not_overwritten(self):
        params = {"platform": "jd"}
        self._fill(params, "淘宝的订单")
        assert params["platform"] == "jd"

    def test_multi_platform_no_fill(self):
        params: dict = {}
        self._fill(params, "淘宝和京东的订单")
        assert "platform" not in params

    def test_no_platform_no_fill(self):
        params: dict = {}
        self._fill(params, "昨天的订单统计")
        assert "platform" not in params

    def test_1688_fills(self):
        params: dict = {}
        self._fill(params, "1688的采购单")
        assert params["platform"] == "1688"

    def test_same_platform_aliases_count_as_one(self):
        params: dict = {}
        self._fill(params, "淘宝天猫的订单")
        assert params["platform"] == "tb"


# ============================================================
# L2 域路由冲突检测测试
# ============================================================


class TestDomainRouteConflict:
    """域路由冲突检测——通过 parse_extract_response + 手动校验模拟。"""

    def _check_conflict(self, domain: str, doc_type: str) -> str:
        """模拟 ERPAgent._extract_params 中的冲突检测逻辑。"""
        allowed = _DOMAIN_DOC_TYPES.get(domain)
        if doc_type and allowed and doc_type not in allowed:
            return _DOMAIN_DEFAULT_DOC_TYPE.get(domain, next(iter(allowed)))
        return doc_type

    def test_trade_with_wrong_doc_type_corrected(self):
        assert self._check_conflict("trade", "purchase") == "order"

    def test_purchase_with_order_doc_type_corrected(self):
        result = self._check_conflict("purchase", "order")
        assert result in {"purchase", "purchase_return"}

    def test_correct_routing_unchanged(self):
        assert self._check_conflict("trade", "order") == "order"

    def test_no_doc_type_no_error(self):
        assert self._check_conflict("trade", "") == ""


# ============================================================
# L2 product_code / order_no DB 验证补全测试
# ============================================================


def _mock_db_with_products(codes: list[str]) -> MagicMock:
    """创建一个 mock DB，erp_products 表中包含指定 outer_id。"""
    db = MagicMock()

    def _mock_table(table_name):
        table = MagicMock()
        chain = MagicMock()
        table.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain

        def _execute():
            result = MagicMock()
            eq_calls = chain.eq.call_args_list
            val = None
            for call in eq_calls:
                args = call[0] if call[0] else ()
                if len(args) >= 2 and args[0] in ("outer_id", "order_no"):
                    val = args[1]
            if table_name == "erp_products":
                result.data = [{"outer_id": val}] if val in codes else []
            elif table_name == "erp_document_items":
                result.data = [{"order_no": val}] if val in codes else []
            else:
                result.data = []
            return result

        chain.execute = _execute
        return table

    db.table = _mock_table
    return db


class TestFillCodesForParams:
    """_fill_codes_for_params 操作单个 params dict。"""

    @pytest.mark.asyncio
    async def test_product_code_found_in_db(self):
        params: dict = {}
        db = _mock_db_with_products(["DBTXL01"])
        await _fill_codes_for_params(params, "查 DBTXL01 的订单", db, "org1")
        assert params["product_code"] == "DBTXL01"

    @pytest.mark.asyncio
    async def test_product_code_not_in_db(self):
        params: dict = {}
        db = _mock_db_with_products([])
        await _fill_codes_for_params(params, "查 XYZABC 的订单", db, "org1")
        assert "product_code" not in params

    @pytest.mark.asyncio
    async def test_product_code_already_extracted(self):
        params = {"product_code": "EXIST01"}
        db = _mock_db_with_products(["DBTXL01"])
        await _fill_codes_for_params(params, "查 DBTXL01 的订单", db, "org1")
        assert params["product_code"] == "EXIST01"

    @pytest.mark.asyncio
    async def test_order_no_found_in_db(self):
        params: dict = {}
        db = _mock_db_with_products(["126036803257340376"])
        await _fill_codes_for_params(
            params, "查订单号 126036803257340376", db, "org1",
        )
        assert params["order_no"] == "126036803257340376"

    @pytest.mark.asyncio
    async def test_order_no_not_in_db(self):
        params: dict = {}
        db = _mock_db_with_products([])
        await _fill_codes_for_params(
            params, "联系方式 138001380001380", db, "org1",
        )
        assert "order_no" not in params

    @pytest.mark.asyncio
    async def test_order_no_already_extracted(self):
        params = {"order_no": "999888777666555444"}
        db = _mock_db_with_products(["126036803257340376"])
        await _fill_codes_for_params(
            params, "查订单号 126036803257340376", db, "org1",
        )
        assert params["order_no"] == "999888777666555444"

    @pytest.mark.asyncio
    async def test_no_db_no_fill(self):
        params: dict = {}
        await _fill_codes_for_params(params, "查 DBTXL01 的订单", None, None)
        assert "product_code" not in params

    @pytest.mark.asyncio
    async def test_short_code_ignored(self):
        params: dict = {}
        db = _mock_db_with_products(["AB"])
        await _fill_codes_for_params(params, "查 AB 的订单", db, "org1")
        assert "product_code" not in params

    @pytest.mark.asyncio
    async def test_xhs_order_format(self):
        params: dict = {}
        db = _mock_db_with_products(["P123456789012345678"])
        await _fill_codes_for_params(
            params, "小红书订单 P123456789012345678", db, "org1",
        )
        assert params["order_no"] == "P123456789012345678"

    @pytest.mark.asyncio
    async def test_both_code_and_order(self):
        params: dict = {}
        db = _mock_db_with_products(["DBTXL01", "126036803257340376"])
        await _fill_codes_for_params(
            params, "商品 DBTXL01 订单 126036803257340376",
            db, "org1",
        )
        assert params["product_code"] == "DBTXL01"
        assert params["order_no"] == "126036803257340376"


# ============================================================
# get_capability_manifest 结构完整性测试
# ============================================================


class TestCapabilityManifest:
    """验证 get_capability_manifest 返回结构完整且从常量自动生成。"""

    def test_required_keys_present(self):
        m = get_capability_manifest()
        required = {
            "domains", "modes", "doc_types", "group_by", "filters",
            "time_cols", "platforms", "field_categories", "summary",
            "use_when", "dont_use_when", "returns", "examples",
            "auto_behaviors",
        }
        assert required.issubset(m.keys())

    def test_group_by_has_six_dims(self):
        m = get_capability_manifest()
        assert set(m["group_by"]) == {
            "shop", "platform", "product", "supplier", "warehouse", "status",
        }

    def test_time_cols_complete(self):
        m = get_capability_manifest()
        assert set(m["time_cols"]) == {
            "doc_created_at", "pay_time", "consign_time",
            "apply_date", "delivery_date", "finished_at",
        }

    def test_field_categories_not_empty(self):
        m = get_capability_manifest()
        assert len(m["field_categories"]) >= 10

    def test_platforms_are_chinese(self):
        m = get_capability_manifest()
        for p in m["platforms"]:
            assert not p.isascii(), f"平台名应为中文: {p}"

    def test_examples_have_query_and_effect(self):
        m = get_capability_manifest()
        for ex in m["examples"]:
            assert "query" in ex and "effect" in ex


# ============================================================
# build_extract_prompt 补全验证
# ============================================================


class TestBuildExtractPromptCompleteness:
    """验证 prompt 包含新增的 group_by 维度、consign_time、fields。"""

    def test_all_group_by_dims_in_prompt(self):
        prompt = build_extract_prompt("test")
        for dim in ("shop", "platform", "product", "supplier",
                     "warehouse", "status"):
            assert dim in prompt, f"group_by 维度 {dim} 未在 prompt 中"

    def test_consign_time_in_prompt(self):
        prompt = build_extract_prompt("test")
        assert "consign_time" in prompt

    def test_fields_in_prompt(self):
        prompt = build_extract_prompt("test")
        assert "fields" in prompt
        assert "remark" in prompt

    def test_group_by_example_in_prompt(self):
        prompt = build_extract_prompt("test")
        assert "product" in prompt
        assert "示例3" in prompt or "按商品分组" in prompt

    def test_new_filter_params_in_prompt(self):
        """验证新增的过滤参数在 prompt 中"""
        prompt = build_extract_prompt("test")
        new_params = [
            "express_no", "buyer_nick", "shop_name", "order_status",
            "aftersale_type", "refund_status", "supplier_name",
            "warehouse_name", "receiver_state", "receiver_city",
            "is_cancel", "is_refund", "is_exception", "is_halt",
            "express_company", "doc_code", "sku_code", "doc_status",
            "text_reason", "good_status",
        ]
        for p in new_params:
            assert p in prompt, f"新参数 {p} 未在 prompt 中"

    def test_express_no_rule_in_prompt(self):
        """prompt 包含快递单号识别规则"""
        prompt = build_extract_prompt("test")
        assert "SF" in prompt
        assert "express_no" in prompt

    def test_express_no_example_in_prompt(self):
        """prompt 包含快递单号查询示例"""
        prompt = build_extract_prompt("test")
        assert "SF1234567890" in prompt


# ============================================================
# get_capability_manifest: 新增 filters 字段验证
# ============================================================


class TestCapabilityManifestFilters:
    """验证 capability_manifest 的 filters 包含新增字段。"""

    def test_new_filters_in_manifest(self):
        m = get_capability_manifest()
        new_filters = [
            "shop_name", "warehouse_name", "supplier_name",
            "express_no", "buyer_nick", "order_status", "doc_status",
            "aftersale_type", "refund_status", "express_company",
        ]
        for f in new_filters:
            assert f in m["filters"], f"manifest filters 缺少 {f}"

    def test_flag_filters_in_manifest(self):
        m = get_capability_manifest()
        for flag in ["is_cancel", "is_refund", "is_exception",
                     "is_halt", "is_urgent", "is_presell"]:
            assert flag in m["filters"], f"manifest filters 缺少 {flag}"


# ============================================================
# _EXPRESS_NO_RE 正则匹配测试
# ============================================================


class TestExpressNoRegex:
    """快递单号正则识别。"""

    def test_sf_match(self):
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert _EXPRESS_NO_RE.findall("快递 SF1234567890 查一下")

    def test_yt_match(self):
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert _EXPRESS_NO_RE.findall("YT98765432101234")

    def test_zto_match(self):
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert _EXPRESS_NO_RE.findall("ZTO12345678901")

    def test_jd_match(self):
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert _EXPRESS_NO_RE.findall("JD12345678901234")

    def test_ems_match(self):
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert _EXPRESS_NO_RE.findall("EMS1234567890")

    def test_case_insensitive(self):
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert _EXPRESS_NO_RE.findall("sf1234567890")

    def test_pure_digits_no_match(self):
        """纯数字不匹配快递正则（那是订单号）"""
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert not _EXPRESS_NO_RE.findall("1234567890123456")

    def test_short_number_no_match(self):
        """前缀+短数字不匹配"""
        from services.agent.plan_builder import _EXPRESS_NO_RE
        assert not _EXPRESS_NO_RE.findall("SF12345")


# ============================================================
# _fill_codes_for_params: express_no 补全
# ============================================================


def _mock_db_with_express(codes: list[str]):
    """mock DB：同时支持 product/order/express 查询。"""
    db = MagicMock()

    def _mock_table(table_name):
        table = MagicMock()
        chain = MagicMock()
        table.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain

        def _execute():
            result = MagicMock()
            eq_calls = chain.eq.call_args_list
            val = None
            for call in eq_calls:
                args = call[0] if call[0] else ()
                if len(args) >= 2 and args[0] in (
                    "outer_id", "order_no", "express_no",
                ):
                    val = args[1]
            if table_name == "erp_products":
                result.data = [{"outer_id": val}] if val in codes else []
            elif table_name == "erp_document_items":
                result.data = (
                    [{"order_no": val}] if val in codes
                    else [{"express_no": val}] if val in codes
                    else []
                )
            else:
                result.data = []
            return result

        chain.execute = _execute
        return table

    db.table = _mock_table
    return db


class TestFillCodesExpressNo:
    """_fill_codes_for_params 快递单号补全。"""

    @pytest.mark.asyncio
    async def test_express_no_found_in_db(self):
        params: dict = {}
        db = _mock_db_with_express(["SF1234567890"])
        await _fill_codes_for_params(
            params, "查快递 SF1234567890", db, "org1",
        )
        assert params.get("express_no") == "SF1234567890"

    @pytest.mark.asyncio
    async def test_express_no_not_in_db(self):
        params: dict = {}
        db = _mock_db_with_express([])
        await _fill_codes_for_params(
            params, "查快递 SF9999999999", db, "org1",
        )
        assert "express_no" not in params

    @pytest.mark.asyncio
    async def test_express_no_already_extracted(self):
        params = {"express_no": "YT111111111"}
        db = _mock_db_with_express(["SF1234567890"])
        await _fill_codes_for_params(
            params, "查快递 SF1234567890", db, "org1",
        )
        assert params["express_no"] == "YT111111111"

    @pytest.mark.asyncio
    async def test_pure_digits_not_matched_as_express(self):
        """纯数字走 order_no 路径，不走 express_no"""
        params: dict = {}
        db = _mock_db_with_express(["1234567890123456"])
        await _fill_codes_for_params(
            params, "查 1234567890123456", db, "org1",
        )
        assert "express_no" not in params
