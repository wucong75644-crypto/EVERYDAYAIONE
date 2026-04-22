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


# ── classify_grouped 测试 ─────────────────────────────


class TestClassifyGrouped:
    """OrderClassifier.classify_grouped() 分组分类"""

    def _make_classifier(self) -> OrderClassifier:
        return OrderClassifier(DEFAULT_ORDER_RULES)

    def test_two_groups(self):
        """两个平台分组，各自独立分类"""
        rows = [
            # 淘宝有效
            {"group_key": "tb", "order_type": "2,3,0", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 100, "total_qty": 200, "total_amount": 5000},
            # 淘宝刷单
            {"group_key": "tb", "order_type": "2,3,10", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 30, "total_qty": 60, "total_amount": 0},
            # 拼多多有效
            {"group_key": "pdd", "order_type": "2,3,0", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 80, "total_qty": 160, "total_amount": 4000},
            # 拼多多关闭
            {"group_key": "pdd", "order_type": "2,3", "order_status": "CLOSED",
             "is_scalping": 0, "doc_count": 10, "total_qty": 20, "total_amount": 300},
        ]
        result = self._make_classifier().classify_grouped(rows)
        assert set(result.keys()) == {"tb", "pdd"}

        # 淘宝：100有效 + 30刷单
        assert result["tb"].total["doc_count"] == 130
        assert result["tb"].valid["doc_count"] == 100
        assert result["tb"].categories["空包/刷单"]["doc_count"] == 30

        # 拼多多：80有效 + 10关闭
        assert result["pdd"].total["doc_count"] == 90
        assert result["pdd"].valid["doc_count"] == 80
        assert result["pdd"].categories["已关闭/取消"]["doc_count"] == 10

    def test_single_group(self):
        """单个分组等价于 classify"""
        rows = [
            {"group_key": "tb", "order_type": "2,3,0", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 50, "total_qty": 100, "total_amount": 2500},
        ]
        result = self._make_classifier().classify_grouped(rows)
        assert len(result) == 1
        assert result["tb"].valid["doc_count"] == 50

    def test_missing_group_key_uses_unknown(self):
        """缺少 group_key 的行归入 '未知'"""
        rows = [
            {"order_type": "2,3,0", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 10, "total_qty": 20, "total_amount": 500},
        ]
        result = self._make_classifier().classify_grouped(rows)
        assert "未知" in result

    def test_each_group_sums_correctly(self):
        """每个分组内分类总和 = 该组总数"""
        rows = [
            {"group_key": "a", "order_type": "2,10", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 20, "total_qty": 40, "total_amount": 100},
            {"group_key": "a", "order_type": "2,14", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 5, "total_qty": 10, "total_amount": 50},
            {"group_key": "a", "order_type": "2,3", "order_status": "PAID",
             "is_scalping": 0, "doc_count": 75, "total_qty": 150, "total_amount": 3000},
        ]
        result = self._make_classifier().classify_grouped(rows)
        cr = result["a"]
        cat_sum = sum(c["doc_count"] for c in cr.categories.values())
        assert cat_sum == cr.total["doc_count"] == 100


# ── to_case_sql 测试 ─────────────────────────────────


class TestToCaseSql:
    """OrderClassifier.to_case_sql() 规则转 SQL"""

    def _make_classifier(self) -> OrderClassifier:
        return OrderClassifier(DEFAULT_ORDER_RULES)

    def test_generates_valid_sql(self):
        """生成的 CASE WHEN 包含所有排除规则"""
        sql = self._make_classifier().to_case_sql()
        assert "CASE" in sql
        assert "ELSE '有效订单'" in sql
        assert "空包/刷单" in sql
        assert "补发单" in sql
        assert "已关闭/取消" in sql

    def test_list_has_uses_string_split(self):
        """list_has 条件转 DuckDB string_split + list_contains"""
        sql = self._make_classifier().to_case_sql()
        assert "string_split" in sql
        assert "list_contains" in sql

    def test_empty_rules_returns_else_only(self):
        """无排除规则时只有 ELSE"""
        # 只保留兜底规则
        classifier = OrderClassifier([
            {"rule_name": "有效订单", "conditions": [], "priority": 99},
        ])
        sql = classifier.to_case_sql()
        assert sql == "CASE  ELSE '有效订单' END"


# ── show_recommendation 参数测试 ─────────────────────


class TestShowRecommendation:
    """to_display_text(show_recommendation=...) 控制推荐语"""

    def _make_result(self) -> ClassificationResult:
        return ClassificationResult(
            total={"doc_count": 100, "total_qty": 200, "total_amount": 5000},
            categories={"有效订单": {"doc_count": 80, "total_qty": 160, "total_amount": 4000}},
            valid={"doc_count": 80, "total_qty": 160, "total_amount": 4000},
        )

    def test_show_recommendation_true(self):
        text = self._make_result().to_display_text(show_recommendation=True)
        assert "后续计算请默认使用有效订单数据" in text

    def test_show_recommendation_false(self):
        text = self._make_result().to_display_text(show_recommendation=False)
        assert "后续计算请默认使用有效订单数据" not in text

    def test_default_shows_recommendation(self):
        """默认显示推荐语（向后兼容）"""
        text = self._make_result().to_display_text()
        assert "后续计算请默认使用有效订单数据" in text


# ── _sql_lit 测试 ─────────────────────────────────────


class TestSqlLit:
    """_sql_lit 值转 SQL 字面量"""

    def test_integer(self):
        from services.kuaimai.order_classifier import _sql_lit
        assert _sql_lit(10) == "10"

    def test_float(self):
        from services.kuaimai.order_classifier import _sql_lit
        assert _sql_lit(3.14) == "3.14"

    def test_string(self):
        from services.kuaimai.order_classifier import _sql_lit
        assert _sql_lit("PAID") == "'PAID'"

    def test_string_with_single_quote(self):
        """单引号转义"""
        from services.kuaimai.order_classifier import _sql_lit
        assert _sql_lit("Tom's") == "'Tom''s'"

    def test_string_with_multiple_quotes(self):
        from services.kuaimai.order_classifier import _sql_lit
        assert _sql_lit("it's Tom's") == "'it''s Tom''s'"


# ── _cond_to_sql 测试 ────────────────────────────────


class TestCondToSql:
    """OrderClassifier._cond_to_sql 各操作符"""

    def test_eq_integer(self):
        sql = OrderClassifier._cond_to_sql("is_scalping", "eq", 1)
        assert sql == "is_scalping = 1"

    def test_eq_string(self):
        sql = OrderClassifier._cond_to_sql("order_status", "eq", "PAID")
        assert sql == "order_status = 'PAID'"

    def test_ne(self):
        sql = OrderClassifier._cond_to_sql("order_status", "ne", "CLOSED")
        assert sql == "order_status != 'CLOSED'"

    def test_in(self):
        sql = OrderClassifier._cond_to_sql("order_status", "in", ["CLOSED", "CANCEL"])
        assert sql == "order_status IN ('CLOSED', 'CANCEL')"

    def test_not_in(self):
        sql = OrderClassifier._cond_to_sql("order_status", "not_in", ["CLOSED"])
        assert sql == "order_status NOT IN ('CLOSED')"

    def test_list_has(self):
        sql = OrderClassifier._cond_to_sql("order_type", "list_has", [10])
        assert "list_contains" in sql
        assert "string_split" in sql
        assert "'10'" in sql

    def test_list_has_multiple(self):
        sql = OrderClassifier._cond_to_sql("order_type", "list_has", [10, 14])
        assert sql.count("list_contains") == 2
        assert " OR " in sql

    def test_list_not_has(self):
        sql = OrderClassifier._cond_to_sql("order_type", "list_not_has", [10])
        assert "NOT" in sql
        assert "list_contains" in sql

    def test_unknown_op_returns_true(self):
        sql = OrderClassifier._cond_to_sql("foo", "unknown_op", "bar")
        assert sql == "TRUE"
