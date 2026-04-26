"""预检防御层集成测试

模拟完整的 execute() → preflight → 路由分发 链路，
验证不同 EXPLAIN 预估行数下路由到正确的执行路径。

不测试各路径内部逻辑（已有单元测试），只测路由决策是否正确触发。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from dataclasses import dataclass

from services.kuaimai.erp_query_preflight import (
    BATCH_THRESHOLD,
    FAST_THRESHOLD,
    REJECT_THRESHOLD,
    PreflightResult,
    QueryRoute,
)


def _make_db_with_explain(plan_rows: int):
    """构造 mock db，EXPLAIN 返回指定 plan_rows，RPC/query 也 mock。"""
    # EXPLAIN 连接
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = {
        "QUERY PLAN": [{"Plan": {"Plan Rows": plan_rows}}]
    }
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    db = MagicMock()
    db.pool = mock_pool

    # RPC（summary 路径用）
    rpc_response = MagicMock()
    rpc_response.data = {"doc_count": 10, "total_qty": 50, "total_amount": 1000}
    db.rpc.return_value.execute.return_value = rpc_response

    return db


# ── 路由分发集成测试 ──────────────────────────────


class TestPreflightRouting:
    """execute() 根据 EXPLAIN 预估行数路由到正确路径"""

    @pytest.mark.asyncio
    async def test_small_export_routes_to_fast_export(self):
        """预估 500 行 + export → 走 _fast_export（不走 DuckDB）"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(500)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast, \
             patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb:
            mock_fast.return_value = MagicMock(summary="fast result")

            result = await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-25"},
                         {"field": "pay_time", "op": "lt", "value": "2026-04-26"}],
                limit=5,
            )

            mock_fast.assert_called_once()
            mock_duckdb.assert_not_called()

    @pytest.mark.asyncio
    async def test_small_summary_routes_to_rpc(self):
        """预估 500 行 + summary → 走 _summary（RPC），不走 _fast_export"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(500)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_summary", new_callable=AsyncMock) as mock_summary, \
             patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast:
            mock_summary.return_value = MagicMock(summary="summary result")

            result = await engine.execute(
                doc_type="order", mode="summary",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-25"},
                         {"field": "pay_time", "op": "lt", "value": "2026-04-26"}],
            )

            mock_summary.assert_called_once()
            mock_fast.assert_not_called()

    @pytest.mark.asyncio
    async def test_medium_export_routes_to_standard_duckdb(self):
        """预估 15,000 行 + export → 走标准 _export（单次 DuckDB）"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(15_000)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast, \
             patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb, \
             patch.object(engine, "_batch_export", new_callable=AsyncMock) as mock_batch:
            mock_duckdb.return_value = MagicMock(summary="duckdb result")

            result = await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-04-15"}],
            )

            mock_fast.assert_not_called()
            mock_batch.assert_not_called()
            mock_duckdb.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_export_routes_to_batch(self):
        """预估 100,000 行 + export → 走 _batch_export"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(100_000)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast, \
             patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb, \
             patch.object(engine, "_batch_export", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = MagicMock(summary="batch result")

            result = await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )

            mock_fast.assert_not_called()
            mock_duckdb.assert_not_called()
            mock_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_summary_still_routes_to_rpc(self):
        """预估 100,000 行 + summary → 仍走 _summary（RPC 不受行数影响）"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(100_000)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_summary", new_callable=AsyncMock) as mock_summary, \
             patch.object(engine, "_batch_export", new_callable=AsyncMock) as mock_batch:
            mock_summary.return_value = MagicMock(summary="summary result")

            result = await engine.execute(
                doc_type="order", mode="summary",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )

            mock_summary.assert_called_once()
            mock_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_reject_threshold_returns_rejected(self):
        """预估 > 500 万行 → 直接返回 REJECTED，不调用任何查询"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        from services.agent.tool_output import OutputStatus

        db = _make_db_with_explain(REJECT_THRESHOLD + 1)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast, \
             patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb, \
             patch.object(engine, "_batch_export", new_callable=AsyncMock) as mock_batch, \
             patch.object(engine, "_summary", new_callable=AsyncMock) as mock_summary:

            result = await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-01-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-12-31"}],
            )

            # 所有执行路径都不应被调用
            mock_fast.assert_not_called()
            mock_duckdb.assert_not_called()
            mock_batch.assert_not_called()
            mock_summary.assert_not_called()

            # 返回 REJECTED 状态（ToolOutput 经 format_filter_hint 出口后 status 为字符串）
            assert str(result.status) == "rejected"
            assert "数据量过大" in result.summary
            assert result.metadata["suggestions"]

    @pytest.mark.asyncio
    async def test_explain_failure_fallback_to_standard(self):
        """EXPLAIN 失败 → 静默降级走标准路径"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = MagicMock()
        # EXPLAIN 会抛异常
        db.pool.connection.side_effect = Exception("connection lost")
        # RPC 正常
        rpc_response = MagicMock()
        rpc_response.data = {"doc_count": 10, "total_qty": 50, "total_amount": 1000}
        db.rpc.return_value.execute.return_value = rpc_response

        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb:
            mock_duckdb.return_value = MagicMock(summary="fallback result")

            result = await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )

            # 降级到标准 DuckDB 路径
            mock_duckdb.assert_called_once()


# ── REJECTED 端到端：ERPAgent → 主 Agent ──────────


class TestRejectedE2E:
    """REJECTED 从 UnifiedQueryEngine → ERPAgent → 主 Agent 的完整传播"""

    @pytest.mark.asyncio
    async def test_rejected_propagates_through_erp_agent(self):
        """execute() 返回 REJECTED → ERPAgent._build_multi_result → error + suggestions"""
        from services.agent.erp_agent import ERPAgent, PlanStep, ExecutionPlan
        from services.agent.tool_output import OutputStatus, ToolOutput

        agent = ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

        # 模拟 UnifiedQueryEngine 返回 REJECTED
        rejected_output = ToolOutput(
            summary="数据量过大（预估 6,000,000 行）",
            source="erp",
            status=OutputStatus.REJECTED,
            metadata={
                "estimated_rows": 6_000_000,
                "suggestions": ["缩小时间范围", "添加过滤条件"],
            },
        )

        plan = ExecutionPlan(steps=[PlanStep("trade", {"mode": "export"})])
        result = agent._build_multi_result(
            [("trade", rejected_output)], plan, "导出今年全部订单",
        )

        # REJECTED 被归入 errors → status="error"
        assert result.status == "error"
        assert "数据量过大" in result.summary
        assert "缩小时间范围" in result.summary
        assert "添加过滤条件" in result.summary


# ── 边界场景 ──────────────────────────────────────


class TestPreflightEdgeCases:
    """预检层边界场景"""

    @pytest.mark.asyncio
    async def test_threshold_boundary_fast_to_standard(self):
        """恰好 FAST_THRESHOLD 行 → 走标准路径（不是快路径）"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(FAST_THRESHOLD)  # 恰好 1000
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast, \
             patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb:
            mock_duckdb.return_value = MagicMock(summary="standard")

            await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-04-10"}],
            )

            mock_fast.assert_not_called()
            mock_duckdb.assert_called_once()

    @pytest.mark.asyncio
    async def test_threshold_boundary_standard_to_batch(self):
        """恰好 BATCH_THRESHOLD 行 → 走标准路径（不是分批）"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(BATCH_THRESHOLD)  # 恰好 30000
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb, \
             patch.object(engine, "_batch_export", new_callable=AsyncMock) as mock_batch:
            mock_duckdb.return_value = MagicMock(summary="standard")

            await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-04-20"}],
            )

            mock_batch.assert_not_called()
            mock_duckdb.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_threshold_plus_one_goes_batch(self):
        """BATCH_THRESHOLD + 1 → 走分批"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(BATCH_THRESHOLD + 1)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_export", new_callable=AsyncMock) as mock_duckdb, \
             patch.object(engine, "_batch_export", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = MagicMock(summary="batch")

            await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )

            mock_duckdb.assert_not_called()
            mock_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_null_org_id(self):
        """org_id=None → EXPLAIN 用 IS NULL，路由正常工作"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(500)
        engine = UnifiedQueryEngine(db=db, org_id=None)

        with patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast:
            mock_fast.return_value = MagicMock(summary="fast")

            await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-25"},
                         {"field": "pay_time", "op": "lt", "value": "2026-04-26"}],
            )

            mock_fast.assert_called_once()

    @pytest.mark.asyncio
    async def test_filters_passed_to_fast_path(self):
        """非时间过滤条件（platform=tb）传递到快路径"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(200)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_fast_export", new_callable=AsyncMock) as mock_fast:
            mock_fast.return_value = MagicMock(summary="fast filtered")

            await engine.execute(
                doc_type="order", mode="export",
                filters=[
                    {"field": "pay_time", "op": "gte", "value": "2026-04-25"},
                    {"field": "pay_time", "op": "lt", "value": "2026-04-26"},
                    {"field": "platform", "op": "eq", "value": "tb"},
                ],
            )

            mock_fast.assert_called_once()
            # 验证 filters 参数包含 platform 过滤
            call_kwargs = mock_fast.call_args
            filters_arg = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get("filters", [])
            platform_filters = [f for f in filters_arg if f.field == "platform"]
            assert len(platform_filters) == 1
            assert platform_filters[0].value == "tb"

    @pytest.mark.asyncio
    async def test_batch_receives_estimated_rows(self):
        """分批路径接收 estimated_rows 参数用于计算切片数"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        estimated = 90_000
        db = _make_db_with_explain(estimated)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_batch_export", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = MagicMock(summary="batch")

            await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )

            # 验证 estimated_rows 被传入
            call_kwargs = mock_batch.call_args[1]
            assert call_kwargs["estimated_rows"] == estimated
