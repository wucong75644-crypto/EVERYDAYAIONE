"""
DAG 全链路集成测试。

覆盖 §10.1 测试矩阵：
- 单域场景（4个）
- 跨2域场景（2个）
- 跨3域场景（1个）
- 错误传播（3个）
- ERPAgent DAG 路径（2个）

全程 mock DB，不依赖外部服务。
设计文档: docs/document/TECH_多Agent单一职责重构.md §10
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.dag_executor import DAGExecutor, DAGResult
from services.agent.execution_plan import ExecutionPlan, Round
from services.agent.plan_builder import PlanBuilder
from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)


# ── Mock DB 工厂 ──

def _mock_db_with_stock(rows=None):
    """创建返回库存数据的 mock DB"""
    db = MagicMock()
    stock_data = rows or [
        {"outer_id": "A001", "sku_outer_id": "A001-01",
         "sellable_num": 30, "total_stock": 50, "lock_stock": 5,
         "purchase_num": 20, "stock_status": 1, "properties_name": "红色",
         "warehouse_id": "WH-1"},
    ]
    # erp_stock_status 查询链
    q = MagicMock()
    q.or_.return_value = q
    q.eq.return_value = q
    q.limit.return_value = q
    q.execute.return_value = MagicMock(data=stock_data)

    # mv_kit_stock（无套件数据）
    kit_q = MagicMock()
    kit_q.or_.return_value = kit_q
    kit_q.eq.return_value = kit_q
    kit_q.limit.return_value = kit_q
    kit_q.execute.return_value = MagicMock(data=[])

    # erp_warehouses
    wh_q = MagicMock()
    wh_q.in_.return_value = wh_q
    wh_q.eq.return_value = wh_q
    wh_q.execute.return_value = MagicMock(data=[
        {"warehouse_id": "WH-1", "name": "主仓"},
    ])

    # erp_sync_state
    sync_q = MagicMock()
    sync_q.in_.return_value = sync_q
    sync_q.execute.return_value = MagicMock(data=[])

    def table_router(name):
        if name == "erp_stock_status":
            return MagicMock(select=MagicMock(return_value=q))
        if name == "mv_kit_stock":
            return MagicMock(select=MagicMock(return_value=kit_q))
        if name == "erp_warehouses":
            return MagicMock(select=MagicMock(return_value=wh_q))
        if name == "erp_sync_state":
            return MagicMock(select=MagicMock(return_value=sync_q))
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.in_.return_value = m
        m.execute.return_value = MagicMock(data=[])
        return m

    db.table = table_router
    return db


# ============================================================
# 单域场景
# ============================================================


class TestSingleDomain:

    @pytest.mark.asyncio
    async def test_warehouse_stock_query(self):
        """单域：查A001库存 → WarehouseAgent"""
        from services.agent.departments.warehouse_agent import WarehouseAgent

        db = _mock_db_with_stock()
        agents = {"warehouse": WarehouseAgent(db=db)}
        plan = ExecutionPlan.single("warehouse", task="查A001库存")
        executor = DAGExecutor(agents=agents, query="查A001库存")

        # 提供 context 带 product_code
        ctx_output = ToolOutput(
            summary="", source="user",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("product_code", "text")],
            data=[{"product_code": "A001"}],
        )
        # 直接调 agent.execute 验证
        result = await agents["warehouse"].execute(
            "查A001库存", context=[ctx_output],
        )
        assert result.status == OutputStatus.OK
        assert result.data is not None
        assert len(result.data) > 0
        assert "库存" in result.summary or "A001" in result.summary

    @pytest.mark.asyncio
    async def test_warehouse_list(self):
        """单域：仓库列表查询"""
        from services.agent.departments.warehouse_agent import WarehouseAgent

        db = MagicMock()
        wh_q = MagicMock()
        wh_q.eq.return_value = wh_q
        wh_q.is_.return_value = wh_q
        wh_q.order.return_value = wh_q
        wh_q.execute.return_value = MagicMock(data=[
            {"warehouse_id": "WH-1", "name": "主仓", "is_virtual": False,
             "warehouse_type": 0, "status": 1},
        ])
        db.table.return_value = MagicMock(select=MagicMock(return_value=wh_q))

        sync_q = MagicMock()
        sync_q.in_.return_value = sync_q
        sync_q.execute.return_value = MagicMock(data=[])
        original_table = db.table

        def table_router(name):
            if name == "erp_sync_state":
                return MagicMock(select=MagicMock(return_value=sync_q))
            return original_table(name)
        db.table = table_router

        agent = WarehouseAgent(db=db)
        result = await agent.execute("仓库列表")
        assert result.status == OutputStatus.OK
        assert "仓库" in result.summary

    @pytest.mark.asyncio
    async def test_parameter_negotiation(self):
        """参数不足 → 返回协商提示"""
        from services.agent.departments.warehouse_agent import WarehouseAgent
        agent = WarehouseAgent(db=MagicMock())
        # stock_query 需要 product_code 或 keyword，无 context → ERROR
        result = await agent.execute("查库存")
        assert result.status == OutputStatus.ERROR
        assert "商品编码" in result.summary


# ============================================================
# 跨域场景（通过 DAGExecutor）
# ============================================================


class TestCrossDomain:

    @pytest.mark.asyncio
    async def test_two_domain_parallel(self):
        """跨2域并行：仓储 + 采购"""
        wh_output = ToolOutput(
            summary="库存数据", source="warehouse",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("product_code", "text"),
                     ColumnMeta("sellable", "integer")],
            data=[{"product_code": "A001", "sellable": 30}],
        )
        pur_output = ToolOutput(
            summary="采购数据", source="purchase",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("product_code", "text"),
                     ColumnMeta("po_no", "text")],
            data=[{"product_code": "A001", "po_no": "PO-001"}],
        )

        wh = MagicMock()
        wh.execute = AsyncMock(return_value=wh_output)
        pur = MagicMock()
        pur.execute = AsyncMock(return_value=pur_output)

        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse", "purchase"], task="查库存和采购"),
        ])
        executor = DAGExecutor(
            agents={"warehouse": wh, "purchase": pur}, query="test",
        )
        result = await executor.run(plan)
        assert result.is_success
        assert len(result.outputs) == 2
        sources = {o.source for o in result.outputs}
        assert sources == {"warehouse", "purchase"}

    @pytest.mark.asyncio
    async def test_three_domain_dag(self):
        """跨3域 DAG：售后→(仓储+采购)→汇总"""
        aftersale_out = ToolOutput(
            summary="退货TOP10", source="aftersale",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("product_code", "text"),
                     ColumnMeta("return_qty", "integer")],
            data=[{"product_code": "A001", "return_qty": 15}],
        )
        wh_out = ToolOutput(
            summary="库存数据", source="warehouse",
            data=[{"product_code": "A001", "sellable": 30}],
        )
        pur_out = ToolOutput(
            summary="采购数据", source="purchase",
            data=[{"product_code": "A001", "onway_qty": 50}],
        )

        agents = {
            "aftersale": MagicMock(execute=AsyncMock(return_value=aftersale_out)),
            "warehouse": MagicMock(execute=AsyncMock(return_value=wh_out)),
            "purchase": MagicMock(execute=AsyncMock(return_value=pur_out)),
        }

        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"], task="查退货TOP10"),
            Round(agents=["warehouse", "purchase"], task="查库存和采购", depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="退货→库存→采购")
        result = await executor.run(plan)
        assert result.is_success
        assert len(result.outputs) == 3

        # 验证 Round 1 收到 Round 0 的 context
        wh_call = agents["warehouse"].execute.call_args
        context = wh_call[1].get("context") or wh_call[0][1]
        assert len(context) == 1
        assert context[0].source == "aftersale"


# ============================================================
# 错误传播
# ============================================================


class TestErrorPropagation:

    @pytest.mark.asyncio
    async def test_error_cascades_to_dependent_rounds(self):
        """Round 0 ERROR → Round 1 跳过 → 根因报告"""
        err = ToolOutput(
            summary="售后查询超时", source="aftersale",
            status=OutputStatus.ERROR, error_message="timeout",
        )
        agents = {
            "aftersale": MagicMock(execute=AsyncMock(return_value=err)),
            "warehouse": MagicMock(execute=AsyncMock()),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"], task="查退货"),
            Round(agents=["warehouse"], task="查库存", depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert not result.is_success
        assert "aftersale" in result.summary
        assert "timeout" in result.summary
        agents["warehouse"].execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_result_continues(self):
        """EMPTY 状态继续执行（没数据本身是有意义的结论）"""
        empty = ToolOutput(
            summary="无退货记录", source="aftersale",
            status=OutputStatus.EMPTY,
        )
        wh = ToolOutput(summary="库存正常", source="warehouse")
        agents = {
            "aftersale": MagicMock(execute=AsyncMock(return_value=empty)),
            "warehouse": MagicMock(execute=AsyncMock(return_value=wh)),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"]),
            Round(agents=["warehouse"], depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert result.is_success
        agents["warehouse"].execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_below_threshold_skips(self):
        """PARTIAL + 数据量<10%预期 → 跳过后续"""
        partial = ToolOutput(
            summary="部分数据", source="aftersale",
            status=OutputStatus.PARTIAL,
            data=[{"x": 1}],
            metadata={"total_expected": 100},
        )
        agents = {
            "aftersale": MagicMock(execute=AsyncMock(return_value=partial)),
            "warehouse": MagicMock(execute=AsyncMock()),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"]),
            Round(agents=["warehouse"], depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert not result.is_success
        agents["warehouse"].execute.assert_not_called()


# ============================================================
# ERPAgent DAG 路径端到端
# ============================================================


class TestERPAgentDAGPath:

    @pytest.mark.asyncio
    async def test_erp_agent_dag_warehouse(self):
        """ERPAgent → DAG → 关键词'库存' → warehouse"""
        from services.agent.erp_agent import ERPAgent
        mock_output = ToolOutput(
            summary="A001 可售30件", source="warehouse",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("sellable", "integer")],
            data=[{"sellable": 30}],
        )
        with patch(
            "services.agent.departments.warehouse_agent.WarehouseAgent.execute",
            new=AsyncMock(return_value=mock_output),
        ):
            agent = ERPAgent(
                db=MagicMock(), user_id="u1",
                conversation_id="c1", org_id="org1",
            )
            result = await agent.execute("查A001库存")
            assert result.status == "success"
            assert "可售30件" in result.text

    @pytest.mark.asyncio
    async def test_erp_agent_dag_unknown_falls_to_abort(self):
        """ERPAgent → DAG → 无法识别意图 → abort"""
        from services.agent.erp_agent import ERPAgent
        agent = ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )
        # mock LLM 让其失败，走降级链 → 关键词无匹配 → abort
        with patch(
            "services.agent.plan_builder.PlanBuilder._llm_plan",
            new=AsyncMock(side_effect=Exception("mocked")),
        ):
            result = await agent.execute("hello world")
            assert result.status == "error"
            assert "无法理解" in result.text


# ============================================================
# PlanBuilder → DAGExecutor 全链路
# ============================================================


class TestFullPipeline:

    @pytest.mark.asyncio
    async def test_keyword_to_dag_execution(self):
        """PlanBuilder(关键词) → ExecutionPlan → DAGExecutor → 结果"""
        builder = PlanBuilder(adapter=None)
        plan = await builder.build("查缺货数量和采购到货进度")
        # "采购"+"到货" 得分更高 → purchase
        assert not plan.is_abort
        assert any("purchase" in r.agents for r in plan.rounds)

        # 用 mock agent 执行
        mock_out = ToolOutput(summary="缺货5个SKU", source="warehouse")
        agents = {
            "warehouse": MagicMock(execute=AsyncMock(return_value=mock_out)),
            "purchase": MagicMock(execute=AsyncMock(
                return_value=ToolOutput(summary="3个在途", source="purchase"),
            )),
            "compute": MagicMock(execute=AsyncMock(
                return_value=ToolOutput(summary="计算完成", source="compute"),
            )),
        }
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert result.is_success


# ============================================================
# 全局超时
# ============================================================


class TestGlobalTimeout:

    @pytest.mark.asyncio
    async def test_erp_agent_global_timeout(self):
        """ERPAgent 全局超时 → 返回超时错误"""
        import asyncio
        from services.agent.erp_agent import ERPAgent

        async def hang_forever(query, **kwargs):
            await asyncio.sleep(999)

        agent = ERPAgent(
            db=MagicMock(), user_id="u1",
            conversation_id="c1", org_id="org1",
        )

        with patch.object(agent, "_execute_dag", side_effect=hang_forever), \
             patch("core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(dag_global_timeout=0.3)
            result = await agent.execute("查库存")
            assert result.status == "error"
            assert "超时" in result.text
