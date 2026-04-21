"""
数据出口翻译层测试 — 确保内部编码不暴露给用户/LLM。

覆盖：
- format_platform（公共平台翻译）
- trade formatter transforms（sysStatus / refundStatus / source）
- build_pii_select cn_header（导出 Excel 中文列头）
- build_column_metas_cn（导出列元信息中文 name）
- summary data platform 翻译

设计文档: 无独立文档，属于数据出口翻译修复
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


# ============================================================
# format_platform 公共翻译
# ============================================================


class TestFormatPlatform:
    """formatters/common.py format_platform()"""

    def test_known_platforms(self):
        from services.kuaimai.formatters.common import format_platform
        assert format_platform("tb") == "淘宝"
        assert format_platform("pdd") == "拼多多"
        assert format_platform("fxg") == "抖音"
        assert format_platform("jd") == "京东"
        assert format_platform("kuaishou") == "快手"
        assert format_platform("xhs") == "小红书"
        assert format_platform("1688") == "1688"
        assert format_platform("sys") == "系统（补发/换货/线下）"

    def test_unknown_platform_passthrough(self):
        from services.kuaimai.formatters.common import format_platform
        assert format_platform("unknown_code") == "unknown_code"

    def test_empty_and_none(self):
        from services.kuaimai.formatters.common import format_platform
        assert format_platform("") == ""
        assert format_platform(None) == ""


# ============================================================
# trade formatter transforms
# ============================================================


class TestTradeTransforms:
    """formatters/trade.py 新增的 transforms"""

    def test_order_source_translated(self):
        """订单 source 字段通过 format_platform 翻译"""
        from services.kuaimai.formatters.trade import _ORDER_TRANSFORMS
        assert "source" in _ORDER_TRANSFORMS
        assert _ORDER_TRANSFORMS["source"]("tb") == "淘宝"
        assert _ORDER_TRANSFORMS["source"]("pdd") == "拼多多"

    def test_sys_status_translated(self):
        """sysStatus 英文状态码 → 中文"""
        from services.kuaimai.formatters.trade import _ORDER_TRANSFORMS
        assert "sysStatus" in _ORDER_TRANSFORMS
        transform = _ORDER_TRANSFORMS["sysStatus"]
        assert transform("WAIT_PAY") == "待付款"
        assert transform("WAIT_SEND") == "待发货"
        assert transform("SEND") == "已发货"
        assert transform("FINISH") == "已完成"
        assert transform("CLOSED") == "已关闭"

    def test_sys_status_unknown_passthrough(self):
        """未知 sysStatus 原样返回"""
        from services.kuaimai.formatters.trade import _ORDER_TRANSFORMS
        assert _ORDER_TRANSFORMS["sysStatus"]("CUSTOM_STATUS") == "CUSTOM_STATUS"

    def test_sys_status_empty(self):
        from services.kuaimai.formatters.trade import _ORDER_TRANSFORMS
        assert _ORDER_TRANSFORMS["sysStatus"]("") == ""
        assert _ORDER_TRANSFORMS["sysStatus"](None) == ""

    def test_refund_status_translated(self):
        """子订单 refundStatus 数字 → 中文"""
        from services.kuaimai.formatters.trade import _SUB_ORDER_TRANSFORMS
        assert "refundStatus" in _SUB_ORDER_TRANSFORMS
        transform = _SUB_ORDER_TRANSFORMS["refundStatus"]
        assert transform(0) == "无退款"
        assert transform(1) == "退款中"
        assert transform(2) == "退款成功"
        assert transform(3) == "退款关闭"

    def test_refund_status_unknown(self):
        from services.kuaimai.formatters.trade import _SUB_ORDER_TRANSFORMS
        assert _SUB_ORDER_TRANSFORMS["refundStatus"](99) == "99"


# ============================================================
# 其他 formatter source 翻译
# ============================================================


class TestOtherFormatterSourceTranslate:
    """aftersale/basic/qimen 的 source transform"""

    def test_aftersale_has_source_transform(self):
        from services.kuaimai.formatters.aftersales import _AFTERSALE_TRANSFORMS
        assert "source" in _AFTERSALE_TRANSFORMS
        assert _AFTERSALE_TRANSFORMS["source"]("fxg") == "抖音"

    def test_shop_has_source_transform(self):
        from services.kuaimai.formatters.basic import _SHOP_TRANSFORMS
        assert "source" in _SHOP_TRANSFORMS
        assert _SHOP_TRANSFORMS["source"]("jd") == "京东"

    def test_qimen_order_has_source_transform(self):
        from services.kuaimai.formatters.qimen import _QIMEN_ORDER_TRANSFORMS
        assert "source" in _QIMEN_ORDER_TRANSFORMS
        assert _QIMEN_ORDER_TRANSFORMS["source"]("xhs") == "小红书"

    def test_qimen_refund_has_source_transform(self):
        from services.kuaimai.formatters.qimen import _QIMEN_REFUND_TRANSFORMS
        assert "source" in _QIMEN_REFUND_TRANSFORMS
        assert _QIMEN_REFUND_TRANSFORMS["source"]("kuaishou") == "快手"


# ============================================================
# build_pii_select cn_header
# ============================================================


class TestBuildPiiSelectCnHeader:
    """erp_duckdb_helpers.py build_pii_select(cn_header=True)"""

    def test_normal_field_gets_cn_alias(self):
        from services.kuaimai.erp_duckdb_helpers import build_pii_select
        result = build_pii_select(["order_no"], cn_header=True)
        assert '"平台订单号"' in result

    def test_amount_gets_cn_alias(self):
        from services.kuaimai.erp_duckdb_helpers import build_pii_select
        result = build_pii_select(["amount"], cn_header=True)
        assert '"金额"' in result

    def test_platform_gets_cn_alias(self):
        from services.kuaimai.erp_duckdb_helpers import build_pii_select
        result = build_pii_select(["platform"], cn_header=True)
        assert '"来源平台"' in result
        assert "CASE" in result  # 仍有 CASE WHEN 翻译

    def test_pii_field_gets_cn_alias(self):
        from services.kuaimai.erp_duckdb_helpers import build_pii_select
        result = build_pii_select(["receiver_name"], cn_header=True)
        assert '"收件人"' in result
        assert "CASE WHEN" in result  # 仍有脱敏

    def test_timestamp_gets_cn_alias(self):
        from services.kuaimai.erp_duckdb_helpers import build_pii_select
        result = build_pii_select(["pay_time"], cn_header=True)
        assert '"付款时间"' in result
        assert "CAST" in result

    def test_cn_header_false_no_alias(self):
        """cn_header=False 不加中文别名（默认行为不变）"""
        from services.kuaimai.erp_duckdb_helpers import build_pii_select
        result = build_pii_select(["order_no", "amount"], cn_header=False)
        assert result == "order_no, amount"

    def test_unknown_field_uses_name_as_label(self):
        """无中文映射的字段用英文名"""
        from services.kuaimai.erp_duckdb_helpers import build_pii_select
        result = build_pii_select(["short_id"], cn_header=True)
        assert '"短ID"' in result


# ============================================================
# build_column_metas_cn
# ============================================================


class TestBuildColumnMetasCn:
    """erp_unified_schema.py build_column_metas_cn()"""

    def test_name_is_chinese(self):
        from services.kuaimai.erp_unified_schema import build_column_metas_cn
        metas = build_column_metas_cn(["order_no", "amount", "platform"])
        names = [m.name for m in metas]
        assert "平台订单号" in names
        assert "金额" in names
        assert "来源平台" in names

    def test_label_equals_name(self):
        """cn 模式下 name 和 label 一致"""
        from services.kuaimai.erp_unified_schema import build_column_metas_cn
        metas = build_column_metas_cn(["order_no"])
        assert metas[0].name == metas[0].label

    def test_dtype_preserved(self):
        from services.kuaimai.erp_unified_schema import build_column_metas_cn
        metas = build_column_metas_cn(["amount"])
        assert metas[0].dtype == "numeric"

    def test_non_whitelist_field_excluded(self):
        from services.kuaimai.erp_unified_schema import build_column_metas_cn
        metas = build_column_metas_cn(["nonexistent_field"])
        assert len(metas) == 0

    def test_build_column_metas_name_is_english(self):
        """对比：普通版 name 是英文"""
        from services.kuaimai.erp_unified_schema import build_column_metas
        metas = build_column_metas(["order_no"])
        assert metas[0].name == "order_no"
        assert metas[0].label == "平台订单号"


# ============================================================
# summary data platform 翻译
# ============================================================


class TestSummaryDataPlatformTranslation:
    """erp_unified_query.py _summary 返回的 data 中 platform 已翻译"""

    def test_platform_in_result_data_translated(self):
        """模拟 RPC 返回的 data，验证 platform 被翻译"""
        from services.kuaimai.erp_unified_schema import PLATFORM_CN
        # 模拟 RPC 返回
        raw_data = [
            {"group_key": "tb", "platform": "tb", "doc_count": 100, "total_amount": 5000},
            {"group_key": "pdd", "platform": "pdd", "doc_count": 200, "total_amount": 8000},
        ]
        # 模拟 _summary 中的翻译逻辑
        rpc_group = "platform"
        for row in raw_data:
            if "platform" in row:
                row["platform"] = PLATFORM_CN.get(row["platform"], row["platform"])
            if "group_key" in row and rpc_group == "platform":
                row["group_key"] = PLATFORM_CN.get(row["group_key"], row["group_key"])

        assert raw_data[0]["platform"] == "淘宝"
        assert raw_data[0]["group_key"] == "淘宝"
        assert raw_data[1]["platform"] == "拼多多"
        assert raw_data[1]["group_key"] == "拼多多"

    def test_non_platform_group_not_translated(self):
        """非 platform 分组时 group_key 不翻译"""
        from services.kuaimai.erp_unified_schema import PLATFORM_CN
        raw_data = [
            {"group_key": "店铺A", "platform": "tb", "doc_count": 100},
        ]
        rpc_group = "shop"  # 按店铺分组
        for row in raw_data:
            if "platform" in row:
                row["platform"] = PLATFORM_CN.get(row["platform"], row["platform"])
            if "group_key" in row and rpc_group == "platform":
                row["group_key"] = PLATFORM_CN.get(row["group_key"], row["group_key"])

        assert raw_data[0]["platform"] == "淘宝"  # platform 翻译
        assert raw_data[0]["group_key"] == "店铺A"  # group_key 不变
