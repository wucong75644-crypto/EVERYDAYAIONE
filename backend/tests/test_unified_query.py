"""
统一查询引擎（Filter DSL）单元测试

覆盖：erp_unified_query.py / erp_unified_schema.py
设计文档: docs/document/TECH_统一查询引擎FilterDSL.md
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


CN_TZ = ZoneInfo("Asia/Shanghai")


# ── Schema 常量测试 ──────────────────────────────────


class TestColumnWhitelist:

    def test_has_core_fields(self):
        from services.kuaimai.erp_unified_schema import COLUMN_WHITELIST
        core = [
            "doc_type", "doc_id", "order_status", "doc_status",
            "outer_id", "amount", "quantity", "shop_name", "platform",
            "order_no", "express_no", "consign_time", "pay_time",
        ]
        for field in core:
            assert field in COLUMN_WHITELIST, f"缺少核心字段: {field}"

    def test_types_are_valid(self):
        from services.kuaimai.erp_unified_schema import COLUMN_WHITELIST
        valid_types = {"text", "integer", "numeric", "timestamp", "boolean"}
        for field, meta in COLUMN_WHITELIST.items():
            assert meta.col_type in valid_types, f"{field} 类型无效: {meta.col_type}"


class TestOpCompat:

    def test_text_supports_eq_like_in(self):
        from services.kuaimai.erp_unified_schema import OP_COMPAT
        text_ops = OP_COMPAT["text"]
        assert {"eq", "ne", "like", "in", "is_null"} == text_ops

    def test_numeric_supports_comparison(self):
        from services.kuaimai.erp_unified_schema import OP_COMPAT
        numeric_ops = OP_COMPAT["numeric"]
        for op in ("eq", "gt", "gte", "lt", "lte", "between"):
            assert op in numeric_ops

    def test_timestamp_supports_range(self):
        from services.kuaimai.erp_unified_schema import OP_COMPAT
        ts_ops = OP_COMPAT["timestamp"]
        for op in ("gte", "lt", "between"):
            assert op in ts_ops

    def test_text_rejects_gt(self):
        from services.kuaimai.erp_unified_schema import OP_COMPAT
        assert "gt" not in OP_COMPAT["text"]


class TestDefaultDetailFields:

    def test_all_doc_types_covered(self):
        from services.kuaimai.erp_unified_schema import (
            DEFAULT_DETAIL_FIELDS, VALID_DOC_TYPES,
        )
        for dt in VALID_DOC_TYPES:
            assert dt in DEFAULT_DETAIL_FIELDS, f"缺少 {dt} 的默认字段"

    def test_order_has_key_fields(self):
        from services.kuaimai.erp_unified_schema import DEFAULT_DETAIL_FIELDS
        order_fields = DEFAULT_DETAIL_FIELDS["order"]
        assert "order_no" in order_fields
        assert "amount" in order_fields
        assert "shop_name" in order_fields


# ── _validate_filters 测试 ───────────────────────────


class TestValidateFilters:

    def test_valid_eq_filter(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [{"field": "order_status", "op": "eq", "value": "FINISHED"}]
        result, err = _validate_filters(filters)
        assert err is None
        assert len(result) == 1
        assert result[0].field == "order_status"
        assert result[0].op == "eq"

    def test_invalid_field_returns_error(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [{"field": "hacked_field", "op": "eq", "value": "x"}]
        result, err = _validate_filters(filters)
        assert err is not None
        assert "不在白名单中" in err

    def test_incompatible_op_returns_error(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        # text 列不支持 gt
        filters = [{"field": "order_status", "op": "gt", "value": "x"}]
        result, err = _validate_filters(filters)
        assert err is not None
        assert "不支持" in err

    def test_between_requires_array_of_two(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [{"field": "amount", "op": "between", "value": 100}]
        result, err = _validate_filters(filters)
        assert err is not None
        assert "min, max" in err

    def test_between_valid(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [{"field": "amount", "op": "between", "value": [100, 500]}]
        result, err = _validate_filters(filters)
        assert err is None
        assert result[0].value == [100, 500]

    def test_empty_in_skipped(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [{"field": "platform", "op": "in", "value": []}]
        result, err = _validate_filters(filters)
        assert err is None
        assert len(result) == 0  # 空 in 被跳过

    def test_non_dict_items_skipped(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = ["not_a_dict", {"field": "amount", "op": "eq", "value": 100}]
        result, err = _validate_filters(filters)
        assert err is None
        assert len(result) == 1

    def test_empty_filters_ok(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        result, err = _validate_filters([])
        assert err is None
        assert len(result) == 0

    def test_coerce_string_to_int(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [{"field": "is_refund", "op": "eq", "value": "1"}]
        result, err = _validate_filters(filters)
        assert err is None
        assert result[0].value == 1

    def test_timestamp_auto_timezone(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [{"field": "consign_time", "op": "gte", "value": "2026-04-14 00:00:00"}]
        result, err = _validate_filters(filters)
        assert err is None
        assert "+08:00" in str(result[0].value)

    def test_multiple_filters(self):
        from services.kuaimai.erp_unified_query import _validate_filters
        filters = [
            {"field": "order_status", "op": "eq", "value": "SELLER_SEND_GOODS"},
            {"field": "platform", "op": "eq", "value": "tb"},
            {"field": "amount", "op": "gt", "value": 500},
        ]
        result, err = _validate_filters(filters)
        assert err is None
        assert len(result) == 3


# ── _extract_time_range 测试 ──────────────────────────


class TestExtractTimeRange:

    def _now(self):
        return datetime(2026, 4, 15, 10, 0, tzinfo=CN_TZ)

    def _ctx(self):
        from utils.time_context import RequestContext, TimePoint
        now = self._now()
        return RequestContext(
            now=now,
            today=TimePoint.from_datetime(now, reference=now),
            user_id="test", org_id="test", request_id="test",
        )

    def test_no_time_filters_summary_defaults_today(self):
        from services.kuaimai.erp_unified_query import _extract_time_range
        tr = _extract_time_range([], None, self._ctx(), "summary")
        assert "04-15" in tr.start_iso
        assert "04-15" in tr.end_iso

    def test_no_time_filters_detail_defaults_30_days(self):
        from services.kuaimai.erp_unified_query import _extract_time_range
        tr = _extract_time_range([], None, self._ctx(), "detail")
        # start 应该是 30 天前（3月16日左右）
        assert "03-16" in tr.start_iso or "03-17" in tr.start_iso

    def test_explicit_time_filters_used(self):
        from services.kuaimai.erp_unified_query import _extract_time_range
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [
            ValidatedFilter("consign_time", "gte", "2026-04-14 00:00:00+08:00", "timestamp"),
            ValidatedFilter("consign_time", "lt", "2026-04-15 00:00:00+08:00", "timestamp"),
        ]
        tr = _extract_time_range(filters, None, self._ctx(), "summary")
        assert "04-14" in tr.start_iso
        assert "04-15" in tr.end_iso
        assert tr.time_col == "consign_time"

    def test_time_type_param_overrides_default(self):
        from services.kuaimai.erp_unified_query import _extract_time_range
        tr = _extract_time_range([], "pay_time", self._ctx(), "summary")
        assert tr.time_col == "pay_time"

    def test_only_start_no_end_defaults_to_today_end(self):
        from services.kuaimai.erp_unified_query import _extract_time_range
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [
            ValidatedFilter("doc_created_at", "gte", "2026-04-10 00:00:00+08:00", "timestamp"),
        ]
        tr = _extract_time_range(filters, None, self._ctx(), "summary")
        assert "04-10" in tr.start_iso
        assert "04-15" in tr.end_iso  # 默认到今天结束

    def test_mixed_time_cols_only_takes_first(self):
        """多时间列冲突时只取同一列（Fix 3 验证）"""
        from services.kuaimai.erp_unified_query import _extract_time_range
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [
            ValidatedFilter("consign_time", "gte", "2026-04-14 00:00:00+08:00", "timestamp"),
            ValidatedFilter("pay_time", "lt", "2026-04-15 00:00:00+08:00", "timestamp"),
        ]
        tr = _extract_time_range(filters, None, self._ctx(), "summary")
        assert tr.time_col == "consign_time"
        # end_val 来自 pay_time 但 detected_col 是 consign_time → pay_time 条件被忽略
        # end 应该是默认值（今天）
        assert "04-15" in tr.end_iso


# ── _split_named_params 测试 ──────────────────────────


class TestSplitNamedParams:

    def test_shop_name_extracted(self):
        from services.kuaimai.erp_unified_query import _split_named_params
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [
            ValidatedFilter("shop_name", "eq", "蓝创旗舰店", "text"),
            ValidatedFilter("order_status", "eq", "FINISHED", "text"),
        ]
        shop, plat, sup, wh, dsl = _split_named_params(filters)
        assert shop == "蓝创旗舰店"
        assert plat is None
        assert len(dsl) == 1
        assert dsl[0]["field"] == "order_status"

    def test_platform_extracted(self):
        from services.kuaimai.erp_unified_query import _split_named_params
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [ValidatedFilter("platform", "eq", "tb", "text")]
        shop, plat, sup, wh, dsl = _split_named_params(filters)
        assert plat == "tb"
        assert len(dsl) == 0

    def test_amount_goes_to_dsl(self):
        from services.kuaimai.erp_unified_query import _split_named_params
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [ValidatedFilter("amount", "gt", 500, "numeric")]
        shop, plat, sup, wh, dsl = _split_named_params(filters)
        assert len(dsl) == 1
        assert dsl[0]["field"] == "amount"


# ── _need_archive 测试 ────────────────────────────────


class TestNeedArchive:

    def test_within_90_days_no_archive(self):
        from services.kuaimai.erp_unified_query import _need_archive
        from services.kuaimai.erp_unified_schema import TimeRange
        now = datetime.now(CN_TZ)
        tr = TimeRange(
            start_iso=(now - timedelta(days=30)).isoformat(),
            end_iso=now.isoformat(),
            time_col="doc_created_at",
            date_range=None, label="",
        )
        assert _need_archive(tr) is False

    def test_beyond_90_days_needs_archive(self):
        from services.kuaimai.erp_unified_query import _need_archive
        from services.kuaimai.erp_unified_schema import TimeRange
        now = datetime.now(CN_TZ)
        tr = TimeRange(
            start_iso=(now - timedelta(days=120)).isoformat(),
            end_iso=now.isoformat(),
            time_col="doc_created_at",
            date_range=None, label="",
        )
        assert _need_archive(tr) is True


# ── _apply_orm_filters 测试 ──────────────────────────


class TestApplyOrmFilters:

    def test_eq_calls_eq(self):
        from services.kuaimai.erp_unified_query import _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.eq.return_value = mock_q
        filters = [ValidatedFilter("status", "eq", "FINISHED", "text")]
        result = _apply_orm_filters(mock_q, filters)
        mock_q.eq.assert_called_once_with("status", "FINISHED")

    def test_gt_calls_gt(self):
        from services.kuaimai.erp_unified_query import _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.gt.return_value = mock_q
        filters = [ValidatedFilter("amount", "gt", 500, "numeric")]
        _apply_orm_filters(mock_q, filters)
        mock_q.gt.assert_called_once_with("amount", 500)

    def test_like_calls_ilike(self):
        from services.kuaimai.erp_unified_query import _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.ilike.return_value = mock_q
        filters = [ValidatedFilter("shop_name", "like", "%蓝创%", "text")]
        _apply_orm_filters(mock_q, filters)
        mock_q.ilike.assert_called_once_with("shop_name", "%蓝创%")

    def test_in_calls_in_(self):
        from services.kuaimai.erp_unified_query import _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.in_.return_value = mock_q
        filters = [ValidatedFilter("platform", "in", ["tb", "jd"], "text")]
        _apply_orm_filters(mock_q, filters)
        mock_q.in_.assert_called_once_with("platform", ["tb", "jd"])

    def test_between_calls_gte_lte(self):
        from services.kuaimai.erp_unified_query import _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.gte.return_value = mock_q
        mock_q.lte.return_value = mock_q
        filters = [ValidatedFilter("amount", "between", [100, 500], "numeric")]
        _apply_orm_filters(mock_q, filters)
        mock_q.gte.assert_called_once_with("amount", 100)
        mock_q.lte.assert_called_once_with("amount", 500)


# ── 格式化函数测试 ───────────────────────────────────


class TestFormatFunctions:

    def test_fmt_detail_rows_basic(self):
        from services.kuaimai.erp_unified_schema import fmt_detail_rows
        rows = [
            {"order_no": "TB123", "amount": 100},
            {"order_no": "TB456", "amount": 200},
        ]
        result = fmt_detail_rows(rows, ["order_no", "amount"], "订单", 20)
        assert "共2条" in result
        assert "TB123" in result
        assert "TB456" in result

    def test_fmt_detail_rows_truncation_hint(self):
        from services.kuaimai.erp_unified_schema import fmt_detail_rows
        rows = [{"order_no": f"TB{i}"} for i in range(5)]
        result = fmt_detail_rows(rows, ["order_no"], "订单", 5)
        assert "mode=export" in result

    def test_fmt_summary_total(self):
        from services.kuaimai.erp_unified_schema import fmt_summary_total
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = []
        data = {"doc_count": 10, "total_qty": 50, "total_amount": 9999.5}
        result = fmt_summary_total(data, "订单", "2026-04-15", mock_db, "order", None)
        assert "10笔" in result
        assert "50件" in result
        assert "9,999.50" in result

    def test_fmt_summary_grouped(self):
        from services.kuaimai.erp_unified_schema import fmt_summary_grouped
        data = [
            {"group_key": "tb", "doc_count": 5, "total_qty": 20, "total_amount": 3000},
            {"group_key": "jd", "doc_count": 3, "total_qty": 10, "total_amount": 2000},
        ]
        result = fmt_summary_grouped(data, "platform", "订单", "今天")
        assert "tb" in result
        assert "jd" in result
        assert "8笔" in result  # 总计

    def test_generate_field_doc(self):
        from services.kuaimai.erp_unified_schema import generate_field_doc
        doc = generate_field_doc("order")
        assert "doc_type=order" in doc
        assert "order_no" in doc
        assert "示例" in doc


# ── mask_pii 测试 ─────────────────────────────────────


class TestMaskPii:

    def test_mask_phone(self):
        from services.kuaimai.erp_unified_schema import mask_pii
        row = {"receiver_mobile": "13812345678"}
        mask_pii(row)
        assert row["receiver_mobile"] == "138****5678"

    def test_mask_name(self):
        from services.kuaimai.erp_unified_schema import mask_pii
        row = {"receiver_name": "张三丰"}
        mask_pii(row)
        assert row["receiver_name"] == "张**"

    def test_short_phone_not_masked(self):
        from services.kuaimai.erp_unified_schema import mask_pii
        row = {"receiver_mobile": "123"}
        mask_pii(row)
        assert row["receiver_mobile"] == "123"

    def test_no_pii_fields_unchanged(self):
        from services.kuaimai.erp_unified_schema import mask_pii
        row = {"order_no": "TB123", "amount": 100}
        mask_pii(row)
        assert row == {"order_no": "TB123", "amount": 100}


# ── UnifiedQueryEngine.execute 入口测试 ───────────────


class TestExecuteEntryPoint:

    @pytest.mark.asyncio
    async def test_invalid_doc_type(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        engine = UnifiedQueryEngine(db=MagicMock(), org_id=None)
        result = await engine.execute("invalid_type", "summary", [])
        assert "无效的 doc_type" in result

    @pytest.mark.asyncio
    async def test_invalid_mode_defaults_to_summary(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        mock_db.rpc.return_value = mock_rpc
        mock_db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = []

        engine = UnifiedQueryEngine(db=mock_db, org_id=None)
        result = await engine.execute("order", "invalid_mode", [])
        # 应该走 summary 模式
        mock_db.rpc.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_filter_field_returns_error(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        engine = UnifiedQueryEngine(db=MagicMock(), org_id=None)
        result = await engine.execute(
            "order", "detail",
            [{"field": "nonexistent", "op": "eq", "value": "x"}],
        )
        assert "不在白名单中" in result
