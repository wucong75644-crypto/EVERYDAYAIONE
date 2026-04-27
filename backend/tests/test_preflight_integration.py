"""预检防御层集成测试

验证 _export() 内部的预检门卫：导出量超限时拒绝。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.kuaimai.erp_query_preflight import EXPORT_ROW_LIMIT


class TestPreflightGateInExport:
    """预检门卫在 _export 内部，DuckDB 子进程启动前"""

    @pytest.mark.asyncio
    async def test_large_limit_rejected_before_duckdb(self):
        """limit > 上限 → 拒绝，DuckDB 子进程不启动"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = MagicMock()
        rpc_response = MagicMock()
        rpc_response.data = {"doc_count": 10, "total_qty": 50, "total_amount": 1000}
        db.rpc.return_value.execute.return_value = rpc_response

        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch("services.kuaimai.erp_export_subprocess.subprocess_export",
                    new_callable=AsyncMock) as mock_sub:
            result = await engine.execute(
                doc_type="order", mode="export",
                limit=EXPORT_ROW_LIMIT + 1,
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )
            mock_sub.assert_not_called()
            assert str(result.status) == "rejected"
            assert "导出行数过大" in result.summary

    @pytest.mark.asyncio
    async def test_small_limit_allowed(self):
        """limit=5 → 预检放行"""
        from services.kuaimai.erp_query_preflight import preflight_check
        result = preflight_check("export", limit=5)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_default_limit_allowed(self):
        """默认 limit=20 → 放行"""
        from services.kuaimai.erp_query_preflight import preflight_check
        result = preflight_check("export", limit=20)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_summary_not_blocked(self):
        """summary 模式不走 _export，不受预检影响"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        db = MagicMock()
        rpc_response = MagicMock()
        rpc_response.data = {"doc_count": 10, "total_qty": 50, "total_amount": 1000}
        db.rpc.return_value.execute.return_value = rpc_response

        engine = UnifiedQueryEngine(db=db, org_id="org-1")

        with patch.object(engine, "_summary", new_callable=AsyncMock) as mock_summary:
            mock_summary.return_value = MagicMock(summary="summary ok")
            await engine.execute(
                doc_type="order", mode="summary",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-01"},
                         {"field": "pay_time", "op": "lt", "value": "2026-05-01"}],
            )
            mock_summary.assert_called_once()


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
            summary="导出行数过大（请求 50,000 行，上限 30,000 行）",
            source="erp",
            status=OutputStatus.REJECTED,
            metadata={"suggestions": ["缩小时间范围", "减少 limit"]},
        )

        plan = ExecutionPlan(steps=[PlanStep("trade", {})])
        result = agent._build_multi_result(
            [("trade", rejected)], plan, "导出全部",
        )
        assert result.status == "error"
        assert "导出行数过大" in result.summary
