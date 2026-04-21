"""
DuckDB 导出辅助函数单元测试

覆盖：services/kuaimai/erp_duckdb_helpers.py
- _sql_escape: 单引号转义
- build_pii_select: PII 脱敏 SQL 构建
- build_export_where: WHERE 子句构建（全 op + 转义）
- resolve_export_path: staging 路径生成
- read_parquet_preview: Parquet 前 N 行预览
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_tests_dir = Path(__file__).parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))
_backend_dir = _tests_dir.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.kuaimai.erp_duckdb_helpers import (
    _sql_escape,
    build_pii_select,
    build_export_where,
    resolve_export_path,
)
from services.kuaimai.erp_unified_schema import TimeRange, ValidatedFilter


# ── _sql_escape 测试 ─────────────────────────────────


class TestSqlEscape:

    def test_normal_string_unchanged(self):
        assert _sql_escape("hello") == "hello"

    def test_single_quote_doubled(self):
        assert _sql_escape("it's") == "it''s"

    def test_multiple_quotes(self):
        assert _sql_escape("a'b'c") == "a''b''c"

    def test_empty_string(self):
        assert _sql_escape("") == ""

    def test_numeric_value(self):
        assert _sql_escape(123) == "123"

    def test_none_value(self):
        assert _sql_escape(None) == "None"

    def test_chinese_with_quote(self):
        assert _sql_escape("小么小二郎's") == "小么小二郎''s"


# ── build_pii_select 测试 ────────────────────────────


class TestBuildPiiSelect:

    def test_normal_fields_pass_through(self):
        result = build_pii_select(["order_no", "amount", "shop_name"])
        assert result == "order_no, amount, shop_name"

    def test_receiver_name_masked(self):
        result = build_pii_select(["order_no", "receiver_name"])
        assert "order_no" in result
        assert "CASE WHEN receiver_name" in result
        assert "substr(receiver_name, 1, 1)" in result
        assert "AS receiver_name" in result

    def test_receiver_mobile_masked(self):
        result = build_pii_select(["receiver_mobile"])
        assert "CASE WHEN receiver_mobile" in result
        assert "'****'" in result
        assert "AS receiver_mobile" in result

    def test_receiver_phone_masked(self):
        result = build_pii_select(["receiver_phone"])
        assert "CASE WHEN receiver_phone" in result
        assert "AS receiver_phone" in result

    def test_all_pii_fields_masked(self):
        result = build_pii_select(["receiver_name", "receiver_mobile", "receiver_phone", "receiver_address"])
        assert result.count("CASE WHEN") == 4

    def test_receiver_address_masked(self):
        result = build_pii_select(["receiver_address"])
        assert "CASE WHEN receiver_address" in result
        assert "AS receiver_address" in result

    def test_mixed_normal_and_pii(self):
        fields = ["order_no", "receiver_name", "amount", "receiver_mobile"]
        result = build_pii_select(fields)
        # 验证普通字段原样输出、PII 字段有 CASE WHEN
        assert result.startswith("order_no, ")
        assert "CASE WHEN receiver_name" in result
        assert ", amount, " in result
        assert "CASE WHEN receiver_mobile" in result

    def test_empty_fields(self):
        assert build_pii_select([]) == ""


# ── build_export_where 测试 ──────────────────────────


class TestBuildExportWhere:

    def _tr(self, time_col="consign_time"):
        return TimeRange(
            start_iso="2026-04-14 00:00:00+08:00",
            end_iso="2026-04-15 00:00:00+08:00",
            time_col=time_col,
            date_range=None, label="",
        )

    def test_basic_where_with_org(self):
        result = build_export_where("order", [], self._tr(), "org-123")
        assert "doc_type = 'order'" in result
        assert "consign_time >= '2026-04-14" in result
        assert "consign_time < '2026-04-15" in result
        assert "org_id = 'org-123'" in result

    def test_basic_where_without_org(self):
        result = build_export_where("order", [], self._tr(), None)
        assert "org_id IS NULL" in result

    def test_eq_filter(self):
        filters = [ValidatedFilter("platform", "eq", "tb", "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "platform = 'tb'" in result

    def test_ne_filter(self):
        filters = [ValidatedFilter("order_status", "ne", "CANCELLED", "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "order_status != 'CANCELLED'" in result

    def test_gt_gte_lt_lte_filters(self):
        filters = [
            ValidatedFilter("amount", "gt", 100, "numeric"),
            ValidatedFilter("amount", "lte", 999, "numeric"),
        ]
        result = build_export_where("order", filters, self._tr(), None)
        assert "amount > '100'" in result
        assert "amount <= '999'" in result

    def test_like_filter(self):
        filters = [ValidatedFilter("shop_name", "like", "%蓝创%", "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "shop_name ILIKE '%蓝创%'" in result

    def test_in_filter(self):
        filters = [ValidatedFilter("platform", "in", ["tb", "jd", "pdd"], "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "platform IN ('tb', 'jd', 'pdd')" in result

    def test_is_null_true(self):
        filters = [ValidatedFilter("express_no", "is_null", True, "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "express_no IS NULL" in result

    def test_is_null_false(self):
        filters = [ValidatedFilter("express_no", "is_null", False, "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "express_no IS NOT NULL" in result

    def test_between_filter(self):
        filters = [ValidatedFilter("amount", "between", [100, 500], "numeric")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "amount BETWEEN '100' AND '500'" in result

    def test_time_filters_excluded(self):
        """时间列 filter 不应出现在 WHERE 中（已由 tr 参数处理）"""
        filters = [ValidatedFilter("consign_time", "gte", "2026-04-14", "timestamp")]
        result = build_export_where("order", filters, self._tr(), None)
        # consign_time 只出现 2 次：来自 tr 的 gte 和 lt
        assert result.count("consign_time") == 2

    def test_single_quote_in_value_escaped(self):
        """值含单引号时必须正确转义"""
        filters = [ValidatedFilter("shop_name", "eq", "小么小二郎's", "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "小么小二郎''s" in result
        # 不能出现未转义的单引号导致 SQL 断裂
        assert "小么小二郎's'" not in result

    def test_single_quote_in_org_id_escaped(self):
        result = build_export_where("order", [], self._tr(), "org'id")
        assert "org''id" in result

    def test_in_values_with_quotes_escaped(self):
        filters = [ValidatedFilter("shop_name", "in", ["A's", "B's"], "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "A''s" in result
        assert "B''s" in result

    def test_between_values_with_quotes_escaped(self):
        filters = [ValidatedFilter("item_name", "between", ["a'1", "z'9"], "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "a''1" in result
        assert "z''9" in result

    def test_multiple_filters_combined_with_and(self):
        filters = [
            ValidatedFilter("platform", "eq", "tb", "text"),
            ValidatedFilter("amount", "gt", 0, "numeric"),
        ]
        result = build_export_where("order", filters, self._tr(), None)
        assert " AND " in result
        assert "platform = 'tb'" in result
        assert "amount > '0'" in result

    def test_empty_in_list_skipped(self):
        """空 in 列表不应生成 IN 子句"""
        filters = [ValidatedFilter("platform", "in", [], "text")]
        result = build_export_where("order", filters, self._tr(), None)
        assert "IN" not in result


# ── resolve_export_path 测试 ─────────────────────────


class TestResolveExportPath:

    @patch("core.config.get_settings")
    def test_returns_four_elements(self, mock_settings):
        mock_settings.return_value.file_workspace_root = tempfile.gettempdir()
        staging_dir, rel_path, staging_path, filename = resolve_export_path(
            "order", "user1", "org1", "conv1",
        )
        assert isinstance(staging_dir, Path)
        assert isinstance(staging_path, Path)
        assert isinstance(rel_path, str)
        assert isinstance(filename, str)

    @patch("core.config.get_settings")
    def test_filename_contains_doc_type(self, mock_settings):
        mock_settings.return_value.file_workspace_root = tempfile.gettempdir()
        _, _, _, filename = resolve_export_path("order", "u", "o", "c")
        assert filename.startswith("local_order_")
        assert filename.endswith(".parquet")

    @patch("core.config.get_settings")
    def test_default_conversation_id(self, mock_settings):
        mock_settings.return_value.file_workspace_root = tempfile.gettempdir()
        _, rel_path, _, _ = resolve_export_path("order", "u", "o", None)
        assert "default" in rel_path

    @patch("core.config.get_settings")
    def test_staging_dir_created(self, mock_settings):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_settings.return_value.file_workspace_root = tmpdir
            staging_dir, _, _, _ = resolve_export_path("order", "u", "o", "c123")
            assert staging_dir.exists()


# v6: read_parquet_preview 已删除，功能由 build_profile_from_duckdb 替代
