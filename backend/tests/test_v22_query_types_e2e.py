"""
v2.2 查询架构重构 E2E 测试。

全链路：用户参数 → UnifiedQueryEngine.execute() → 各分析模块 → ToolOutput。
每个 query_type 一个测试类，mock 数据库返回，验证路由 + 参数传递 + 返回格式。

设计文档: docs/document/TECH_ERP查询架构重构.md
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ── mock 工厂 ──────────────────────────────────────


def _mock_db(rows: list[dict], count: int | None = None):
    """构造 Supabase ORM + RPC 双响应 mock。"""
    resp = MagicMock()
    resp.data = rows
    resp.count = count if count is not None else len(rows)

    q = MagicMock()
    for method in ("eq", "is_", "gte", "gt", "lt", "lte", "neq",
                    "ilike", "in_", "order", "limit", "not_"):
        setattr(q, method, MagicMock(return_value=q))
    q.not_ = MagicMock()
    q.not_.in_ = MagicMock(return_value=q)
    q.not_.is_ = MagicMock(return_value=q)
    q.execute = MagicMock(return_value=resp)

    db = MagicMock()
    db.table = MagicMock(return_value=MagicMock(select=MagicMock(return_value=q)))

    # RPC mock
    rpc_resp = MagicMock()
    rpc_resp.data = rows
    rpc_mock = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
    db.rpc = rpc_mock
    db._q = q
    return db


def _make_engine(db=None, org_id="test-org-id"):
    from services.kuaimai.erp_unified_query import UnifiedQueryEngine
    return UnifiedQueryEngine(db=db or _mock_db([]), org_id=org_id)


# ============================================================
# 1. _resolve_query_type 自动推断
# ============================================================


class TestResolveQueryType:
    """验证 auto 推断逻辑正确路由到对应 query_type。"""

    def _resolve(self, **kw):
        from services.kuaimai.erp_unified_query import _resolve_query_type
        defaults = {
            "query_type": "auto", "mode": "summary", "limit": 20,
            "time_granularity": None, "compare_range": None,
            "alert_type": None, "metrics": None,
        }
        defaults.update(kw)
        return _resolve_query_type(**defaults)

    def test_explicit_type_passthrough(self):
        assert self._resolve(query_type="trend") == "trend"
        assert self._resolve(query_type="cross") == "cross"
        assert self._resolve(query_type="alert") == "alert"

    def test_alert_type_triggers_alert(self):
        assert self._resolve(alert_type="low_stock") == "alert"

    def test_time_granularity_triggers_trend(self):
        assert self._resolve(time_granularity="day") == "trend"

    def test_compare_range_triggers_compare(self):
        assert self._resolve(compare_range="mom") == "compare"

    def test_cross_metrics_triggers_cross(self):
        assert self._resolve(metrics=["return_rate"]) == "cross"
        assert self._resolve(metrics=["gross_margin"]) == "cross"

    def test_non_cross_metrics_stay_summary(self):
        assert self._resolve(metrics=["count"]) == "summary"

    def test_export_default_limit_becomes_export(self):
        """mode=export + limit≤20（默认值）→ 用户大概率想全量导出"""
        assert self._resolve(mode="export", limit=5) == "export"
        assert self._resolve(mode="export", limit=20) == "export"

    def test_export_small_explicit_becomes_detail(self):
        """mode=export + limit 在 21~200 → 明确指定了少量条数，走 detail"""
        assert self._resolve(mode="export", limit=50) == "detail"
        assert self._resolve(mode="export", limit=200) == "detail"

    def test_export_large_stays_export(self):
        assert self._resolve(mode="export", limit=500) == "export"

    def test_default_is_summary(self):
        assert self._resolve() == "summary"

    def test_alert_priority_over_trend(self):
        """alert 优先级高于 trend"""
        assert self._resolve(alert_type="low_stock", time_granularity="day") == "alert"

    def test_trend_priority_over_compare(self):
        """time_granularity 优先级高于 compare_range"""
        assert self._resolve(time_granularity="day", compare_range="mom") == "trend"


# ============================================================
# 2. Detail（PG ORM 直查 ≤200 行）
# ============================================================


class TestDetailQuery:

    @pytest.mark.asyncio
    async def test_detail_routes_correctly(self):
        """mode=export + limit≤200 → detail → ORM 直查"""
        rows = [{"doc_id": "D001", "amount": 100, "outer_id": "P001"}]
        db = _mock_db(rows, count=1)
        engine = _make_engine(db)

        with patch("services.kuaimai.erp_orm_query.detail_orm", new_callable=AsyncMock) as mock_detail:
            from services.agent.tool_output import ToolOutput, OutputFormat
            mock_detail.return_value = ToolOutput(
                summary="订单明细：共 1 条",
                format=OutputFormat.TABLE,
                data=rows,
                metadata={"query_type": "detail"},
            )
            result = await engine.execute(
                doc_type="order", mode="export", limit=5,
                filters=[{"field": "doc_created_at", "op": "gte", "value": "2026-04-01"},
                         {"field": "doc_created_at", "op": "lt", "value": "2026-04-28"}],
                sort_by="amount", sort_dir="desc",
            )
            mock_detail.assert_called_once()
            assert result.metadata.get("query_type") == "detail"


# ============================================================
# 3. Summary（聚合统计）
# ============================================================


class TestSummaryQuery:

    @pytest.mark.asyncio
    async def test_summary_routes_to_rpc(self):
        """mode=summary → RPC 聚合"""
        rpc_data = {"doc_count": 42, "total_qty": 100, "total_amount": 5000}
        db = _mock_db([])
        rpc_resp = MagicMock()
        rpc_resp.data = rpc_data
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
        engine = _make_engine(db)

        result = await engine.execute(
            doc_type="order", mode="summary",
            filters=[{"field": "doc_created_at", "op": "gte", "value": "2026-04-01"},
                     {"field": "doc_created_at", "op": "lt", "value": "2026-04-28"}],
        )
        db.rpc.assert_called()
        assert "42" in result.summary or result.data


# ============================================================
# 4. Trend（趋势分析）
# ============================================================


class TestTrendQuery:

    @pytest.mark.asyncio
    async def test_trend_routes_to_analytics_trend(self):
        """query_type=trend → erp_analytics_trend.query_trend"""
        trend_data = [
            {"period": "2026-04-01", "order_count": 10, "order_amount": 5000},
            {"period": "2026-04-02", "order_count": 15, "order_amount": 7500},
        ]
        db = _mock_db([])
        rpc_resp = MagicMock()
        rpc_resp.data = trend_data
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
        engine = _make_engine(db)

        result = await engine.execute(
            doc_type="daily_stats", mode="summary",
            filters=[{"field": "stat_date", "op": "gte", "value": "2026-04-01"},
                     {"field": "stat_date", "op": "lt", "value": "2026-04-28"}],
            query_type="trend",
            time_granularity="day",
            metrics=["order_amount"],
        )
        # 验证调用了 erp_trend_query RPC
        db.rpc.assert_called()
        call_args = db.rpc.call_args
        assert call_args[0][0] == "erp_trend_query"

    @pytest.mark.asyncio
    async def test_trend_auto_fills_time_range(self):
        """趋势查询无时间范围时自动补最近30天"""
        db = _mock_db([])
        rpc_resp = MagicMock()
        rpc_resp.data = []
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
        engine = _make_engine(db)

        # 不传 time filters → 应该自动补默认30天
        result = await engine.execute(
            doc_type="daily_stats", mode="summary",
            filters=[],
            query_type="trend",
            time_granularity="day",
        )
        # 不应该报错（不再拦截）
        assert result.status != "error" or "时间范围" not in (result.summary or "")


# ============================================================
# 5. Compare（对比分析）
# ============================================================


class TestCompareQuery:

    @pytest.mark.asyncio
    async def test_compare_routes_to_query_compare(self):
        """query_type=compare → erp_analytics_trend.query_compare"""
        db = _mock_db([])
        rpc_resp = MagicMock()
        rpc_resp.data = {"doc_count": 10, "total_amount": 5000}
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
        engine = _make_engine(db)

        result = await engine.execute(
            doc_type="order", mode="summary",
            filters=[{"field": "doc_created_at", "op": "gte", "value": "2026-04-01"},
                     {"field": "doc_created_at", "op": "lt", "value": "2026-04-28"}],
            query_type="compare",
            compare_range="mom",
            metrics=["amount"],
        )
        # compare 调两次 RPC
        assert db.rpc.call_count >= 1


# ============================================================
# 6. Ratio（占比/排名/ABC）
# ============================================================


class TestRatioQuery:

    @pytest.mark.asyncio
    async def test_ratio_computes_abc(self):
        """query_type=ratio → 占比计算 + ABC 分类"""
        rpc_data = [
            {"group_key": "淘宝", "doc_count": 100, "total_qty": 500, "total_amount": 80000},
            {"group_key": "京东", "doc_count": 30, "total_qty": 100, "total_amount": 15000},
            {"group_key": "拼多多", "doc_count": 10, "total_qty": 30, "total_amount": 5000},
        ]
        db = _mock_db([])
        rpc_resp = MagicMock()
        rpc_resp.data = rpc_data
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
        engine = _make_engine(db)

        result = await engine.execute(
            doc_type="order", mode="summary",
            filters=[{"field": "doc_created_at", "op": "gte", "value": "2026-04-01"},
                     {"field": "doc_created_at", "op": "lt", "value": "2026-04-28"}],
            query_type="ratio",
            group_by=["platform"],
            metrics=["amount"],
        )
        assert result.data is not None
        assert len(result.data) == 3
        # 淘宝 80000/100000 = 80% → A类
        assert result.data[0]["abc_class"] == "A"
        assert result.data[0]["ratio"] == 80.0
        # 累计占比
        assert result.data[0]["cumulative_ratio"] == 80.0
        assert result.metadata.get("query_type") == "ratio"

    @pytest.mark.asyncio
    async def test_ratio_requires_group_by(self):
        """占比分析必须有 group_by"""
        engine = _make_engine()
        result = await engine.execute(
            doc_type="order", mode="summary",
            filters=[{"field": "doc_created_at", "op": "gte", "value": "2026-04-01"},
                     {"field": "doc_created_at", "op": "lt", "value": "2026-04-28"}],
            query_type="ratio",
        )
        assert result.status in ("error", "ERROR")


# ============================================================
# 7. Cross（跨域指标）
# ============================================================


class TestCrossQuery:

    @pytest.mark.asyncio
    async def test_cross_routes_to_analytics_cross(self):
        """query_type=cross → erp_analytics_cross.query_cross"""
        cross_data = [
            {"group_key": "tb", "metric_value": 3.5, "numerator": 35, "denominator": 1000},
        ]
        db = _mock_db([])
        rpc_resp = MagicMock()
        rpc_resp.data = cross_data
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
        engine = _make_engine(db)

        result = await engine.execute(
            doc_type="daily_stats", mode="summary",
            filters=[{"field": "stat_date", "op": "gte", "value": "2026-04-01"},
                     {"field": "stat_date", "op": "lt", "value": "2026-04-28"}],
            query_type="cross",
            metrics=["return_rate"],
            group_by=["platform"],
        )
        db.rpc.assert_called()
        call_args = db.rpc.call_args
        assert call_args[0][0] == "erp_cross_metric_query"


# ============================================================
# 8. Alert（预警查询）
# ============================================================


class TestAlertQuery:

    @pytest.mark.asyncio
    async def test_alert_routes_to_analytics_alert(self):
        """query_type=alert → erp_analytics_alert.query_alert"""
        stock_rows = [
            {"outer_id": "P001", "item_name": "商品A", "available_qty": 3},
            {"outer_id": "P002", "item_name": "商品B", "available_qty": 50},
        ]
        daily_rows = [
            {"outer_id": "P001", "order_qty": 30},
            {"outer_id": "P002", "order_qty": 5},
        ]

        db = _mock_db(stock_rows, count=2)
        engine = _make_engine(db)

        with patch("services.kuaimai.erp_analytics_alert.query_alert", new_callable=AsyncMock) as mock_alert:
            from services.agent.tool_output import ToolOutput
            mock_alert.return_value = ToolOutput(
                summary="缺货预警：2 个商品库存告急",
                data=[{"outer_id": "P001", "days_left": 2, "severity": "critical"}],
                metadata={"query_type": "alert", "alert_type": "low_stock"},
            )
            result = await engine.execute(
                doc_type="stock", mode="summary", filters=[],
                query_type="alert",
                alert_type="low_stock",
            )
            mock_alert.assert_called_once()
            assert result.metadata.get("alert_type") == "low_stock"


# ============================================================
# 9. Distribution（分布直方图）
# ============================================================


class TestDistributionQuery:

    @pytest.mark.asyncio
    async def test_distribution_routes_to_analytics(self):
        """query_type=distribution → erp_analytics_distribution.query_distribution"""
        dist_data = [
            {"bucket": "0-50", "count": 120, "bucket_total": 3500},
            {"bucket": "50-100", "count": 85, "bucket_total": 6200},
        ]
        db = _mock_db([])
        rpc_resp = MagicMock()
        rpc_resp.data = dist_data
        db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
        engine = _make_engine(db)

        result = await engine.execute(
            doc_type="order", mode="summary",
            filters=[{"field": "doc_created_at", "op": "gte", "value": "2026-04-01"},
                     {"field": "doc_created_at", "op": "lt", "value": "2026-04-28"}],
            query_type="distribution",
            metrics=["amount"],
        )
        db.rpc.assert_called()
        call_args = db.rpc.call_args
        assert call_args[0][0] == "erp_distribution_query"


# ============================================================
# 10. Agent 链路参数透传
# ============================================================


class TestAgentParamPassthrough:
    """验证 5 个新参数从 DepartmentAgent 透传到 engine.execute()。"""

    def test_query_kwargs_includes_new_params(self):
        """_query_kwargs 透传 v2.2 分析类参数"""
        from services.agent.department_agent import DepartmentAgent

        params = {
            "mode": "summary",
            "filters": [],
            "query_type": "trend",
            "time_granularity": "day",
            "compare_range": "mom",
            "metrics": ["return_rate"],
            "alert_type": "low_stock",
        }
        kw = DepartmentAgent._query_kwargs(params)

        assert kw["query_type"] == "trend"
        assert kw["time_granularity"] == "day"
        assert kw["compare_range"] == "mom"
        assert kw["metrics"] == ["return_rate"]
        assert kw["alert_type"] == "low_stock"

    def test_query_kwargs_omits_none_values(self):
        """None 值不透传"""
        from services.agent.department_agent import DepartmentAgent

        params = {"mode": "summary", "filters": [], "query_type": None}
        kw = DepartmentAgent._query_kwargs(params)
        assert "query_type" not in kw

    def test_warehouse_analytics_route(self):
        """WarehouseAgent 分析类查询走 _ANALYTICS_QUERY_TYPES 分支"""
        from services.agent.departments.warehouse_agent import WarehouseAgent
        assert "trend" in WarehouseAgent._ANALYTICS_QUERY_TYPES
        assert "cross" in WarehouseAgent._ANALYTICS_QUERY_TYPES
        assert "alert" in WarehouseAgent._ANALYTICS_QUERY_TYPES
        assert "distribution" in WarehouseAgent._ANALYTICS_QUERY_TYPES


# ============================================================
# 11. PlanBuilder 参数校验
# ============================================================


class TestPlanBuilderSanitize:
    """验证 _sanitize_params 对新参数的白名单校验。"""

    def _sanitize(self, params: dict) -> dict:
        from services.agent.plan_builder import _sanitize_params
        return _sanitize_params(params)

    def test_valid_query_type_passthrough(self):
        clean = self._sanitize({"query_type": "trend"})
        assert clean["query_type"] == "trend"

    def test_invalid_query_type_dropped(self):
        clean = self._sanitize({"query_type": "hacker"})
        assert "query_type" not in clean

    def test_valid_time_granularity(self):
        clean = self._sanitize({"time_granularity": "week"})
        assert clean["time_granularity"] == "week"

    def test_invalid_time_granularity_dropped(self):
        clean = self._sanitize({"time_granularity": "year"})
        assert "time_granularity" not in clean

    def test_valid_compare_range(self):
        clean = self._sanitize({"compare_range": "mom"})
        assert clean["compare_range"] == "mom"

    def test_valid_metrics_list(self):
        clean = self._sanitize({"metrics": ["return_rate", "gross_margin"]})
        assert clean["metrics"] == ["return_rate", "gross_margin"]

    def test_metrics_string_to_list(self):
        clean = self._sanitize({"metrics": "return_rate"})
        assert clean["metrics"] == ["return_rate"]

    def test_invalid_metrics_filtered(self):
        clean = self._sanitize({"metrics": ["return_rate", "hacker", "gross_margin"]})
        assert "hacker" not in clean["metrics"]
        assert "return_rate" in clean["metrics"]

    def test_valid_alert_type(self):
        clean = self._sanitize({"alert_type": "low_stock"})
        assert clean["alert_type"] == "low_stock"

    def test_invalid_alert_type_dropped(self):
        clean = self._sanitize({"alert_type": "nuke_db"})
        assert "alert_type" not in clean


# ============================================================
# 12. plan_fill 关键词兜底
# ============================================================


class TestPlanFillQueryType:
    """验证 fill_query_type 从文本关键词推断 query_type。"""

    def _fill(self, query: str, params: dict | None = None) -> dict:
        from services.agent.plan_fill import fill_query_type
        p = params or {}
        fill_query_type(p, query)
        return p

    def test_alert_keywords(self):
        assert self._fill("哪些商品快缺货了")["query_type"] == "alert"
        assert self._fill("滞销商品有哪些")["query_type"] == "alert"
        assert self._fill("采购超期未到货")["query_type"] == "alert"

    def test_cross_keywords(self):
        assert self._fill("各平台退货率")["query_type"] == "cross"
        assert self._fill("毛利率多少")["query_type"] == "cross"
        assert self._fill("库存周转天数")["query_type"] == "cross"
        assert self._fill("发货时效")["query_type"] == "cross"

    def test_trend_keywords(self):
        assert self._fill("每天的销售额趋势")["query_type"] == "trend"
        assert self._fill("每月看订单量")["query_type"] == "trend"

    def test_compare_keywords(self):
        assert self._fill("环比增长率")["query_type"] == "compare"
        assert self._fill("这个月比上个月怎么样")["query_type"] == "compare"

    def test_ratio_keywords(self):
        assert self._fill("各平台销售额占比")["query_type"] == "ratio"
        assert self._fill("商品ABC分类")["query_type"] == "ratio"

    def test_distribution_keywords(self):
        assert self._fill("订单金额分布")["query_type"] == "distribution"

    def test_no_match_leaves_empty(self):
        assert "query_type" not in self._fill("4月订单总金额")

    def test_existing_query_type_not_overwritten(self):
        p = self._fill("退货率趋势", {"query_type": "trend"})
        assert p["query_type"] == "trend"  # 已有值不覆盖

    def test_alert_fills_alert_type(self):
        p = self._fill("哪些商品滞销了")
        assert p["query_type"] == "alert"
        assert p["alert_type"] == "slow_moving"

    def test_cross_fills_metric(self):
        p = self._fill("客单价多少")
        assert p["query_type"] == "cross"
        assert p["metrics"] == ["avg_order_value"]


# ============================================================
# 13. SQL 兜底安全校验
# ============================================================


class TestSqlFallbackValidation:

    def _validate(self, sql: str, org_id: str = "eadc4c11-7e83-4279-a849-cfe0cbf6982b") -> tuple[bool, str]:
        from services.kuaimai.erp_sql_fallback import validate_generated_sql
        return validate_generated_sql(sql, org_id)

    def test_valid_select(self):
        sql = "SELECT * FROM erp_document_items WHERE org_id = 'eadc4c11-7e83-4279-a849-cfe0cbf6982b' LIMIT 100"
        ok, err = self._validate(sql)
        assert ok, err

    def test_valid_with_cte(self):
        sql = "WITH t AS (SELECT 1) SELECT * FROM t WHERE org_id = 'eadc4c11-7e83-4279-a849-cfe0cbf6982b' LIMIT 10"
        ok, err = self._validate(sql)
        assert ok, err

    def test_reject_delete(self):
        sql = "DELETE FROM erp_document_items WHERE org_id = 'eadc4c11-7e83-4279-a849-cfe0cbf6982b' LIMIT 10"
        ok, err = self._validate(sql)
        assert not ok
        assert "危险" in err

    def test_reject_drop(self):
        sql = "DROP TABLE erp_document_items"
        ok, err = self._validate(sql)
        assert not ok

    def test_reject_missing_org_id(self):
        sql = "SELECT * FROM erp_document_items LIMIT 100"
        ok, err = self._validate(sql)
        assert not ok
        assert "org_id" in err

    def test_reject_wrong_org_id(self):
        sql = "SELECT * FROM erp_document_items WHERE org_id = 'other-org' LIMIT 100"
        ok, err = self._validate(sql)
        assert not ok

    def test_reject_missing_limit(self):
        sql = "SELECT * FROM erp_document_items WHERE org_id = 'eadc4c11-7e83-4279-a849-cfe0cbf6982b'"
        ok, err = self._validate(sql)
        assert not ok
        assert "LIMIT" in err

    def test_reject_large_limit(self):
        sql = "SELECT * FROM erp_document_items WHERE org_id = 'eadc4c11-7e83-4279-a849-cfe0cbf6982b' LIMIT 50000"
        ok, err = self._validate(sql)
        assert not ok
        assert "1000" in err

    def test_reject_insert_in_cte(self):
        sql = "WITH x AS (INSERT INTO t VALUES(1)) SELECT 1 WHERE org_id = 'eadc4c11-7e83-4279-a849-cfe0cbf6982b' LIMIT 1"
        ok, err = self._validate(sql)
        assert not ok


# ============================================================
# 14. SQL 兜底触发条件
# ============================================================


class TestSqlFallbackTrigger:

    def _should_try(self, status: str, summary: str = "", error: str = "", metadata: dict | None = None) -> bool:
        from services.kuaimai.erp_sql_fallback import should_try_sql

        class FakeResult:
            pass
        r = FakeResult()
        r.status = status
        r.summary = summary
        r.error_message = error
        r.metadata = metadata or {}
        return should_try_sql(r, "test query")

    def test_triggers_on_error(self):
        assert self._should_try("error", "查询失败")

    def test_triggers_on_empty(self):
        assert self._should_try("empty", "无匹配记录")

    def test_not_trigger_on_success(self):
        assert not self._should_try("success")

    def test_not_trigger_on_timeout(self):
        assert not self._should_try("error", "查询超时")
        assert not self._should_try("timeout", "超时")

    def test_not_trigger_on_param_error(self):
        assert not self._should_try("error", "参数错误")

    def test_not_trigger_on_alert(self):
        assert not self._should_try("error", "预警查询失败", metadata={"query_type": "alert"})


# ============================================================
# 15. erp_tool_description 能力清单
# ============================================================


class TestToolDescription:

    def test_query_types_in_manifest(self):
        from services.agent.erp_tool_description import get_capability_manifest
        m = get_capability_manifest()
        qt = m.get("query_types", {})
        for t in ("summary", "trend", "compare", "ratio", "cross", "alert", "distribution", "detail", "export"):
            assert t in qt, f"缺少 query_type: {t}"

    def test_cross_metrics_in_manifest(self):
        from services.agent.erp_tool_description import get_capability_manifest
        m = get_capability_manifest()
        metrics = m.get("cross_metrics", [])
        assert len(metrics) >= 10
        metric_text = " ".join(metrics)
        for key in ("return_rate", "gross_margin", "avg_order_value", "inventory_turnover"):
            assert key in metric_text, f"缺少 cross_metric: {key}"

    def test_alert_types_in_manifest(self):
        from services.agent.erp_tool_description import get_capability_manifest
        m = get_capability_manifest()
        alerts = m.get("alert_types", [])
        assert len(alerts) == 5
        alert_text = " ".join(alerts)
        for key in ("low_stock", "slow_moving", "overstock", "out_of_stock", "purchase_overdue"):
            assert key in alert_text, f"缺少 alert_type: {key}"

    def test_description_includes_analytics(self):
        from services.agent.erp_tool_description import build_tool_description
        desc = build_tool_description()
        for keyword in ("趋势分析", "对比分析", "占比分析", "跨域指标", "预警查询", "分布分析"):
            assert keyword in desc, f"描述缺少: {keyword}"
