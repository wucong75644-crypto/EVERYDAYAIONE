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
from services.agent.input_normalizer import (
    MultiValueParser,
    InputNormalizer,
)


# ============================================================
# 多值解析测试已迁移到 test_input_normalizer.py
# 以下仅保留 params_to_filters 集成测试
# ============================================================

# ============================================================
# params_to_filters: 多值集成测试
# ============================================================


class TestMultiValueIntegration:
    """验证各字段多值传入后生成 in 过滤器"""

    def test_product_code_comma(self):
        filters, _ = params_to_filters({"product_code": "A,B,C"})
        f = [x for x in filters if x["field"] == "outer_id"]
        assert len(f) == 1
        assert f[0]["op"] == "in"
        assert f[0]["value"] == ["A", "B", "C"]

    def test_product_code_single(self):
        filters, _ = params_to_filters({"product_code": "ABC"})
        f = [x for x in filters if x["field"] == "outer_id"]
        assert len(f) == 1
        assert f[0]["op"] == "eq"
        assert f[0]["value"] == "ABC"

    def test_product_code_list(self):
        filters, _ = params_to_filters({"product_code": ["X", "Y"]})
        f = [x for x in filters if x["field"] == "outer_id"]
        assert f[0]["op"] == "in"

    def test_order_no_comma(self):
        filters, _ = params_to_filters({"order_no": "O001,O002"})
        f = [x for x in filters if x["field"] == "order_no"]
        assert f[0]["op"] == "in"
        assert f[0]["value"] == ["O001", "O002"]

    def test_doc_code_multi(self):
        filters, _ = params_to_filters({"doc_code": "PO001;PO002"})
        f = [x for x in filters if x["field"] == "doc_code"]
        assert f[0]["op"] == "in"

    def test_sku_code_multi(self):
        filters, _ = params_to_filters({"sku_code": "SKU1,SKU2,SKU3"})
        f = [x for x in filters if x["field"] == "sku_outer_id"]
        assert f[0]["op"] == "in"
        assert len(f[0]["value"]) == 3

    def test_buyer_nick_single_stays_eq(self):
        filters, _ = params_to_filters({"buyer_nick": "张三"})
        f = [x for x in filters if x["field"] == "buyer_nick"]
        assert f[0]["op"] == "eq"


# ============================================================
# params_to_filters: 文本精确匹配类（eq）
# ============================================================


class TestTextEqFields:

    def test_express_no(self):
        filters, _ = params_to_filters({"express_no": "SF1234567890"})
        assert any(
            f["field"] == "express_no" and f["op"] == "eq"
            and f["value"] == "SF1234567890"
            for f in filters
        )

    def test_buyer_nick(self):
        filters, _ = params_to_filters({"buyer_nick": "张三"})
        assert any(
            f["field"] == "buyer_nick" and f["op"] == "eq"
            and f["value"] == "张三"
            for f in filters
        )

    def test_doc_code(self):
        filters, _ = params_to_filters({"doc_code": "PO202604001"})
        assert any(
            f["field"] == "doc_code" and f["op"] == "eq"
            and f["value"] == "PO202604001"
            for f in filters
        )

    def test_sku_code_maps_to_sku_outer_id(self):
        """sku_code 语义名 → sku_outer_id DB列名"""
        filters, _ = params_to_filters({"sku_code": "ABC-01-RED"})
        assert any(
            f["field"] == "sku_outer_id" and f["op"] == "eq"
            and f["value"] == "ABC-01-RED"
            for f in filters
        )

    def test_receiver_name(self):
        filters, _ = params_to_filters({"receiver_name": "李四"})
        assert any(
            f["field"] == "receiver_name" and f["value"] == "李四"
            for f in filters
        )

    def test_platform_refund_id(self):
        filters, _ = params_to_filters({"platform_refund_id": "RF20260401001"})
        assert any(
            f["field"] == "platform_refund_id" and f["value"] == "RF20260401001"
            for f in filters
        )

    def test_purchase_order_code(self):
        filters, _ = params_to_filters({"purchase_order_code": "PO-2026-001"})
        assert any(
            f["field"] == "purchase_order_code" and f["value"] == "PO-2026-001"
            for f in filters
        )

    def test_refund_express_no(self):
        filters, _ = params_to_filters({"refund_express_no": "YT9876543210"})
        assert any(
            f["field"] == "refund_express_no" and f["value"] == "YT9876543210"
            for f in filters
        )

    def test_all_text_eq_fields_mapped(self):
        """确保所有 TEXT_EQ_FIELDS 都能生成 filter"""
        params = {k: f"val_{k}" for k in TEXT_EQ_FIELDS}
        filters, _ = params_to_filters(params)
        filter_fields = {f["field"] for f in filters}
        for db_field in TEXT_EQ_FIELDS.values():
            assert db_field in filter_fields, f"missing filter for {db_field}"

    def test_strip_whitespace(self):
        """值带前后空格应被 strip"""
        filters, _ = params_to_filters({"express_no": "  SF123  "})
        assert any(f["value"] == "SF123" for f in filters)

    def test_empty_string_ignored(self):
        """空字符串不产生 filter"""
        filters, _ = params_to_filters({"express_no": ""})
        assert not any(f["field"] == "express_no" for f in filters)

    def test_whitespace_only_ignored(self):
        """纯空格字符串不产生 filter"""
        filters, _ = params_to_filters({"buyer_nick": "   "})
        assert not any(f["field"] == "buyer_nick" for f in filters)


# ============================================================
# params_to_filters: 文本模糊匹配类（like）
# ============================================================


class TestTextLikeFields:

    def test_shop_name(self):
        filters, _ = params_to_filters({"shop_name": "旗舰店"})
        assert any(
            f["field"] == "shop_name" and f["op"] == "like"
            and f["value"] == "%旗舰店%"
            for f in filters
        )

    def test_supplier_name(self):
        filters, _ = params_to_filters({"supplier_name": "供应商A"})
        assert any(
            f["field"] == "supplier_name" and f["op"] == "like"
            and f["value"] == "%供应商A%"
            for f in filters
        )

    def test_warehouse_name(self):
        filters, _ = params_to_filters({"warehouse_name": "A仓"})
        assert any(
            f["field"] == "warehouse_name" and f["value"] == "%A仓%"
            for f in filters
        )

    def test_item_name(self):
        filters, _ = params_to_filters({"item_name": "连衣裙"})
        assert any(
            f["field"] == "item_name" and f["value"] == "%连衣裙%"
            for f in filters
        )

    def test_receiver_state(self):
        filters, _ = params_to_filters({"receiver_state": "广东"})
        assert any(
            f["field"] == "receiver_state" and f["value"] == "%广东%"
            for f in filters
        )

    def test_receiver_city(self):
        filters, _ = params_to_filters({"receiver_city": "深圳"})
        assert any(
            f["field"] == "receiver_city" and f["value"] == "%深圳%"
            for f in filters
        )

    def test_text_reason(self):
        filters, _ = params_to_filters({"text_reason": "质量"})
        assert any(
            f["field"] == "text_reason" and f["value"] == "%质量%"
            for f in filters
        )

    def test_remark(self):
        filters, _ = params_to_filters({"remark": "加急"})
        assert any(
            f["field"] == "remark" and f["value"] == "%加急%"
            for f in filters
        )

    def test_express_company(self):
        filters, _ = params_to_filters({"express_company": "顺丰"})
        assert any(
            f["field"] == "express_company" and f["value"] == "%顺丰%"
            for f in filters
        )

    def test_creator_name(self):
        filters, _ = params_to_filters({"creator_name": "小李"})
        assert any(
            f["field"] == "creator_name" and f["value"] == "%小李%"
            for f in filters
        )

    def test_all_text_like_fields_mapped(self):
        """确保所有 TEXT_LIKE_FIELDS 都能生成 like filter"""
        params = {k: f"val_{k}" for k in TEXT_LIKE_FIELDS}
        filters, _ = params_to_filters(params)
        filter_fields = {f["field"] for f in filters}
        for db_field in TEXT_LIKE_FIELDS.values():
            assert db_field in filter_fields, f"missing like filter for {db_field}"

    def test_like_empty_string_ignored(self):
        filters, _ = params_to_filters({"shop_name": ""})
        assert not any(f["field"] == "shop_name" for f in filters)


# ============================================================
# params_to_filters: 枚举精确匹配类（eq）
# ============================================================


class TestEnumEqFields:

    def test_order_status(self):
        filters, _ = params_to_filters({"order_status": "WAIT_SEND_GOODS"})
        assert any(
            f["field"] == "order_status" and f["op"] == "eq"
            and f["value"] == "WAIT_SEND_GOODS"
            for f in filters
        )

    def test_doc_status(self):
        """已审核 → 归一化为 DB 值 VERIFYING"""
        filters, _ = params_to_filters({"doc_status": "已审核"})
        assert any(
            f["field"] == "doc_status" and f["value"] == "VERIFYING"
            for f in filters
        )

    def test_aftersale_type(self):
        """退货退款 → 归一化为 DB 值 "2" """
        filters, _ = params_to_filters({"aftersale_type": "退货退款"})
        assert any(
            f["field"] == "aftersale_type" and f["value"] == "2"
            for f in filters
        )

    def test_refund_status(self):
        """退款中 → 归一化为 DB 值 "1" """
        filters, _ = params_to_filters({"refund_status": "退款中"})
        assert any(
            f["field"] == "refund_status" and f["value"] == "1"
            for f in filters
        )

    def test_good_status(self):
        """买家已退货 → 归一化为 DB 值 "2" """
        filters, _ = params_to_filters({"good_status": "买家已退货"})
        assert any(
            f["field"] == "good_status" and f["value"] == "2"
            for f in filters
        )

    def test_order_type(self):
        """补发 → 归一化为 DB 值 "14" """
        filters, _ = params_to_filters({"order_type": "补发"})
        assert any(
            f["field"] == "order_type" and f["value"] == "14"
            for f in filters
        )

    def test_all_enum_eq_fields_mapped(self):
        params = {k: f"val_{k}" for k in ENUM_EQ_FIELDS}
        filters, _ = params_to_filters(params)
        filter_fields = {f["field"] for f in filters}
        for db_field in ENUM_EQ_FIELDS.values():
            assert db_field in filter_fields


# ============================================================
# params_to_filters: 枚举值归一化（中文 → DB 值）
# ============================================================


class TestEnumNormalize:
    """枚举字段中文值归一化为 DB 实际值"""

    def test_order_type_chinese_to_code(self):
        """补发 → 14"""
        filters, _ = params_to_filters({"order_type": "补发"})
        ot = [f for f in filters if f["field"] == "order_type"]
        assert ot[0]["value"] == "14"

    def test_order_type_hebing(self):
        """合并 → 7"""
        filters, _ = params_to_filters({"order_type": "合并"})
        ot = [f for f in filters if f["field"] == "order_type"]
        assert ot[0]["value"] == "7"

    def test_aftersale_type_chinese(self):
        """退货退款 → 2"""
        filters, _ = params_to_filters({"aftersale_type": "退货退款"})
        at = [f for f in filters if f["field"] == "aftersale_type"]
        assert at[0]["value"] == "2"

    def test_aftersale_type_alias(self):
        """仅退款 → 1（别名）"""
        filters, _ = params_to_filters({"aftersale_type": "仅退款"})
        at = [f for f in filters if f["field"] == "aftersale_type"]
        assert at[0]["value"] == "1"

    def test_refund_status_chinese(self):
        """退款中 → 1"""
        filters, _ = params_to_filters({"refund_status": "退款中"})
        rs = [f for f in filters if f["field"] == "refund_status"]
        assert rs[0]["value"] == "1"

    def test_refund_status_success(self):
        """退款成功 → 2"""
        filters, _ = params_to_filters({"refund_status": "退款成功"})
        rs = [f for f in filters if f["field"] == "refund_status"]
        assert rs[0]["value"] == "2"

    def test_good_status_chinese(self):
        """买家已发 → 2"""
        filters, _ = params_to_filters({"good_status": "买家已发"})
        gs = [f for f in filters if f["field"] == "good_status"]
        assert gs[0]["value"] == "2"

    def test_good_status_alias(self):
        """买家已退货 → 2（别名）"""
        filters, _ = params_to_filters({"good_status": "买家已退货"})
        gs = [f for f in filters if f["field"] == "good_status"]
        assert gs[0]["value"] == "2"

    def test_doc_status_purchase_chinese(self):
        """待审核 → WAIT_VERIFY"""
        filters, _ = params_to_filters({"doc_status": "待审核"})
        ds = [f for f in filters if f["field"] == "doc_status"]
        assert ds[0]["value"] == "WAIT_VERIFY"

    def test_doc_status_purchase_finished(self):
        """已完成 → FINISHED"""
        filters, _ = params_to_filters({"doc_status": "已完成"})
        ds = [f for f in filters if f["field"] == "doc_status"]
        assert ds[0]["value"] == "FINISHED"

    def test_order_status_english_passthrough(self):
        """order_status 直接输英文枚举不归一化"""
        filters, _ = params_to_filters({"order_status": "WAIT_SEND_GOODS"})
        os = [f for f in filters if f["field"] == "order_status"]
        assert os[0]["value"] == "WAIT_SEND_GOODS"

    def test_unknown_enum_value_passthrough(self):
        """未知中文值保留原值（不崩溃）"""
        filters, _ = params_to_filters({"order_type": "未知类型XYZ"})
        ot = [f for f in filters if f["field"] == "order_type"]
        assert ot[0]["value"] == "未知类型XYZ"

    def test_online_status_chinese_to_code(self):
        """待卖家同意 → 2"""
        filters, _ = params_to_filters({"online_status": "待卖家同意"})
        os = [f for f in filters if f["field"] == "online_status"]
        assert os[0]["value"] == "2"

    def test_online_status_alias(self):
        """等待卖家同意 → 2（别名）"""
        filters, _ = params_to_filters({"online_status": "等待卖家同意"})
        os = [f for f in filters if f["field"] == "online_status"]
        assert os[0]["value"] == "2"

    def test_online_status_refund_success(self):
        """退款成功 → 7"""
        filters, _ = params_to_filters({"online_status": "退款成功"})
        os = [f for f in filters if f["field"] == "online_status"]
        assert os[0]["value"] == "7"

    def test_handler_status_pending(self):
        """待处理 → -1"""
        filters, _ = params_to_filters({"handler_status": "待处理"})
        hs = [f for f in filters if f["field"] == "handler_status"]
        assert hs[0]["value"] == "-1"

    def test_handler_status_alias(self):
        """已处理 → 1（别名）"""
        filters, _ = params_to_filters({"handler_status": "已处理"})
        hs = [f for f in filters if f["field"] == "handler_status"]
        assert hs[0]["value"] == "1"

    def test_handler_status_failed(self):
        """处理失败 → 2"""
        filters, _ = params_to_filters({"handler_status": "处理失败"})
        hs = [f for f in filters if f["field"] == "handler_status"]
        assert hs[0]["value"] == "2"


# ============================================================
# params_to_filters: 布尔标记类（eq 1）
# ============================================================


class TestFlagFields:

    @pytest.mark.parametrize("flag", FLAG_FIELDS)
    def test_flag_true_generates_eq_1(self, flag):
        filters, _ = params_to_filters({flag: True})
        assert any(
            f["field"] == flag and f["op"] == "eq" and f["value"] == 1
            for f in filters
        )

    @pytest.mark.parametrize("flag", FLAG_FIELDS)
    def test_flag_false_no_filter(self, flag):
        filters, _ = params_to_filters({flag: False})
        assert not any(f["field"] == flag for f in filters)

    @pytest.mark.parametrize("flag", FLAG_FIELDS)
    def test_flag_absent_no_filter(self, flag):
        filters, _ = params_to_filters({})
        assert not any(f["field"] == flag for f in filters)


# ============================================================
# params_to_filters: 组合场景
# ============================================================


class TestCombined:

    def test_multiple_new_fields_together(self):
        """多个新字段同时存在"""
        filters, _ = params_to_filters({
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
        filters, _ = params_to_filters({
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
        filters, warnings = params_to_filters({})
        assert filters == []
        assert warnings == []


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
        result = diagnose_error("统计查询超时")
        assert "超时" in result
        assert "时间范围" in result

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
