"""
v2.2 全链路 E2E 测试——从 WarehouseAgent.execute() 走到 RPC/ORM 返回。

中间不 mock Agent 层，只 mock 数据库返回。验证：
  WarehouseAgent.execute(dag_mode=True, params={query_type=..., ...})
    → _dispatch() 识别分析类查询
    → _query_local_data() 透传 5 个新参数
    → UnifiedQueryEngine.execute() 路由到对应分析模块
    → 分析模块调用 RPC/ORM → 返回 ToolOutput

设计文档: docs/document/TECH_ERP查询架构重构.md §19, §21
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


# ── 支持多表的 mock 工厂 ──────────────────────────────


def _mock_multi_table_db(table_data: dict[str, list[dict]]):
    """构造支持多表查询的 DB mock。

    table_data: {"erp_stock_status": [rows], "erp_product_daily_stats": [rows]}
    每张表有独立的 ORM 链和 execute 返回。
    同时支持 db.rpc("func_name", params).execute() 调用。
    """
    rpc_responses: dict[str, list] = {}

    def _make_chain(rows: list[dict], count: int | None = None):
        resp = MagicMock()
        resp.data = rows
        resp.count = count if count is not None else len(rows)
        q = MagicMock()
        for method in ("eq", "is_", "gte", "gt", "lt", "lte", "neq",
                        "ilike", "in_", "order", "limit", "select"):
            setattr(q, method, MagicMock(return_value=q))
        q.not_ = MagicMock()
        q.not_.in_ = MagicMock(return_value=q)
        q.not_.is_ = MagicMock(return_value=q)
        q.execute = MagicMock(return_value=resp)
        return q

    def _table_factory(table_name: str):
        rows = table_data.get(table_name, [])
        return _make_chain(rows)

    db = MagicMock()
    db.table = MagicMock(side_effect=_table_factory)

    # RPC mock: 按函数名返回预设数据
    def _rpc_factory(func_name: str, params: dict | None = None):
        resp = MagicMock()
        resp.data = rpc_responses.get(func_name, [])
        return MagicMock(execute=MagicMock(return_value=resp))

    db.rpc = MagicMock(side_effect=_rpc_factory)
    db._rpc_responses = rpc_responses  # 外部可设置
    return db


def _make_warehouse(db, org_id="test-org"):
    from services.agent.departments.warehouse_agent import WarehouseAgent
    return WarehouseAgent(db=db, org_id=org_id)


# ============================================================
# E2E 1: 趋势分析——"每天的销售额趋势"
# ============================================================


class TestFullChainTrend:
    """
    链路: WarehouseAgent.execute(params={query_type=trend, time_granularity=day})
      → _dispatch() 检测 _ANALYTICS_QUERY_TYPES
      → _query_local_data(doc_type="daily_stats", query_type="trend", ...)
      → UnifiedQueryEngine.execute(query_type="trend")
      → _resolve_query_type → "trend"
      → erp_analytics_trend.query_trend()
      → db.rpc("erp_trend_query", ...).execute()
    """

    @pytest.mark.asyncio
    async def test_trend_daily(self):
        trend_rows = [
            {"period": "2026-04-01", "order_count": 42, "order_amount": 15800.00},
            {"period": "2026-04-02", "order_count": 38, "order_amount": 12300.00},
            {"period": "2026-04-03", "order_count": 55, "order_amount": 21000.00},
        ]
        db = _mock_multi_table_db({})
        db._rpc_responses["erp_trend_query"] = trend_rows

        agent = _make_warehouse(db)
        result = await agent.execute(
            "每天的销售额趋势",
            dag_mode=True,
            params={
                "doc_type": "daily_stats",
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-03 23:59",
                "query_type": "trend",
                "time_granularity": "day",
                "metrics": ["order_amount"],
            },
        )

        assert result.status == "success", f"期望 success，实际 {result.status}: {result.summary}"
        # 验证 RPC 调用
        db.rpc.assert_called()
        rpc_name = db.rpc.call_args[0][0]
        assert rpc_name == "erp_trend_query", f"期望调用 erp_trend_query，实际 {rpc_name}"
        # 验证参数透传
        rpc_params = db.rpc.call_args[0][1]
        assert rpc_params["p_granularity"] == "day"
        assert "order_amount" in rpc_params["p_metrics"]
        # 验证返回数据
        assert result.data is not None
        assert len(result.data) >= 3
        assert result.metadata.get("query_type") == "trend"


# ============================================================
# E2E 2: 跨域指标——"各平台退货率"
# ============================================================


class TestFullChainCross:
    """
    链路: WarehouseAgent.execute(params={query_type=cross, metrics=[return_rate]})
      → _dispatch() → _query_local_data(..., query_type="cross")
      → UnifiedQueryEngine.execute(query_type="cross")
      → erp_analytics_cross.query_cross()
      → db.rpc("erp_cross_metric_query", ...).execute()
    """

    @pytest.mark.asyncio
    async def test_return_rate_by_platform(self):
        cross_rows = [
            {"group_key": "tb", "metric_value": 3.2, "numerator": 32, "denominator": 1000},
            {"group_key": "pdd", "metric_value": 5.1, "numerator": 51, "denominator": 1000},
        ]
        db = _mock_multi_table_db({})
        db._rpc_responses["erp_cross_metric_query"] = cross_rows

        agent = _make_warehouse(db)
        result = await agent.execute(
            "各平台退货率",
            dag_mode=True,
            params={
                "doc_type": "daily_stats",
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-27 23:59",
                "query_type": "cross",
                "metrics": ["return_rate"],
                "group_by": "platform",
            },
        )

        assert result.status == "success", f"实际 {result.status}: {result.summary}"
        db.rpc.assert_called()
        rpc_name = db.rpc.call_args[0][0]
        assert rpc_name == "erp_cross_metric_query"
        rpc_params = db.rpc.call_args[0][1]
        assert rpc_params["p_metric"] == "return_rate"


# ============================================================
# E2E 3: 预警查询——"哪些商品快缺货了"
# ============================================================


class TestFullChainAlert:
    """
    链路: WarehouseAgent.execute(params={query_type=alert, alert_type=low_stock})
      → _dispatch() → _query_local_data(..., query_type="alert", alert_type="low_stock")
      → UnifiedQueryEngine.execute(query_type="alert")
      → erp_analytics_alert.query_alert(alert_type="low_stock")
      → _alert_low_stock() → db.table("erp_stock_status") + db.table("erp_product_daily_stats")
    """

    @pytest.mark.asyncio
    async def test_low_stock_alert(self):
        stock_rows = [
            {"outer_id": "P001", "item_name": "商品A", "available_stock": 5,
             "total_stock": 10, "sellable_num": 5},
            {"outer_id": "P002", "item_name": "商品B", "available_stock": 100,
             "total_stock": 100, "sellable_num": 100},
        ]
        daily_rows = [
            {"outer_id": "P001", "order_qty": 30},  # 日均 1 件 → 剩 5 天
            {"outer_id": "P002", "order_qty": 3},    # 日均 0.1 件 → 剩 1000 天
        ]
        db = _mock_multi_table_db({
            "erp_stock_status": stock_rows,
            "erp_product_daily_stats": daily_rows,
        })

        agent = _make_warehouse(db)
        result = await agent.execute(
            "哪些商品快缺货了",
            dag_mode=True,
            params={
                "query_type": "alert",
                "alert_type": "low_stock",
            },
        )

        assert result.status == "success", f"实际 {result.status}: {result.summary}"
        assert result.metadata.get("query_type") == "alert"
        assert result.metadata.get("alert_type") == "low_stock"
        # P001 日均 1 件，剩 5 天 → 应该触发预警
        assert result.data is not None
        if result.data:
            alert_ids = [r["outer_id"] for r in result.data]
            assert "P001" in alert_ids, "P001 库存 5 天应触发预警"


# ============================================================
# E2E 4: 占比分析——"各平台销售额占比"
# ============================================================


class TestFullChainRatio:
    """
    链路: WarehouseAgent.execute(params={query_type=ratio, group_by=[platform]})
      → _dispatch() → _query_local_data(..., query_type="ratio")
      → UnifiedQueryEngine.execute(query_type="ratio")
      → _query_ratio() → _summary() → RPC 聚合
      → compute_ratio() → ABC 分类
    """

    @pytest.mark.asyncio
    async def test_ratio_abc(self):
        """ratio 走 TradeAgent（doc_type=order，RPC 支持 GROUP BY）。"""
        from services.agent.departments.trade_agent import TradeAgent
        rpc_data = [
            {"group_key": "淘宝", "doc_count": 100, "total_qty": 500, "total_amount": 80000},
            {"group_key": "京东", "doc_count": 30, "total_qty": 100, "total_amount": 15000},
            {"group_key": "拼多多", "doc_count": 10, "total_qty": 30, "total_amount": 5000},
        ]
        db = _mock_multi_table_db({})
        db._rpc_responses["erp_global_stats_query"] = rpc_data

        agent = TradeAgent(db=db, org_id="test-org")
        result = await agent.execute(
            "各平台销售额占比",
            dag_mode=True,
            params={
                "doc_type": "order",
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-27 23:59",
                "query_type": "ratio",
                "group_by": ["platform"],
                "metrics": ["amount"],
            },
        )

        assert result.status == "success", f"实际 {result.status}: {result.summary}"
        assert result.data is not None
        assert result.metadata.get("query_type") == "ratio"
        # 淘宝 80000/100000 = 80% → A 类
        top = result.data[0]
        assert top["abc_class"] == "A"
        assert top["ratio"] == 80.0
        assert top["cumulative_ratio"] == 80.0


# ============================================================
# E2E 5: 分布分析——"订单金额分布"
# ============================================================


class TestFullChainDistribution:
    """
    链路: WarehouseAgent.execute(params={query_type=distribution, metrics=[amount]})
      → _dispatch() → _query_local_data(doc_type="order", query_type="distribution")
      → UnifiedQueryEngine.execute(query_type="distribution")
      → erp_analytics_distribution.query_distribution()
      → db.rpc("erp_distribution_query", ...).execute()
    """

    @pytest.mark.asyncio
    async def test_distribution(self):
        """distribution 走 TradeAgent（doc_type=order）。"""
        from services.agent.departments.trade_agent import TradeAgent
        dist_rows = [
            {"bucket": "0-50", "count": 120, "bucket_total": 3500},
            {"bucket": "50-100", "count": 85, "bucket_total": 6200},
            {"bucket": "100-500", "count": 40, "bucket_total": 12000},
        ]
        db = _mock_multi_table_db({})
        db._rpc_responses["erp_distribution_query"] = dist_rows

        agent = TradeAgent(db=db, org_id="test-org")
        result = await agent.execute(
            "订单金额分布",
            dag_mode=True,
            params={
                "doc_type": "order",
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-27 23:59",
                "query_type": "distribution",
                "metrics": ["amount"],
            },
        )

        assert result.status == "success", f"实际 {result.status}: {result.summary}"
        db.rpc.assert_called()
        rpc_name = db.rpc.call_args[0][0]
        assert rpc_name == "erp_distribution_query"
        assert result.data is not None
        assert len(result.data) == 3


# ============================================================
# E2E 6: 参数传递验证——确认 5 个参数从 params 到 RPC 不丢失
# ============================================================


class TestParamPassthroughToRPC:
    """从 WarehouseAgent params 到 RPC 参数，验证无丢失。"""

    @pytest.mark.asyncio
    async def test_trend_params_reach_rpc(self):
        """time_granularity + metrics 从 params 传到 erp_trend_query RPC。"""
        db = _mock_multi_table_db({})
        db._rpc_responses["erp_trend_query"] = [
            {"period": "2026-04-01", "order_count": 10, "order_amount": 5000},
        ]
        agent = _make_warehouse(db)

        await agent.execute(
            "每周销售额趋势",
            dag_mode=True,
            params={
                "doc_type": "daily_stats",
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-27 23:59",
                "query_type": "trend",
                "time_granularity": "week",
                "metrics": ["order_amount", "order_count"],
            },
        )

        db.rpc.assert_called()
        rpc_params = db.rpc.call_args[0][1]
        assert rpc_params["p_granularity"] == "week", "time_granularity 未透传到 RPC"
        assert "order_amount" in rpc_params["p_metrics"], "metrics 未透传到 RPC"
        assert "order_count" in rpc_params["p_metrics"]

    @pytest.mark.asyncio
    async def test_cross_metric_reaches_rpc(self):
        """metrics=[gross_margin] 从 params 传到 erp_cross_metric_query RPC。"""
        db = _mock_multi_table_db({})
        db._rpc_responses["erp_cross_metric_query"] = [
            {"group_key": None, "metric_value": 35.2, "numerator": 35200, "denominator": 100000},
        ]
        agent = _make_warehouse(db)

        await agent.execute(
            "毛利率多少",
            dag_mode=True,
            params={
                "doc_type": "daily_stats",
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-27 23:59",
                "query_type": "cross",
                "metrics": ["gross_margin"],
            },
        )

        db.rpc.assert_called()
        rpc_params = db.rpc.call_args[0][1]
        assert rpc_params["p_metric"] == "gross_margin", "metric 未透传到 RPC"

    @pytest.mark.asyncio
    async def test_alert_type_reaches_handler(self):
        """alert_type=purchase_overdue 从 params 传到 alert 处理器。"""
        db = _mock_multi_table_db({
            "erp_document_items": [],  # 无超期采购单
        })
        agent = _make_warehouse(db)

        result = await agent.execute(
            "采购超期了吗",
            dag_mode=True,
            params={
                "query_type": "alert",
                "alert_type": "purchase_overdue",
            },
        )

        # purchase_overdue 走 ORM 查 erp_document_items
        db.table.assert_any_call("erp_document_items")
        assert result.metadata.get("alert_type") == "purchase_overdue"
