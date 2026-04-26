"""快路径导出单测

覆盖：_apply_filters_to_qb / _classify_row / fast_export 主函数
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass

from services.kuaimai.erp_fast_export import (
    _apply_filters_to_qb,
    _classify_row,
)
from services.kuaimai.erp_unified_schema import ValidatedFilter


# ── _apply_filters_to_qb ─────────────────────────


class TestApplyFiltersToQb:
    """ValidatedFilter → QueryBuilder 映射"""

    def _make_qb(self):
        """链式 mock：每个方法返回自身"""
        qb = MagicMock()
        for m in ("eq", "neq", "gt", "gte", "lt", "lte", "in_", "is_", "ilike"):
            getattr(qb, m).return_value = qb
        return qb

    def test_eq_filter(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="platform", op="eq", value="tb", col_type="text")]
        result = _apply_filters_to_qb(qb, filters)
        qb.eq.assert_called_with("platform", "tb")

    def test_ne_filter(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="status", op="ne", value="cancelled", col_type="text")]
        _apply_filters_to_qb(qb, filters)
        qb.neq.assert_called_with("status", "cancelled")

    def test_gt_filter(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="amount", op="gt", value=100, col_type="numeric")]
        _apply_filters_to_qb(qb, filters)
        qb.gt.assert_called_with("amount", 100)

    def test_gte_lte_filter(self):
        qb = self._make_qb()
        filters = [
            ValidatedFilter(field="quantity", op="gte", value=5, col_type="integer"),
            ValidatedFilter(field="quantity", op="lte", value=50, col_type="integer"),
        ]
        _apply_filters_to_qb(qb, filters)
        qb.gte.assert_called_with("quantity", 5)
        qb.lte.assert_called_with("quantity", 50)

    def test_in_filter_list(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="platform", op="in", value=["tb", "jd"], col_type="text")]
        _apply_filters_to_qb(qb, filters)
        qb.in_.assert_called_with("platform", ["tb", "jd"])

    def test_in_filter_single_value(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="platform", op="in", value="tb", col_type="text")]
        _apply_filters_to_qb(qb, filters)
        qb.in_.assert_called_with("platform", ["tb"])

    def test_like_uses_ilike(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="item_name", op="like", value="%手机%", col_type="text")]
        _apply_filters_to_qb(qb, filters)
        qb.ilike.assert_called_with("item_name", "%手机%")

    def test_is_null_filter(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="express_no", op="is_null", value=True, col_type="text")]
        _apply_filters_to_qb(qb, filters)
        qb.is_.assert_called_with("express_no", "null")

    def test_between_filter(self):
        qb = self._make_qb()
        filters = [ValidatedFilter(field="amount", op="between", value=[100, 500], col_type="numeric")]
        _apply_filters_to_qb(qb, filters)
        qb.gte.assert_called_with("amount", 100)
        qb.lte.assert_called_with("amount", 500)

    def test_not_in_skipped(self):
        """not_in 在快路径中跳过，不报错"""
        qb = self._make_qb()
        filters = [ValidatedFilter(field="status", op="not_in", value=["a", "b"], col_type="text")]
        result = _apply_filters_to_qb(qb, filters)
        # 不应调用任何过滤方法
        qb.eq.assert_not_called()

    def test_time_columns_skipped(self):
        """时间列由调用方处理，此处跳过"""
        qb = self._make_qb()
        filters = [ValidatedFilter(field="pay_time", op="gte", value="2026-04-01", col_type="timestamp")]
        _apply_filters_to_qb(qb, filters)
        qb.gte.assert_not_called()

    def test_empty_filters(self):
        qb = self._make_qb()
        result = _apply_filters_to_qb(qb, [])
        assert result is qb

    def test_multiple_filters(self):
        qb = self._make_qb()
        filters = [
            ValidatedFilter(field="platform", op="eq", value="tb", col_type="text"),
            ValidatedFilter(field="amount", op="gt", value=100, col_type="numeric"),
        ]
        _apply_filters_to_qb(qb, filters)
        qb.eq.assert_called_once()
        qb.gt.assert_called_once()


# ── _classify_row ────────────────────────────────


class TestClassifyRow:
    """Python 侧行级订单分类"""

    def _make_classifier(self, rules):
        from services.kuaimai.order_classifier import OrderClassifier
        c = MagicMock(spec=OrderClassifier)
        c.rules = rules
        c._match_all_conditions = OrderClassifier._match_all_conditions
        return c

    def test_matches_first_rule(self):
        rules = [
            {"rule_name": "刷单", "conditions": [{"field": "is_scalping", "op": "eq", "value": 1}]},
        ]
        c = self._make_classifier(rules)
        row = {"is_scalping": 1}
        assert _classify_row(c, row) == "刷单"

    def test_no_match_returns_default(self):
        rules = [
            {"rule_name": "刷单", "conditions": [{"field": "is_scalping", "op": "eq", "value": 1}]},
        ]
        c = self._make_classifier(rules)
        row = {"is_scalping": 0}
        assert _classify_row(c, row) == "有效订单"

    def test_empty_conditions_skipped(self):
        """空条件规则（兜底）被 continue 跳过"""
        rules = [
            {"rule_name": "兜底", "conditions": []},
        ]
        c = self._make_classifier(rules)
        assert _classify_row(c, {"is_scalping": 0}) == "有效订单"

    def test_first_match_wins(self):
        rules = [
            {"rule_name": "规则A", "conditions": [{"field": "platform", "op": "eq", "value": "tb"}]},
            {"rule_name": "规则B", "conditions": [{"field": "platform", "op": "eq", "value": "tb"}]},
        ]
        c = self._make_classifier(rules)
        row = {"platform": "tb"}
        assert _classify_row(c, row) == "规则A"
