"""
param_converter 单元测试。

覆盖: params_to_filters（51字段转换）+ diagnose_empty（16字段诊断）+ diagnose_error
"""
import sys
from pathlib import Path

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.param_converter import (
    params_to_filters,
    diagnose_empty,
    diagnose_error,
    TEXT_EQ_FIELDS,
    TEXT_LIKE_FIELDS,
    ENUM_EQ_FIELDS,
    FLAG_FIELDS,
)


# ============================================================
# params_to_filters: 文本精确匹配类（eq）
# ============================================================


class TestTextEqFields:

    def test_express_no(self):
        filters = params_to_filters({"express_no": "SF1234567890"})
        assert any(
            f["field"] == "express_no" and f["op"] == "eq"
            and f["value"] == "SF1234567890"
            for f in filters
        )

    def test_buyer_nick(self):
        filters = params_to_filters({"buyer_nick": "张三"})
        assert any(
            f["field"] == "buyer_nick" and f["op"] == "eq"
            and f["value"] == "张三"
            for f in filters
        )

    def test_doc_code(self):
        filters = params_to_filters({"doc_code": "PO202604001"})
        assert any(
            f["field"] == "doc_code" and f["op"] == "eq"
            and f["value"] == "PO202604001"
            for f in filters
        )

    def test_sku_code_maps_to_sku_outer_id(self):
        """sku_code 语义名 → sku_outer_id DB列名"""
        filters = params_to_filters({"sku_code": "ABC-01-RED"})
        assert any(
            f["field"] == "sku_outer_id" and f["op"] == "eq"
            and f["value"] == "ABC-01-RED"
            for f in filters
        )

    def test_receiver_name(self):
        filters = params_to_filters({"receiver_name": "李四"})
        assert any(
            f["field"] == "receiver_name" and f["value"] == "李四"
            for f in filters
        )

    def test_platform_refund_id(self):
        filters = params_to_filters({"platform_refund_id": "RF20260401001"})
        assert any(
            f["field"] == "platform_refund_id" and f["value"] == "RF20260401001"
            for f in filters
        )

    def test_purchase_order_code(self):
        filters = params_to_filters({"purchase_order_code": "PO-2026-001"})
        assert any(
            f["field"] == "purchase_order_code" and f["value"] == "PO-2026-001"
            for f in filters
        )

    def test_refund_express_no(self):
        filters = params_to_filters({"refund_express_no": "YT9876543210"})
        assert any(
            f["field"] == "refund_express_no" and f["value"] == "YT9876543210"
            for f in filters
        )

    def test_all_text_eq_fields_mapped(self):
        """确保所有 TEXT_EQ_FIELDS 都能生成 filter"""
        params = {k: f"val_{k}" for k in TEXT_EQ_FIELDS}
        filters = params_to_filters(params)
        filter_fields = {f["field"] for f in filters}
        for db_field in TEXT_EQ_FIELDS.values():
            assert db_field in filter_fields, f"missing filter for {db_field}"

    def test_strip_whitespace(self):
        """值带前后空格应被 strip"""
        filters = params_to_filters({"express_no": "  SF123  "})
        assert any(f["value"] == "SF123" for f in filters)

    def test_empty_string_ignored(self):
        """空字符串不产生 filter"""
        filters = params_to_filters({"express_no": ""})
        assert not any(f["field"] == "express_no" for f in filters)

    def test_whitespace_only_ignored(self):
        """纯空格字符串不产生 filter"""
        filters = params_to_filters({"buyer_nick": "   "})
        assert not any(f["field"] == "buyer_nick" for f in filters)


# ============================================================
# params_to_filters: 文本模糊匹配类（like）
# ============================================================


class TestTextLikeFields:

    def test_shop_name(self):
        filters = params_to_filters({"shop_name": "旗舰店"})
        assert any(
            f["field"] == "shop_name" and f["op"] == "like"
            and f["value"] == "%旗舰店%"
            for f in filters
        )

    def test_supplier_name(self):
        filters = params_to_filters({"supplier_name": "供应商A"})
        assert any(
            f["field"] == "supplier_name" and f["op"] == "like"
            and f["value"] == "%供应商A%"
            for f in filters
        )

    def test_warehouse_name(self):
        filters = params_to_filters({"warehouse_name": "A仓"})
        assert any(
            f["field"] == "warehouse_name" and f["value"] == "%A仓%"
            for f in filters
        )

    def test_item_name(self):
        filters = params_to_filters({"item_name": "连衣裙"})
        assert any(
            f["field"] == "item_name" and f["value"] == "%连衣裙%"
            for f in filters
        )

    def test_receiver_state(self):
        filters = params_to_filters({"receiver_state": "广东"})
        assert any(
            f["field"] == "receiver_state" and f["value"] == "%广东%"
            for f in filters
        )

    def test_receiver_city(self):
        filters = params_to_filters({"receiver_city": "深圳"})
        assert any(
            f["field"] == "receiver_city" and f["value"] == "%深圳%"
            for f in filters
        )

    def test_text_reason(self):
        filters = params_to_filters({"text_reason": "质量"})
        assert any(
            f["field"] == "text_reason" and f["value"] == "%质量%"
            for f in filters
        )

    def test_remark(self):
        filters = params_to_filters({"remark": "加急"})
        assert any(
            f["field"] == "remark" and f["value"] == "%加急%"
            for f in filters
        )

    def test_express_company(self):
        filters = params_to_filters({"express_company": "顺丰"})
        assert any(
            f["field"] == "express_company" and f["value"] == "%顺丰%"
            for f in filters
        )

    def test_creator_name(self):
        filters = params_to_filters({"creator_name": "小李"})
        assert any(
            f["field"] == "creator_name" and f["value"] == "%小李%"
            for f in filters
        )

    def test_all_text_like_fields_mapped(self):
        """确保所有 TEXT_LIKE_FIELDS 都能生成 like filter"""
        params = {k: f"val_{k}" for k in TEXT_LIKE_FIELDS}
        filters = params_to_filters(params)
        filter_fields = {f["field"] for f in filters}
        for db_field in TEXT_LIKE_FIELDS.values():
            assert db_field in filter_fields, f"missing like filter for {db_field}"

    def test_like_empty_string_ignored(self):
        filters = params_to_filters({"shop_name": ""})
        assert not any(f["field"] == "shop_name" for f in filters)


# ============================================================
# params_to_filters: 枚举精确匹配类（eq）
# ============================================================


class TestEnumEqFields:

    def test_order_status(self):
        filters = params_to_filters({"order_status": "WAIT_SEND_GOODS"})
        assert any(
            f["field"] == "order_status" and f["op"] == "eq"
            and f["value"] == "WAIT_SEND_GOODS"
            for f in filters
        )

    def test_doc_status(self):
        filters = params_to_filters({"doc_status": "已审核"})
        assert any(
            f["field"] == "doc_status" and f["value"] == "已审核"
            for f in filters
        )

    def test_aftersale_type(self):
        filters = params_to_filters({"aftersale_type": "退货退款"})
        assert any(
            f["field"] == "aftersale_type" and f["value"] == "退货退款"
            for f in filters
        )

    def test_refund_status(self):
        filters = params_to_filters({"refund_status": "退款中"})
        assert any(
            f["field"] == "refund_status" and f["value"] == "退款中"
            for f in filters
        )

    def test_good_status(self):
        filters = params_to_filters({"good_status": "买家已退货"})
        assert any(
            f["field"] == "good_status" and f["value"] == "买家已退货"
            for f in filters
        )

    def test_order_type(self):
        filters = params_to_filters({"order_type": "补发"})
        assert any(
            f["field"] == "order_type" and f["value"] == "补发"
            for f in filters
        )

    def test_all_enum_eq_fields_mapped(self):
        params = {k: f"val_{k}" for k in ENUM_EQ_FIELDS}
        filters = params_to_filters(params)
        filter_fields = {f["field"] for f in filters}
        for db_field in ENUM_EQ_FIELDS.values():
            assert db_field in filter_fields


# ============================================================
# params_to_filters: 布尔标记类（eq 1）
# ============================================================


class TestFlagFields:

    @pytest.mark.parametrize("flag", FLAG_FIELDS)
    def test_flag_true_generates_eq_1(self, flag):
        filters = params_to_filters({flag: True})
        assert any(
            f["field"] == flag and f["op"] == "eq" and f["value"] == 1
            for f in filters
        )

    @pytest.mark.parametrize("flag", FLAG_FIELDS)
    def test_flag_false_no_filter(self, flag):
        filters = params_to_filters({flag: False})
        assert not any(f["field"] == flag for f in filters)

    @pytest.mark.parametrize("flag", FLAG_FIELDS)
    def test_flag_absent_no_filter(self, flag):
        filters = params_to_filters({})
        assert not any(f["field"] == flag for f in filters)


# ============================================================
# params_to_filters: 组合场景
# ============================================================


class TestCombined:

    def test_multiple_new_fields_together(self):
        """多个新字段同时存在"""
        filters = params_to_filters({
            "express_no": "SF123",
            "shop_name": "旗舰店",
            "order_status": "WAIT_SEND_GOODS",
            "is_cancel": True,
        })
        fields = [f["field"] for f in filters]
        assert "express_no" in fields
        assert "shop_name" in fields
        assert "order_status" in fields
        assert "is_cancel" in fields

    def test_new_and_old_fields_coexist(self):
        """新字段和旧字段（order_no）共存"""
        filters = params_to_filters({
            "time_range": "2026-04-01 ~ 2026-04-17",
            "order_no": "123456789012345678",
            "buyer_nick": "张三",
            "order_status": "FINISHED",
        })
        fields = [f["field"] for f in filters]
        assert "order_no" in fields
        assert "buyer_nick" in fields
        assert "order_status" in fields
        # platform 的映射测试在 test_warehouse_agent.py 中覆盖
        # （需要 PLATFORM_NORMALIZE 导入链，本地 Python 3.14 缺 pydantic）

    def test_empty_params_returns_empty(self):
        assert params_to_filters({}) == []


# ============================================================
# diagnose_empty: 新增字段诊断
# ============================================================


class TestDiagnoseEmpty:

    def test_express_no(self):
        result = diagnose_empty([
            {"field": "express_no", "op": "eq", "value": "SF123"},
        ])
        assert "快递单号" in result
        assert "SF123" in result

    def test_shop_name_like(self):
        """like 值去掉 % 展示"""
        result = diagnose_empty([
            {"field": "shop_name", "op": "like", "value": "%旗舰店%"},
        ])
        assert "店铺" in result
        assert "旗舰店" in result
        assert "%" not in result

    def test_order_status(self):
        result = diagnose_empty([
            {"field": "order_status", "op": "eq", "value": "WAIT_SEND_GOODS"},
        ])
        assert "订单状态" in result

    def test_supplier_name(self):
        result = diagnose_empty([
            {"field": "supplier_name", "op": "like", "value": "%供应商A%"},
        ])
        assert "供应商" in result

    def test_warehouse_name(self):
        result = diagnose_empty([
            {"field": "warehouse_name", "op": "like", "value": "%A仓%"},
        ])
        assert "仓库" in result

    def test_aftersale_type(self):
        result = diagnose_empty([
            {"field": "aftersale_type", "op": "eq", "value": "退货退款"},
        ])
        assert "售后类型" in result

    def test_refund_status(self):
        result = diagnose_empty([
            {"field": "refund_status", "op": "eq", "value": "退款中"},
        ])
        assert "退款状态" in result

    def test_doc_code(self):
        result = diagnose_empty([
            {"field": "doc_code", "op": "eq", "value": "PO2026001"},
        ])
        assert "单据编号" in result

    def test_sku_outer_id(self):
        result = diagnose_empty([
            {"field": "sku_outer_id", "op": "eq", "value": "SKU-001"},
        ])
        assert "SKU编码" in result

    def test_receiver_state(self):
        result = diagnose_empty([
            {"field": "receiver_state", "op": "like", "value": "%广东%"},
        ])
        assert "收件省" in result

    def test_buyer_nick(self):
        result = diagnose_empty([
            {"field": "buyer_nick", "op": "eq", "value": "张三"},
        ])
        assert "买家昵称" in result

    def test_empty_value_skipped(self):
        result = diagnose_empty([
            {"field": "express_no", "op": "eq", "value": ""},
        ])
        assert result == ""

    # platform 诊断测试在 test_warehouse_agent.py::test_diagnose_empty_platform_filter 覆盖
    # （需要 PLATFORM_CN 导入链，本地 Python 3.14 缺 pydantic）


# ============================================================
# diagnose_error
# ============================================================


class TestDiagnoseError:

    def test_timeout(self):
        assert "超时" in diagnose_error("query timeout after 30s")

    def test_timeout_cn(self):
        assert "缩小时间" in diagnose_error("统计查询超时")

    def test_too_many(self):
        assert "数据量" in diagnose_error("too many rows: 65535")

    def test_invalid_doc_type(self):
        assert "文档类型" in diagnose_error("invalid doc_type: xxx")

    def test_no_valid_field(self):
        assert "字段" in diagnose_error("no valid field in request")

    def test_filter_error(self):
        assert "过滤条件" in diagnose_error("column not found in filter")

    def test_empty_msg(self):
        assert diagnose_error("") == ""

    def test_unknown_error_empty(self):
        assert diagnose_error("something weird happened") == ""
