"""订单分类引擎单元测试"""

import pytest

from config.default_classification_rules import DEFAULT_ORDER_RULES
from services.kuaimai.order_classifier import ClassificationResult, OrderClassifier


# ── 基础分类测试 ─────────────────────────────────────────


class TestClassify:
    """OrderClassifier.classify() 核心逻辑"""

    def _make_classifier(self) -> OrderClassifier:
        return OrderClassifier(DEFAULT_ORDER_RULES)

    def test_valid_order_only(self):
        """全部有效订单"""
        rows = [
            {"order_type": "2,3,0", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 100, "total_qty": 200, "total_amount": 5000},
        ]
        result = self._make_classifier().classify(rows)
        assert result.total["doc_count"] == 100
        assert result.valid["doc_count"] == 100
        assert len(result.categories_list) == 0

    def test_scalping_by_order_type(self):
        """order_type 含 10 → 空包/刷单"""
        rows = [
            {"order_type": "2,3,10,0", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 50, "total_qty": 100, "total_amount": 2000},
            {"order_type": "2,3,0", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 100, "total_qty": 200, "total_amount": 5000},
        ]
        result = self._make_classifier().classify(rows)
        assert result.total["doc_count"] == 150
        assert result.valid["doc_count"] == 100
        assert result.categories["空包/刷单"]["doc_count"] == 50

    def test_scalping_by_is_scalping_flag(self):
        """is_scalping=1 → 空包/刷单（不含 order_type=10）"""
        rows = [
            {"order_type": "2,3,0", "order_status": "PAID", "is_scalping": 1,
             "doc_count": 30, "total_qty": 60, "total_amount": 1000},
        ]
        result = self._make_classifier().classify(rows)
        assert result.categories["空包/刷单"]["doc_count"] == 30
        assert result.valid["doc_count"] == 0

    def test_supplement_order(self):
        """order_type 含 14 → 补发单"""
        rows = [
            {"order_type": "2,14", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 10, "total_qty": 20, "total_amount": 300},
        ]
        result = self._make_classifier().classify(rows)
        assert result.categories["补发单"]["doc_count"] == 10

    def test_closed_order(self):
        """已关闭订单"""
        rows = [
            {"order_type": "2,3,0", "order_status": "CLOSED", "is_scalping": 0,
             "doc_count": 20, "total_qty": 40, "total_amount": 800},
        ]
        result = self._make_classifier().classify(rows)
        assert result.categories["已关闭/取消"]["doc_count"] == 20

    def test_cancel_order(self):
        """取消订单"""
        rows = [
            {"order_type": "2", "order_status": "CANCEL", "is_scalping": 0,
             "doc_count": 5, "total_qty": 10, "total_amount": 200},
        ]
        result = self._make_classifier().classify(rows)
        assert result.categories["已关闭/取消"]["doc_count"] == 5

    def test_priority_scalping_over_closed(self):
        """order_type=10 优先于 CLOSED（priority 10 < 30）"""
        rows = [
            {"order_type": "10", "order_status": "CLOSED", "is_scalping": 0,
             "doc_count": 5, "total_qty": 10, "total_amount": 100},
        ]
        result = self._make_classifier().classify(rows)
        assert result.categories.get("空包/刷单", {}).get("doc_count") == 5
        assert "已关闭/取消" not in result.categories

    def test_mixed_categories_sum_equals_total(self):
        """所有分类数量之和 = 总数（互斥分类）"""
        rows = [
            {"order_type": "2,3,10", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 2539, "total_qty": 5000, "total_amount": 8000},
            {"order_type": "2,14", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 66, "total_qty": 100, "total_amount": 500},
            {"order_type": "2,3", "order_status": "CLOSED", "is_scalping": 0,
             "doc_count": 614, "total_qty": 1200, "total_amount": 2500},
            {"order_type": "2,3,0", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 6057, "total_qty": 12000, "total_amount": 16059.77},
        ]
        result = self._make_classifier().classify(rows)
        cat_sum = sum(c["doc_count"] for c in result.categories.values())
        assert cat_sum == result.total["doc_count"]
        assert result.total["doc_count"] == 9276

    def test_empty_rows(self):
        """空输入"""
        result = self._make_classifier().classify([])
        assert result.total["doc_count"] == 0
        assert result.valid["doc_count"] == 0


# ── 条件匹配测试 ─────────────────────────────────────────


class TestMatchCondition:
    """_match_condition 边界情况"""

    def test_null_value_positive_match_returns_false(self):
        assert OrderClassifier._match_condition(
            {"order_type": None}, {"field": "order_type", "op": "list_has", "value": [10]}
        ) is False

    def test_null_value_negative_match_returns_true(self):
        assert OrderClassifier._match_condition(
            {"order_type": None}, {"field": "order_type", "op": "not_in", "value": [10]}
        ) is True

    def test_list_has_exact_match(self):
        """list_has 精确匹配：'1' 不应匹配 '10'"""
        assert OrderClassifier._match_condition(
            {"order_type": "2,3,10"}, {"field": "order_type", "op": "list_has", "value": [1]}
        ) is False

    def test_list_has_finds_target(self):
        assert OrderClassifier._match_condition(
            {"order_type": "2,3,10"}, {"field": "order_type", "op": "list_has", "value": [10]}
        ) is True

    def test_in_operator(self):
        assert OrderClassifier._match_condition(
            {"order_status": "CLOSED"}, {"field": "order_status", "op": "in", "value": ["CLOSED", "CANCEL"]}
        ) is True

    def test_eq_operator(self):
        assert OrderClassifier._match_condition(
            {"is_scalping": 1}, {"field": "is_scalping", "op": "eq", "value": 1}
        ) is True

    def test_ne_operator(self):
        assert OrderClassifier._match_condition(
            {"is_scalping": 0}, {"field": "is_scalping", "op": "ne", "value": 1}
        ) is True

    def test_unknown_op_returns_false(self):
        assert OrderClassifier._match_condition(
            {"foo": "bar"}, {"field": "foo", "op": "unknown_op", "value": "bar"}
        ) is False


# ── 展示文本测试 ─────────────────────────────────────────


class TestDisplayText:
    """ClassificationResult.to_display_text()"""

    def test_display_text_contains_valid_and_total(self):
        cr = ClassificationResult(
            total={"doc_count": 100, "total_qty": 200, "total_amount": 5000},
            categories={"有效订单": {"doc_count": 80, "total_qty": 160, "total_amount": 4000},
                         "空包/刷单": {"doc_count": 20, "total_qty": 40, "total_amount": 1000}},
            valid={"doc_count": 80, "total_qty": 160, "total_amount": 4000},
        )
        text = cr.to_display_text()
        assert "100" in text
        assert "80" in text
        assert "4,000.00" in text
        assert "后续计算请默认使用有效订单数据" in text

    def test_categories_list_excludes_valid(self):
        cr = ClassificationResult(
            total={"doc_count": 100, "total_qty": 200, "total_amount": 5000},
            categories={"有效订单": {"doc_count": 80, "total_qty": 160, "total_amount": 4000},
                         "空包/刷单": {"doc_count": 20, "total_qty": 40, "total_amount": 1000}},
            valid={"doc_count": 80, "total_qty": 160, "total_amount": 4000},
        )
        cats = cr.categories_list
        assert len(cats) == 1
        assert cats[0]["name"] == "空包/刷单"


# ── 未知 order_type 监控测试 ─────────────────────────────


class TestUnknownOrderType:
    """未知 order_type 触发 logger.warning"""

    def test_unknown_type_logged(self):
        from loguru import logger
        messages: list[str] = []
        handler_id = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            rows = [
                {"order_type": "2,3,999", "order_status": "PAID", "is_scalping": 0,
                 "doc_count": 1, "total_qty": 1, "total_amount": 10},
            ]
            OrderClassifier(DEFAULT_ORDER_RULES).classify(rows)
            assert any("999" in m for m in messages)
        finally:
            logger.remove(handler_id)

    def test_known_types_no_warning(self):
        from loguru import logger
        messages: list[str] = []
        handler_id = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            rows = [
                {"order_type": "2,3,10,0", "order_status": "PAID", "is_scalping": 0,
                 "doc_count": 1, "total_qty": 1, "total_amount": 10},
            ]
            OrderClassifier(DEFAULT_ORDER_RULES).classify(rows)
            assert not any("未知 order_type" in m for m in messages)
        finally:
            logger.remove(handler_id)


# ── 缓存测试 ─────────────────────────────────────────────


class TestCache:
    def test_invalidate_cache_specific_org(self):
        OrderClassifier._cache["test-org"] = ([], 9999999999)
        OrderClassifier.invalidate_cache("test-org")
        assert "test-org" not in OrderClassifier._cache

    def test_invalidate_cache_all(self):
        OrderClassifier._cache["org1"] = ([], 9999999999)
        OrderClassifier._cache["org2"] = ([], 9999999999)
        OrderClassifier.invalidate_cache()
        assert len(OrderClassifier._cache) == 0
