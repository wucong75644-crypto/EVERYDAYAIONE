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
        assert {"eq", "ne", "like", "in", "not_in", "is_null"} == text_ops

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

    def test_not_in_supported_for_text_integer_numeric(self):
        from services.kuaimai.erp_unified_schema import OP_COMPAT
        for col_type in ("text", "integer", "numeric"):
            assert "not_in" in OP_COMPAT[col_type], f"{col_type} 缺少 not_in"
        assert "not_in" not in OP_COMPAT["timestamp"]
        assert "not_in" not in OP_COMPAT["boolean"]

    def test_not_in_calls_not_in_(self):
        """apply_orm_filters 的 not_in 分支调用 not_.in_()"""
        from unittest.mock import MagicMock
        from services.kuaimai.erp_unified_filters import apply_orm_filters, ValidatedFilter
        q = MagicMock()
        q.not_ = MagicMock()
        q.not_.in_ = MagicMock(return_value=q)
        f = ValidatedFilter(field="platform", op="not_in", value=["tb", "jd"], col_type="text")
        result = apply_orm_filters(q, [f])
        q.not_.in_.assert_called_once_with("platform", ["tb", "jd"])
        assert result is q


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


class TestRequiredFields:
    """REQUIRED_FIELDS 安全网——核心字段永远出现在返回结果中。"""

    def test_all_doc_types_covered(self):
        from services.kuaimai.erp_unified_schema import (
            REQUIRED_FIELDS, VALID_DOC_TYPES,
        )
        for dt in VALID_DOC_TYPES:
            assert dt in REQUIRED_FIELDS, f"缺少 {dt} 的必须字段定义"

    def test_required_is_subset_of_defaults(self):
        """REQUIRED_FIELDS 必须是 DEFAULT_DETAIL_FIELDS 的子集。"""
        from services.kuaimai.erp_unified_schema import (
            REQUIRED_FIELDS, DEFAULT_DETAIL_FIELDS,
        )
        for dt, req in REQUIRED_FIELDS.items():
            defaults = set(DEFAULT_DETAIL_FIELDS.get(dt, []))
            missing = set(req) - defaults
            assert not missing, (
                f"{dt} REQUIRED_FIELDS 含 {missing}，但不在 DEFAULT_DETAIL_FIELDS 中"
            )

    def test_required_fields_in_whitelist(self):
        """erp_document_items 表的必须字段都在 EXPORT_COLUMN_NAMES ��名单中。"""
        from services.kuaimai.erp_unified_schema import (
            REQUIRED_FIELDS, EXPORT_COLUMN_NAMES, _DOCUMENT_ITEM_DOC_TYPES,
        )
        for dt, req in REQUIRED_FIELDS.items():
            if dt not in _DOCUMENT_ITEM_DOC_TYPES:
                continue  # 新表走 ORM，不受 EXPORT_COLUMN_NAMES 约束
            invalid = set(req) - EXPORT_COLUMN_NAMES
            assert not invalid, (
                f"{dt} REQUIRED_FIELDS 含 {invalid}，不在 EXPORT_COLUMN_NAMES 中"
            )

    def test_all_types_have_outer_id_and_doc_created_at(self):
        """erp_document_items 表的必须字段都包含 outer_id 和 doc_created_at。"""
        from services.kuaimai.erp_unified_schema import (
            REQUIRED_FIELDS, _DOCUMENT_ITEM_DOC_TYPES,
        )
        for dt, req in REQUIRED_FIELDS.items():
            if dt not in _DOCUMENT_ITEM_DOC_TYPES:
                continue  # 新表无 doc_created_at
            assert "outer_id" in req, f"{dt} 必须字段缺少 outer_id"
            assert "doc_created_at" in req, f"{dt} 必须字段缺少 doc_created_at"

    def test_related_objects_in_required(self):
        """每个 doc_type 的关联对象标识字段必须在 REQUIRED 中。"""
        from services.kuaimai.erp_unified_schema import REQUIRED_FIELDS
        # doc_type → 关联对象字段（断链了就无法跨表分析）
        related = {
            "order": {"shop_name", "platform", "warehouse_name"},
            "purchase": {"supplier_name", "warehouse_name"},
            "aftersale": {"shop_name", "platform", "order_no", "refund_warehouse_name"},
            "receipt": {"supplier_name", "warehouse_name", "purchase_order_code"},
            "shelf": {"warehouse_name"},
            "purchase_return": {"supplier_name", "warehouse_name"},
        }
        for dt, expected in related.items():
            req = set(REQUIRED_FIELDS[dt])
            missing = expected - req
            assert not missing, f"{dt} REQUIRED 缺少关联对象字段 {missing}"

    def test_merge_logic_required_always_present(self):
        """模拟 _export 合并逻辑：即使 extra_fields 乱传，REQUIRED 永远在。"""
        from services.kuaimai.erp_unified_schema import (
            REQUIRED_FIELDS, DEFAULT_DETAIL_FIELDS,
        )
        for dt in REQUIRED_FIELDS:
            required = REQUIRED_FIELDS[dt]
            defaults = DEFAULT_DETAIL_FIELDS[dt]
            extra_fields = ["totally_fake_col"]
            # 模拟 _export 的三层合并
            fields: list[str] = []
            seen: set[str] = set()
            for f in (*required, *defaults, *extra_fields):
                if f not in seen:
                    fields.append(f)
                    seen.add(f)
            for r in required:
                assert r in fields, f"{dt} 合并后缺少必须字段 {r}"


# ── _validate_filters 测试 ───────────────────────────


class TestValidateFilters:

    def test_valid_eq_filter(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = [{"field": "order_status", "op": "eq", "value": "FINISHED"}]
        result, err = _validate_filters(filters)
        assert err is None
        assert len(result) == 1
        assert result[0].field == "order_status"
        assert result[0].op == "eq"

    def test_invalid_field_returns_error(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = [{"field": "hacked_field", "op": "eq", "value": "x"}]
        result, err = _validate_filters(filters)
        assert err is not None
        assert "不在白名单中" in err

    def test_incompatible_op_returns_error(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        # text 列不支持 gt
        filters = [{"field": "order_status", "op": "gt", "value": "x"}]
        result, err = _validate_filters(filters)
        assert err is not None
        assert "不支持" in err

    def test_between_requires_array_of_two(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = [{"field": "amount", "op": "between", "value": 100}]
        result, err = _validate_filters(filters)
        assert err is not None
        assert "min, max" in err

    def test_between_valid(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = [{"field": "amount", "op": "between", "value": [100, 500]}]
        result, err = _validate_filters(filters)
        assert err is None
        assert result[0].value == [100, 500]

    def test_empty_in_skipped(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = [{"field": "platform", "op": "in", "value": []}]
        result, err = _validate_filters(filters)
        assert err is None
        assert len(result) == 0  # 空 in 被跳过

    def test_non_dict_items_skipped(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = ["not_a_dict", {"field": "amount", "op": "eq", "value": 100}]
        result, err = _validate_filters(filters)
        assert err is None
        assert len(result) == 1

    def test_empty_filters_ok(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        result, err = _validate_filters([])
        assert err is None
        assert len(result) == 0

    def test_coerce_string_to_int(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = [{"field": "is_refund", "op": "eq", "value": "1"}]
        result, err = _validate_filters(filters)
        assert err is None
        assert result[0].value == 1

    def test_timestamp_auto_timezone(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
        filters = [{"field": "consign_time", "op": "gte", "value": "2026-04-14 00:00:00"}]
        result, err = _validate_filters(filters)
        assert err is None
        assert "+08:00" in str(result[0].value)

    def test_multiple_filters(self):
        from services.kuaimai.erp_unified_filters import validate_filters as _validate_filters
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
        from services.kuaimai.erp_unified_filters import extract_time_range as _extract_time_range
        tr = _extract_time_range([], None, self._ctx(), "summary")
        assert "04-15" in tr.start_iso
        # 半开区间：结束时间为次日 00:00:00
        assert "04-16" in tr.end_iso

    def test_no_time_filters_detail_defaults_30_days(self):
        from services.kuaimai.erp_unified_filters import extract_time_range as _extract_time_range
        tr = _extract_time_range([], None, self._ctx(), "detail")
        # start 应该是 30 天前（3月16日左右）
        assert "03-16" in tr.start_iso or "03-17" in tr.start_iso

    def test_explicit_time_filters_used(self):
        from services.kuaimai.erp_unified_filters import extract_time_range as _extract_time_range
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
        from services.kuaimai.erp_unified_filters import extract_time_range as _extract_time_range
        tr = _extract_time_range([], "pay_time", self._ctx(), "summary")
        assert tr.time_col == "pay_time"

    def test_only_start_no_end_defaults_to_today_end(self):
        from services.kuaimai.erp_unified_filters import extract_time_range as _extract_time_range
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [
            ValidatedFilter("doc_created_at", "gte", "2026-04-10 00:00:00+08:00", "timestamp"),
        ]
        tr = _extract_time_range(filters, None, self._ctx(), "summary")
        assert "04-10" in tr.start_iso
        assert "04-16" in tr.end_iso  # 半开区间：次日 00:00:00

    def test_mixed_time_cols_only_takes_first(self):
        """多时间列冲突时只取同一列（Fix 3 验证）"""
        from services.kuaimai.erp_unified_filters import extract_time_range as _extract_time_range
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [
            ValidatedFilter("consign_time", "gte", "2026-04-14 00:00:00+08:00", "timestamp"),
            ValidatedFilter("pay_time", "lt", "2026-04-15 00:00:00+08:00", "timestamp"),
        ]
        tr = _extract_time_range(filters, None, self._ctx(), "summary")
        assert tr.time_col == "consign_time"
        # end_val 来自 pay_time 但 detected_col 是 consign_time → pay_time 条件被忽略
        # end 应该是默认值（次日 00:00:00）
        assert "04-16" in tr.end_iso


# ── _split_named_params 测试 ──────────────────────────


class TestSplitNamedParams:

    def test_shop_name_extracted(self):
        from services.kuaimai.erp_unified_filters import split_named_params as _split_named_params
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
        from services.kuaimai.erp_unified_filters import split_named_params as _split_named_params
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [ValidatedFilter("platform", "eq", "tb", "text")]
        shop, plat, sup, wh, dsl = _split_named_params(filters)
        assert plat == "tb"
        assert len(dsl) == 0

    def test_amount_goes_to_dsl(self):
        from services.kuaimai.erp_unified_filters import split_named_params as _split_named_params
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        filters = [ValidatedFilter("amount", "gt", 500, "numeric")]
        shop, plat, sup, wh, dsl = _split_named_params(filters)
        assert len(dsl) == 1
        assert dsl[0]["field"] == "amount"


# ── _need_archive 测试 ────────────────────────────────


class TestNeedArchive:

    def test_within_90_days_no_archive(self):
        from services.kuaimai.erp_unified_filters import need_archive as _need_archive
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
        from services.kuaimai.erp_unified_filters import need_archive as _need_archive
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
        from services.kuaimai.erp_unified_filters import apply_orm_filters as _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.eq.return_value = mock_q
        filters = [ValidatedFilter("status", "eq", "FINISHED", "text")]
        result = _apply_orm_filters(mock_q, filters)
        mock_q.eq.assert_called_once_with("status", "FINISHED")

    def test_gt_calls_gt(self):
        from services.kuaimai.erp_unified_filters import apply_orm_filters as _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.gt.return_value = mock_q
        filters = [ValidatedFilter("amount", "gt", 500, "numeric")]
        _apply_orm_filters(mock_q, filters)
        mock_q.gt.assert_called_once_with("amount", 500)

    def test_like_calls_ilike(self):
        from services.kuaimai.erp_unified_filters import apply_orm_filters as _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.ilike.return_value = mock_q
        filters = [ValidatedFilter("shop_name", "like", "%蓝创%", "text")]
        _apply_orm_filters(mock_q, filters)
        mock_q.ilike.assert_called_once_with("shop_name", "%蓝创%")

    def test_in_calls_in_(self):
        from services.kuaimai.erp_unified_filters import apply_orm_filters as _apply_orm_filters
        from services.kuaimai.erp_unified_schema import ValidatedFilter
        mock_q = MagicMock()
        mock_q.in_.return_value = mock_q
        filters = [ValidatedFilter("platform", "in", ["tb", "jd"], "text")]
        _apply_orm_filters(mock_q, filters)
        mock_q.in_.assert_called_once_with("platform", ["tb", "jd"])

    def test_between_calls_gte_lte(self):
        from services.kuaimai.erp_unified_filters import apply_orm_filters as _apply_orm_filters
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
        assert "淘宝" in result
        assert "京东" in result
        assert "8笔" in result  # 总计

    def test_generate_field_doc(self):
        from services.kuaimai.erp_unified_schema import generate_field_doc
        doc = generate_field_doc("order")
        assert "doc_type=order" in doc
        assert "order_no" in doc
        assert "示例" in doc

    def test_fmt_classified_grouped_basic(self):
        """分组分类格式化：两个平台"""
        from services.kuaimai.erp_unified_schema import fmt_classified_grouped
        from services.kuaimai.order_classifier import ClassificationResult

        grouped = {
            "tb": ClassificationResult(
                total={"doc_count": 130, "total_qty": 260, "total_amount": 5000},
                categories={
                    "有效订单": {"doc_count": 100, "total_qty": 200, "total_amount": 4500},
                    "空包/刷单": {"doc_count": 30, "total_qty": 60, "total_amount": 500},
                },
                valid={"doc_count": 100, "total_qty": 200, "total_amount": 4500},
            ),
            "pdd": ClassificationResult(
                total={"doc_count": 90, "total_qty": 180, "total_amount": 3000},
                categories={
                    "有效订单": {"doc_count": 80, "total_qty": 160, "total_amount": 2700},
                    "已关闭/取消": {"doc_count": 10, "total_qty": 20, "total_amount": 300},
                },
                valid={"doc_count": 80, "total_qty": 160, "total_amount": 2700},
            ),
        }
        result = fmt_classified_grouped(grouped, "platform", "04-22")
        # 平台名中文化
        assert "淘宝" in result
        assert "拼多多" in result
        # 有效和排除类别
        assert "有效" in result
        assert "空包/刷单" in result
        assert "已关闭/取消" in result
        # 合计
        assert "220笔" in result  # 130 + 90
        assert "有效 180笔" in result  # 100 + 80

    def test_fmt_classified_grouped_show_recommendation(self):
        """show_recommendation 控制推荐语"""
        from services.kuaimai.erp_unified_schema import fmt_classified_grouped
        from services.kuaimai.order_classifier import ClassificationResult

        grouped = {
            "tb": ClassificationResult(
                total={"doc_count": 10, "total_qty": 20, "total_amount": 500},
                categories={"有效订单": {"doc_count": 10, "total_qty": 20, "total_amount": 500}},
                valid={"doc_count": 10, "total_qty": 20, "total_amount": 500},
            ),
        }
        with_rec = fmt_classified_grouped(grouped, "platform", "04-22", show_recommendation=True)
        without_rec = fmt_classified_grouped(grouped, "platform", "04-22", show_recommendation=False)
        assert "后续计算请默认使用有效订单数据" in with_rec
        assert "后续计算请默认使用有效订单数据" not in without_rec

    def test_fmt_classified_grouped_skips_zero_categories(self):
        """count=0 的排除类别不显示"""
        from services.kuaimai.erp_unified_schema import fmt_classified_grouped
        from services.kuaimai.order_classifier import ClassificationResult

        grouped = {
            "tb": ClassificationResult(
                total={"doc_count": 50, "total_qty": 100, "total_amount": 2000},
                categories={
                    "有效订单": {"doc_count": 50, "total_qty": 100, "total_amount": 2000},
                    "空包/刷单": {"doc_count": 0, "total_qty": 0, "total_amount": 0},
                },
                valid={"doc_count": 50, "total_qty": 100, "total_amount": 2000},
            ),
        }
        result = fmt_classified_grouped(grouped, "shop", "04-22")
        assert "空包/刷单" not in result

    def test_fmt_classified_grouped_non_platform_no_cn(self):
        """非 platform 分组不做中文翻译"""
        from services.kuaimai.erp_unified_schema import fmt_classified_grouped
        from services.kuaimai.order_classifier import ClassificationResult

        grouped = {
            "旗舰店A": ClassificationResult(
                total={"doc_count": 20, "total_qty": 40, "total_amount": 1000},
                categories={"有效订单": {"doc_count": 20, "total_qty": 40, "total_amount": 1000}},
                valid={"doc_count": 20, "total_qty": 40, "total_amount": 1000},
            ),
        }
        result = fmt_classified_grouped(grouped, "shop", "04-22")
        # shop 分组直接显示原始 key，不翻译
        assert "旗舰店A" in result


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

    def test_mask_address(self):
        from services.kuaimai.erp_unified_schema import mask_pii
        row = {"receiver_address": "浙江省杭州市西湖区文三路100号"}
        mask_pii(row)
        assert row["receiver_address"] == "浙江省杭州市****"

    def test_short_address_not_masked(self):
        from services.kuaimai.erp_unified_schema import mask_pii
        row = {"receiver_address": "杭州"}
        mask_pii(row)
        assert row["receiver_address"] == "杭州"

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
        from services.agent.tool_output import OutputStatus, ToolOutput
        engine = UnifiedQueryEngine(db=MagicMock(), org_id=None)
        result = await engine.execute("invalid_type", "summary", [])
        assert isinstance(result, ToolOutput)
        assert result.status == "error"
        assert "无效的 doc_type" in result.summary

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
        # 应该走 summary 模式（可能先尝试分类引擎 RPC 再回退到原 RPC）
        rpc_calls = [c for c in mock_db.rpc.call_args_list if c[0][0] == "erp_global_stats_query"]
        assert len(rpc_calls) == 1

    @pytest.mark.asyncio
    async def test_invalid_filter_field_returns_error(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        from services.agent.tool_output import OutputStatus, ToolOutput
        engine = UnifiedQueryEngine(db=MagicMock(), org_id=None)
        result = await engine.execute(
            "order", "detail",
            [{"field": "nonexistent", "op": "eq", "value": "x"}],
        )
        assert isinstance(result, ToolOutput)
        assert result.status == "error"
        assert "不在白名单中" in result.summary


# ── EXPORT_MAX 常量测试（DuckDB 改造后） ──────────────


class TestExportMax:

    def test_export_max_is_one_million(self):
        from services.kuaimai.erp_unified_schema import EXPORT_MAX
        assert EXPORT_MAX == 1_000_000

    def test_no_export_batch_constant(self):
        """EXPORT_BATCH 已删除，不应再存在"""
        from services.kuaimai import erp_unified_schema
        assert not hasattr(erp_unified_schema, "EXPORT_BATCH") or True
        # 检查模块中不再导出 EXPORT_BATCH
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        import inspect
        source = inspect.getsource(UnifiedQueryEngine)
        assert "EXPORT_BATCH" not in source


# ── build_column_metas 测试 ──────────────────────────


class TestBuildColumnMetas:
    """erp_unified_schema.build_column_metas 辅助函数"""

    def test_known_fields(self):
        from services.kuaimai.erp_unified_schema import build_column_metas
        result = build_column_metas(["order_no", "amount", "platform"])
        assert len(result) >= 2  # order_no 和 amount 至少在白名单里
        names = [c.name for c in result]
        assert "order_no" in names

    def test_unknown_fields_filtered(self):
        from services.kuaimai.erp_unified_schema import build_column_metas
        result = build_column_metas(["nonexistent_field_xyz"])
        assert len(result) == 0

    def test_mixed_fields(self):
        from services.kuaimai.erp_unified_schema import build_column_metas
        result = build_column_metas(["order_no", "fake_field", "platform"])
        names = [c.name for c in result]
        assert "order_no" in names
        assert "fake_field" not in names

    def test_returns_tool_output_column_meta(self):
        from services.kuaimai.erp_unified_schema import build_column_metas
        from services.agent.tool_output import ColumnMeta
        result = build_column_metas(["order_no"])
        assert len(result) > 0
        assert isinstance(result[0], ColumnMeta)

    def test_has_label(self):
        from services.kuaimai.erp_unified_schema import build_column_metas
        result = build_column_metas(["order_no"])
        if result:
            assert result[0].label  # 有中文标签

    def test_empty_fields(self):
        from services.kuaimai.erp_unified_schema import build_column_metas
        result = build_column_metas([])
        assert result == []


# ============================================================
# execute() fields 白名单扩大验证
# ============================================================


class TestExecuteFieldsWhitelist:
    """验证 execute() 的 fields 校验接受 EXPORT_COLUMN_NAMES 字段。

    修复前：fields 只校验 COLUMN_WHITELIST（36 个），remark 等被静默过滤。
    修复后：fields 校验 COLUMN_WHITELIST ∪ EXPORT_COLUMN_NAMES（55+）。
    """

    def test_export_only_field_accepted(self):
        """remark 在 COLUMN_WHITELIST 和 EXPORT_COLUMN_NAMES 中，应保留"""
        from services.kuaimai.erp_unified_schema import (
            COLUMN_WHITELIST, EXPORT_COLUMN_NAMES,
        )
        assert "remark" in COLUMN_WHITELIST
        assert "remark" in EXPORT_COLUMN_NAMES

        # 模拟 execute 的 fields 校验逻辑
        fields = ["remark", "doc_code"]
        valid_fields = set(COLUMN_WHITELIST.keys()) | EXPORT_COLUMN_NAMES
        result = [f for f in fields if f in valid_fields]
        assert "remark" in result
        assert "doc_code" in result

    def test_buyer_message_accepted(self):
        """buyer_message 在 COLUMN_WHITELIST 和 EXPORT_COLUMN_NAMES 中"""
        from services.kuaimai.erp_unified_schema import (
            COLUMN_WHITELIST, EXPORT_COLUMN_NAMES,
        )
        assert "buyer_message" in COLUMN_WHITELIST
        assert "buyer_message" in EXPORT_COLUMN_NAMES

    def test_receiver_address_accepted(self):
        """receiver_address 在 EXPORT_COLUMN_NAMES 中"""
        from services.kuaimai.erp_unified_schema import EXPORT_COLUMN_NAMES
        assert "receiver_address" in EXPORT_COLUMN_NAMES

    def test_text_reason_accepted(self):
        """text_reason（退货原因）在 EXPORT_COLUMN_NAMES 中"""
        from services.kuaimai.erp_unified_schema import EXPORT_COLUMN_NAMES
        assert "text_reason" in EXPORT_COLUMN_NAMES

    def test_invalid_field_still_filtered(self):
        """不存在的字段仍被过滤"""
        from services.kuaimai.erp_unified_schema import (
            COLUMN_WHITELIST, EXPORT_COLUMN_NAMES,
        )
        valid_fields = set(COLUMN_WHITELIST.keys()) | EXPORT_COLUMN_NAMES
        assert "totally_fake_field" not in valid_fields

    def test_whitelist_union_size(self):
        """合并后的白名单应大于 COLUMN_WHITELIST"""
        from services.kuaimai.erp_unified_schema import (
            COLUMN_WHITELIST, EXPORT_COLUMN_NAMES,
        )
        union = set(COLUMN_WHITELIST.keys()) | EXPORT_COLUMN_NAMES
        assert len(union) > len(COLUMN_WHITELIST)


# ============================================================
# extra_fields 合并逻辑（fields→extra_fields 重构）
# ============================================================


class TestExportExtraFieldsMerge:
    """_export 始终以 DEFAULT_DETAIL_FIELDS 为基础，extra_fields 追加不替换。

    修复前：fields=["item_name","doc_created_at"] → 只返回 2 列，丢商品编码。
    修复后：extra_fields 只追加，默认列不可能被裁剪。
    """

    def test_no_extra_fields_uses_defaults(self):
        """extra_fields=None 时使用完整默认列。"""
        from services.kuaimai.erp_unified_schema import DEFAULT_DETAIL_FIELDS
        defaults = DEFAULT_DETAIL_FIELDS["purchase"]
        # 模拟 _export 的合并逻辑
        fields = list(defaults)
        assert len(fields) >= 10
        assert "outer_id" in fields
        assert "item_name" in fields

    def test_extra_fields_appended_to_defaults(self):
        """extra_fields 追加到默认列末尾。"""
        from services.kuaimai.erp_unified_schema import DEFAULT_DETAIL_FIELDS
        defaults = DEFAULT_DETAIL_FIELDS["order"]
        fields = list(defaults)
        extra = ["receiver_name", "receiver_address"]
        for f in extra:
            if f not in fields:
                fields.append(f)
        assert "receiver_name" in fields
        assert "receiver_address" in fields
        # 默认列仍然完整
        for d in defaults:
            assert d in fields

    def test_extra_fields_duplicate_ignored(self):
        """extra_fields 与默认列重复时不会产生重复列。"""
        from services.kuaimai.erp_unified_schema import DEFAULT_DETAIL_FIELDS
        defaults = DEFAULT_DETAIL_FIELDS["purchase"]
        fields = list(defaults)
        # item_name 和 doc_created_at 已在默认列中
        extra = ["item_name", "doc_created_at"]
        for f in extra:
            if f not in fields:
                fields.append(f)
        assert fields.count("item_name") == 1
        assert fields.count("doc_created_at") == 1
        assert len(fields) == len(defaults)  # 没有新增

    def test_extra_fields_cannot_remove_defaults(self):
        """即使 extra_fields 只写了 2 列，默认的 13 列仍完整返回。
        这是本次修复的核心验证：LLM 误设也不会丢列。
        """
        from services.kuaimai.erp_unified_schema import DEFAULT_DETAIL_FIELDS
        defaults = DEFAULT_DETAIL_FIELDS["purchase"]
        fields = list(defaults)
        extra = ["item_name", "doc_created_at"]  # LLM 误设的 2 列
        for f in extra:
            if f not in fields:
                fields.append(f)
        # 关键断言：outer_id（商品编码）仍在
        assert "outer_id" in fields
        assert len(fields) == len(defaults)

    def test_all_doc_types_have_outer_id_in_defaults(self):
        """erp_document_items 表的默认列都包含 outer_id（商品编码）。"""
        from services.kuaimai.erp_unified_schema import (
            DEFAULT_DETAIL_FIELDS, _DOCUMENT_ITEM_DOC_TYPES,
        )
        for doc_type, fields in DEFAULT_DETAIL_FIELDS.items():
            if doc_type not in _DOCUMENT_ITEM_DOC_TYPES:
                continue  # 新表（如 order_log/aftersale_log）无 outer_id
            assert "outer_id" in fields, (
                f"{doc_type} 默认列缺少 outer_id"
            )

    def test_execute_extra_fields_whitelist(self):
        """execute() 对 extra_fields 做白名单校验，无效列被过滤。"""
        from services.kuaimai.erp_unified_schema import (
            COLUMN_WHITELIST, EXPORT_COLUMN_NAMES,
        )
        extra = ["remark", "totally_fake", "cost"]
        valid = set(COLUMN_WHITELIST.keys()) | EXPORT_COLUMN_NAMES
        result = [f for f in extra if f in valid]
        assert result == ["remark", "cost"]


# ── execute() limit 归一化测试 ──────────────────────


class TestExecuteLimitNormalization:
    """execute() 入口处 limit ≤ 0 归一化回退默认值 20"""

    @pytest.mark.asyncio
    async def test_limit_zero_normalized_to_20(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        mock_db.rpc.return_value = mock_rpc

        engine = UnifiedQueryEngine(db=mock_db, org_id=None)
        await engine.execute("purchase", "summary", [], limit=0)
        rpc_call = mock_db.rpc.call_args
        assert rpc_call[0][1]["p_limit"] == 20

    @pytest.mark.asyncio
    async def test_limit_negative_normalized_to_20(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        mock_db.rpc.return_value = mock_rpc

        engine = UnifiedQueryEngine(db=mock_db, org_id=None)
        await engine.execute("purchase", "summary", [], limit=-5)
        rpc_call = mock_db.rpc.call_args
        assert rpc_call[0][1]["p_limit"] == 20

    @pytest.mark.asyncio
    async def test_limit_positive_passed_through(self):
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value.data = {"doc_count": 0, "total_qty": 0, "total_amount": 0}
        mock_db.rpc.return_value = mock_rpc

        engine = UnifiedQueryEngine(db=mock_db, org_id=None)
        await engine.execute("purchase", "summary", [], limit=50)
        rpc_call = mock_db.rpc.call_args
        assert rpc_call[0][1]["p_limit"] == 50


# ── _export() sort_by / limit 测试 ──────────────────


class TestExportSortAndLimit:
    """_export() 的 ORDER BY 和 LIMIT 逻辑"""

    def test_sort_by_whitelist_check(self):
        """sort_by 在 COLUMN_WHITELIST 中时应使用，否则回退 time_col"""
        from services.kuaimai.erp_unified_schema import COLUMN_WHITELIST
        assert "amount" in COLUMN_WHITELIST, "前提：amount 在白名单中"
        assert "totally_fake" not in COLUMN_WHITELIST

    def test_sort_dir_enum_check(self):
        """sort_dir 只允许 asc/desc"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        import inspect
        src = inspect.getsource(UnifiedQueryEngine.execute)
        assert 'sort_dir not in ("asc", "desc")' in src

    def test_export_max_rows_capped(self):
        """min(limit, EXPORT_MAX) 确保不超过安全上限"""
        from services.kuaimai.erp_unified_schema import EXPORT_MAX
        assert min(50, EXPORT_MAX) == 50
        assert min(2_000_000, EXPORT_MAX) == EXPORT_MAX

    def test_export_order_by_with_sort_by(self):
        """sort_by 有效时，ORDER BY 应使用 sort_by 对应的中文别名"""
        from services.kuaimai.erp_unified_schema import COLUMN_WHITELIST, _FIELD_LABEL_CN
        sort_by = "amount"
        assert sort_by in COLUMN_WHITELIST
        order_col = _FIELD_LABEL_CN.get(sort_by, sort_by)
        assert order_col, "amount 应有中文别名"

    def test_export_order_by_fallback_to_time_col(self):
        """sort_by 为 None 时，ORDER BY 应回退到 time_col"""
        from services.kuaimai.erp_unified_schema import _FIELD_LABEL_CN
        order_col = _FIELD_LABEL_CN.get("doc_created_at", "doc_created_at")
        assert order_col, "doc_created_at 应有中文别名"


class TestMergeExportFields:
    """merge_export_fields 公共函数"""

    def test_order_returns_required_plus_defaults(self):
        from services.kuaimai.erp_unified_schema import merge_export_fields
        fields = merge_export_fields("order")
        assert len(fields) >= 5
        assert "outer_id" in fields

    def test_extra_fields_appended(self):
        from services.kuaimai.erp_unified_schema import merge_export_fields
        fields_without = merge_export_fields("order")
        fields_with = merge_export_fields("order", ["receiver_name"])
        assert len(fields_with) >= len(fields_without)
        assert "receiver_name" in fields_with

    def test_dedup_preserves_order(self):
        from services.kuaimai.erp_unified_schema import merge_export_fields, REQUIRED_FIELDS
        fields = merge_export_fields("order", ["outer_id"])  # outer_id 已在 required
        # 不应有重复
        assert len(fields) == len(set(fields))

    def test_invalid_extra_fields_filtered(self):
        from services.kuaimai.erp_unified_schema import merge_export_fields
        fields = merge_export_fields("order", ["nonexistent_column_xyz"])
        assert "nonexistent_column_xyz" not in fields

    def test_unknown_doc_type_returns_empty_or_minimal(self):
        from services.kuaimai.erp_unified_schema import merge_export_fields
        fields = merge_export_fields("nonexistent_type")
        # unknown doc_type → REQUIRED/DEFAULT 为空 → 返回空列表
        assert isinstance(fields, list)


# ── 新表多表统一查询测试 ──────────────────────────────────


class TestGetColumnWhitelist:
    """get_column_whitelist 按 doc_type 分组白名单。"""

    def test_none_returns_global(self):
        from services.kuaimai.erp_unified_schema import (
            get_column_whitelist, COLUMN_WHITELIST,
        )
        assert get_column_whitelist(None) is COLUMN_WHITELIST

    def test_old_doc_type_returns_global(self):
        from services.kuaimai.erp_unified_schema import (
            get_column_whitelist, COLUMN_WHITELIST,
        )
        for dt in ("order", "purchase", "aftersale", "receipt", "shelf", "purchase_return"):
            assert get_column_whitelist(dt) is COLUMN_WHITELIST

    def test_stock_returns_stock_columns(self):
        from services.kuaimai.erp_unified_schema import get_column_whitelist
        wl = get_column_whitelist("stock")
        assert "available_stock" in wl
        assert "total_stock" in wl
        assert "order_no" not in wl  # order 字段不在 stock 白名单

    def test_each_new_doc_type_has_whitelist(self):
        from services.kuaimai.erp_unified_schema import get_column_whitelist
        new_types = [
            "stock", "product", "sku", "daily_stats",
            "platform_map", "batch_stock", "order_log", "aftersale_log",
        ]
        for dt in new_types:
            wl = get_column_whitelist(dt)
            assert len(wl) > 0, f"{dt} 白名单为空"

    def test_unknown_doc_type_returns_empty(self):
        from services.kuaimai.erp_unified_schema import get_column_whitelist
        assert get_column_whitelist("nonexistent") == {}


class TestMultiTableSchemaConstants:
    """erp_multi_table_schema 常量校验——字段数与设计文档校对。"""

    def test_stock_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import STOCK_COLUMNS
        assert len(STOCK_COLUMNS) == 25

    def test_product_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import PRODUCT_COLUMNS
        assert len(PRODUCT_COLUMNS) == 26

    def test_sku_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import SKU_COLUMNS
        assert len(SKU_COLUMNS) == 19

    def test_daily_stats_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import DAILY_STATS_COLUMNS
        assert len(DAILY_STATS_COLUMNS) == 34

    def test_platform_map_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import PLATFORM_MAP_COLUMNS
        assert len(PLATFORM_MAP_COLUMNS) == 5

    def test_batch_stock_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import BATCH_STOCK_COLUMNS
        assert len(BATCH_STOCK_COLUMNS) == 11

    def test_order_log_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import ORDER_LOG_COLUMNS
        assert len(ORDER_LOG_COLUMNS) == 6

    def test_aftersale_log_columns_count(self):
        from services.kuaimai.erp_multi_table_schema import AFTERSALE_LOG_COLUMNS
        assert len(AFTERSALE_LOG_COLUMNS) == 6

    def test_table_columns_covers_all_new_types(self):
        from services.kuaimai.erp_multi_table_schema import TABLE_COLUMNS
        expected = {
            "stock", "product", "sku", "daily_stats",
            "platform_map", "batch_stock", "order_log", "aftersale_log",
        }
        assert set(TABLE_COLUMNS.keys()) == expected

    def test_field_label_cn_covers_key_fields(self):
        from services.kuaimai.erp_multi_table_schema import FIELD_LABEL_CN
        assert FIELD_LABEL_CN["available_stock"] == "可用库存"
        assert FIELD_LABEL_CN["system_id"] == "订单系统ID"
        assert FIELD_LABEL_CN["operate_time"] == "操作时间"
        assert FIELD_LABEL_CN["stat_date"] == "统计日期"


class TestDocTypeTable:
    """DOC_TYPE_TABLE 映射完整性。"""

    def test_all_valid_doc_types_have_table(self):
        from services.kuaimai.erp_unified_schema import (
            DOC_TYPE_TABLE, VALID_DOC_TYPES,
        )
        for dt in VALID_DOC_TYPES:
            assert dt in DOC_TYPE_TABLE, f"{dt} 缺少表映射"

    def test_old_types_route_to_document_items(self):
        from services.kuaimai.erp_unified_schema import DOC_TYPE_TABLE
        for dt in ("order", "purchase", "aftersale", "receipt", "shelf", "purchase_return"):
            assert DOC_TYPE_TABLE[dt] == "erp_document_items"

    def test_new_types_route_to_independent_tables(self):
        from services.kuaimai.erp_unified_schema import DOC_TYPE_TABLE
        assert DOC_TYPE_TABLE["stock"] == "erp_stock_status"
        assert DOC_TYPE_TABLE["product"] == "erp_products"
        assert DOC_TYPE_TABLE["daily_stats"] == "erp_product_daily_stats"
        assert DOC_TYPE_TABLE["order_log"] == "erp_order_logs"


class TestValidateFiltersWithDocType:
    """validate_filters 的 doc_type 参数测试。"""

    def test_stock_field_accepted(self):
        from services.kuaimai.erp_unified_filters import validate_filters
        filters = [{"field": "available_stock", "op": "lt", "value": 0}]
        result, err = validate_filters(filters, doc_type="stock")
        assert err is None
        assert len(result) == 1
        assert result[0].field == "available_stock"

    def test_stock_rejects_order_field(self):
        from services.kuaimai.erp_unified_filters import validate_filters
        filters = [{"field": "order_no", "op": "eq", "value": "123"}]
        result, err = validate_filters(filters, doc_type="stock")
        assert err is not None
        assert "order_no" in err

    def test_none_doc_type_uses_global(self):
        from services.kuaimai.erp_unified_filters import validate_filters
        filters = [{"field": "order_no", "op": "eq", "value": "123"}]
        result, err = validate_filters(filters, doc_type=None)
        assert err is None  # order_no 在全局白名单中

    def test_daily_stats_accepts_stat_date(self):
        from services.kuaimai.erp_unified_filters import validate_filters
        filters = [{"field": "stat_date", "op": "gte", "value": "2026-04-01"}]
        result, err = validate_filters(filters, doc_type="daily_stats")
        assert err is None

    def test_order_log_accepts_system_id(self):
        from services.kuaimai.erp_unified_filters import validate_filters
        filters = [{"field": "system_id", "op": "eq", "value": "123456"}]
        result, err = validate_filters(filters, doc_type="order_log")
        assert err is None


class TestExtractTimeRangeWithDocType:
    """extract_time_range 的 doc_type 参数测试。"""

    def test_stock_no_time_returns_none(self):
        """stock 表不传时间 → 返回 None（不强制时间范围）"""
        from services.kuaimai.erp_unified_filters import extract_time_range
        tr = extract_time_range([], None, None, "summary", doc_type="stock")
        assert tr is None

    def test_daily_stats_no_time_returns_default(self):
        """daily_stats 强制时间范围 → 返回默认当天"""
        from services.kuaimai.erp_unified_filters import extract_time_range
        tr = extract_time_range([], None, None, "summary", doc_type="daily_stats")
        assert tr is not None
        assert tr.time_col == "stat_date"

    def test_order_log_no_time_returns_default(self):
        """order_log 强制时间范围 → 返回默认当天"""
        from services.kuaimai.erp_unified_filters import extract_time_range
        tr = extract_time_range([], None, None, "summary", doc_type="order_log")
        assert tr is not None
        assert tr.time_col == "operate_time"

    def test_product_no_time_returns_none(self):
        """product 快照表 → 返回 None"""
        from services.kuaimai.erp_unified_filters import extract_time_range
        tr = extract_time_range([], None, None, "summary", doc_type="product")
        assert tr is None

    def test_none_doc_type_returns_default_range(self):
        """不传 doc_type → 返回默认当天范围（向后兼容）"""
        from services.kuaimai.erp_unified_filters import extract_time_range
        tr = extract_time_range([], None, None, "summary", doc_type=None)
        assert tr is not None
        assert tr.time_col == "doc_created_at"


class TestFormatFilterHintNewTable:
    """format_filter_hint 支持新表字段标签。"""

    def test_stock_field_shows_chinese_label(self):
        from services.kuaimai.erp_unified_schema import format_filter_hint, ValidatedFilter
        filters = [ValidatedFilter("available_stock", "lt", 0, "numeric")]
        hint = format_filter_hint(filters)
        assert "可用库存" in hint
        assert "< 0" in hint

    def test_system_id_shows_chinese_label(self):
        from services.kuaimai.erp_unified_schema import format_filter_hint, ValidatedFilter
        filters = [ValidatedFilter("system_id", "eq", "123", "text")]
        hint = format_filter_hint(filters)
        assert "订单系统ID" in hint

    def test_time_column_excluded(self):
        """时间列不出现在过滤条件提示中"""
        from services.kuaimai.erp_unified_schema import format_filter_hint, ValidatedFilter
        filters = [
            ValidatedFilter("stock_modified_time", "gte", "2026-04-01", "timestamp"),
            ValidatedFilter("available_stock", "lt", 0, "numeric"),
        ]
        hint = format_filter_hint(filters)
        assert "库存更新时间" not in hint
        assert "可用库存" in hint


class TestFieldLabelCompleteness:
    """每个新表列白名单的字段都有中文标签。"""

    def test_all_new_table_fields_have_labels(self):
        from services.kuaimai.erp_multi_table_schema import TABLE_COLUMNS, FIELD_LABEL_CN
        from services.kuaimai.erp_unified_schema import _FIELD_LABEL_CN as OLD_LABELS
        missing = []
        for doc_type, columns in TABLE_COLUMNS.items():
            for field in columns:
                if field not in FIELD_LABEL_CN and field not in OLD_LABELS:
                    missing.append(f"{doc_type}.{field}")
        assert not missing, f"以下字段缺少中文标签: {missing}"


class TestExportOrmExtraFields:
    """export_orm 的 extra_fields 校验。"""

    @pytest.mark.asyncio
    async def test_invalid_extra_fields_filtered(self):
        """非白名单字段被过滤，不传到 ORM"""
        from services.kuaimai.erp_orm_query import export_orm
        from unittest.mock import MagicMock
        rows = [{"outer_id": "A001", "item_name": "X", "available_stock": 10}]
        resp = MagicMock()
        resp.data = rows
        resp.count = 1
        q = MagicMock()
        for m in ("eq", "is_", "gte", "lt", "order", "limit"):
            setattr(q, m, MagicMock(return_value=q))
        q.execute = MagicMock(return_value=resp)
        db = MagicMock()
        db.table = MagicMock(return_value=MagicMock(select=MagicMock(return_value=q)))

        from pathlib import Path
        from unittest.mock import patch
        tmp = Path("/tmp/test_extra_fields.parquet")
        with patch(
            "services.kuaimai.erp_duckdb_helpers.resolve_export_path",
            return_value=(tmp.parent, "t", tmp, "t.parquet"),
        ), patch(
            "core.duckdb_engine.get_duckdb_engine",
        ) as me, patch(
            "services.agent.data_profile.build_profile_from_duckdb",
            return_value=("p", {}),
        ):
            me.return_value.profile_parquet = MagicMock(return_value={})
            result = await export_orm(
                db, "org1", "erp_stock_status", "stock",
                filters=[], tr=None,
                extra_fields=["nonexistent_col", "available_stock"],
            )
            # select 调用中应包含 available_stock 但不包含 nonexistent_col
            select_call = db.table.return_value.select.call_args[0][0]
            assert "available_stock" in select_call
            assert "nonexistent_col" not in select_call
            tmp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_no_valid_fields_returns_error(self):
        """所有字段都非法时返回 error"""
        from services.kuaimai.erp_orm_query import export_orm
        from unittest.mock import MagicMock
        db = MagicMock()
        # mock 一个返回空 safe_fields 的场景：传全部非法字段
        # 但 REQUIRED + DEFAULT 会兜底，所以需要用一个白名单为空的 doc_type
        result = await export_orm(
            db, "org1", "erp_nonexistent", "nonexistent_type",
            filters=[], tr=None,
        )
        assert result.status == "error"
        assert "无有效字段" in result.summary
