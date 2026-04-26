"""预检防御层集成测试

验证 _export() 内部的预检门卫：DuckDB 启动前拦截大数据量请求。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.kuaimai.erp_query_preflight import EXPORT_ROW_LIMIT


def _make_db_with_explain(plan_rows: int):
    """构造 mock db，EXPLAIN 返回指定 plan_rows。"""
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

    rpc_response = MagicMock()
    rpc_response.data = {"doc_count": 10, "total_qty": 50, "total_amount": 1000}
    db.rpc.return_value.execute.return_value = rpc_response

    return db


class TestPreflightGateInExport:
    """预检门卫在 _export 内部，DuckDB 子进程启动前"""

    @pytest.mark.asyncio
    async def test_large_data_rejected_before_duckdb(self):
        """预估 > 阈值 → 拒绝，DuckDB 子进程不启动"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        from services.agent.tool_output import OutputStatus

        db = _make_db_with_explain(300_000)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch("services.kuaimai.erp_export_subprocess.subprocess_export",
                    new_callable=AsyncMock) as mock_sub:
            result = await engine.execute(
                doc_type="order", mode="export",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )
            # DuckDB 子进程不应被调用
            mock_sub.assert_not_called()
            # 返回 REJECTED
            assert str(result.status) == "rejected"
            assert "数据量过大" in result.summary

    @pytest.mark.asyncio
    async def test_small_limit_also_rejected(self):
        """预估 > 阈值 + limit=5 → 仍然拒绝"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(300_000)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch("services.kuaimai.erp_export_subprocess.subprocess_export",
                    new_callable=AsyncMock) as mock_sub:
            result = await engine.execute(
                doc_type="order", mode="export", limit=5,
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )
            mock_sub.assert_not_called()
            assert "数据量过大" in result.summary

    @pytest.mark.asyncio
    async def test_boundary_at_limit_allowed(self):
        """恰好等于阈值 → 放行（只拦超过的）"""
        from services.kuaimai.erp_query_preflight import preflight_check
        db = _make_db_with_explain(EXPORT_ROW_LIMIT)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_boundary_above_limit_rejected(self):
        """阈值 + 1 → 拒绝"""
        from services.kuaimai.erp_query_preflight import preflight_check
        db = _make_db_with_explain(EXPORT_ROW_LIMIT + 1)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_summary_not_blocked(self):
        """summary 模式不走 _export，不受预检影响"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = _make_db_with_explain(300_000)
        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_summary", new_callable=AsyncMock) as mock_summary:
            mock_summary.return_value = MagicMock(summary="summary ok")
            await engine.execute(
                doc_type="order", mode="summary",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )
            mock_summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_explain_failure_allows_execution(self):
        """EXPLAIN 失败 → 放行（预检结果 ok=True）"""
        from services.kuaimai.erp_query_preflight import preflight_check
        db = MagicMock()
        db.pool.connection.side_effect = Exception("connection lost")
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is True


class TestRejectedPropagation:
    """REJECTED 通过 ERPAgent 透传到主 Agent"""

    @pytest.mark.asyncio
    async def test_rejected_becomes_error_in_erp_agent(self):
        from services.agent.erp_agent import ERPAgent, PlanStep, ExecutionPlan
        from services.agent.tool_output import OutputStatus, ToolOutput

        agent = ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

        rejected = ToolOutput(
            summary="数据量过大（预估 300,000 行，上限 30,000 行），导出可能超时失败",
            source="erp",
            status=OutputStatus.REJECTED,
            metadata={
                "estimated_rows": 300_000,
                "suggestions": ["缩小时间范围", "添加过滤条件"],
            },
        )

        plan = ExecutionPlan(steps=[PlanStep("trade", {"mode": "export"})])
        result = agent._build_multi_result(
            [("trade", rejected)], plan, "导出本月全部订单",
        )

        assert result.status == "error"
        assert "数据量过大" in result.summary
        assert "缩小时间范围" in result.summary
