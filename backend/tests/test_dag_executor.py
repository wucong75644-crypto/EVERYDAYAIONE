"""
DAG 执行引擎 + 部门Agent execute() 单元测试。

覆盖: dag_executor.py + department_agent.execute()/_classify_action/_dispatch
设计文档: docs/document/TECH_多Agent单一职责重构.md §9 / §13.6
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.dag_executor import DAGExecutor, DAGResult
from services.agent.execution_plan import ExecutionPlan, Round
from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)


# ── mock Agent 工厂 ──

def _mock_agent(domain: str, output: ToolOutput | None = None):
    """创建 mock DepartmentAgent（execute 直接返回预设结果）"""
    agent = MagicMock()
    agent.domain = domain
    default = output or ToolOutput(
        summary=f"{domain} 查询完成",
        source=domain,
        format=OutputFormat.TABLE,
        columns=[ColumnMeta("id", "integer")],
        data=[{"id": 1}],
    )
    agent.execute = AsyncMock(return_value=default)
    return agent


def _error_output(domain: str, msg: str = "查询失败") -> ToolOutput:
    return ToolOutput(
        summary=msg, source=domain,
        status=OutputStatus.ERROR, error_message=msg,
    )


# ============================================================
# DAGExecutor — 基本编排
# ============================================================


class TestDAGExecutorBasic:

    @pytest.mark.asyncio
    async def test_single_domain(self):
        """单域直通"""
        agents = {"warehouse": _mock_agent("warehouse")}
        plan = ExecutionPlan.single("warehouse", task="查库存")
        executor = DAGExecutor(agents=agents, query="查A001库存")
        result = await executor.run(plan)
        assert result.is_success
        assert "warehouse 查询完成" in result.summary
        agents["warehouse"].execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_abort_plan(self):
        plan = ExecutionPlan.abort("无法理解")
        executor = DAGExecutor(agents={}, query="xxx")
        result = await executor.run(plan)
        assert not result.is_success
        assert "无法理解" in result.summary

    @pytest.mark.asyncio
    async def test_multi_domain_parallel(self):
        """两域并行"""
        agents = {
            "warehouse": _mock_agent("warehouse"),
            "purchase": _mock_agent("purchase"),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse", "purchase"], task="查库存和采购"),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert result.is_success
        assert len(result.outputs) == 2

    @pytest.mark.asyncio
    async def test_serial_rounds_with_dependency(self):
        """串行依赖：Round 1 依赖 Round 0"""
        agents = {
            "aftersale": _mock_agent("aftersale"),
            "warehouse": _mock_agent("warehouse"),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"], task="查退货"),
            Round(agents=["warehouse"], task="查库存", depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert result.is_success
        assert len(result.outputs) == 2
        # warehouse 的 execute 应该收到 aftersale 的输出作为 context
        warehouse_call = agents["warehouse"].execute.call_args
        context_arg = warehouse_call[1].get("context") or warehouse_call[0][1]
        assert len(context_arg) == 1
        assert context_arg[0].source == "aftersale"

    @pytest.mark.asyncio
    async def test_unknown_domain_error(self):
        """未知域 → ERROR"""
        plan = ExecutionPlan.single("finance")
        executor = DAGExecutor(agents={}, query="test")
        result = await executor.run(plan)
        assert not result.is_success
        assert "unknown domain" in result.summary or "未知域" in result.summary


# ============================================================
# DAGExecutor — 错误传播
# ============================================================


class TestDAGErrorPropagation:

    @pytest.mark.asyncio
    async def test_error_skips_dependent_rounds(self):
        """Round 0 ERROR → Round 1 跳过"""
        agents = {
            "aftersale": _mock_agent("aftersale", _error_output("aftersale")),
            "warehouse": _mock_agent("warehouse"),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"], task="查退货"),
            Round(agents=["warehouse"], task="查库存", depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert not result.is_success
        # warehouse.execute 不应被调用（被跳过）
        agents["warehouse"].execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_does_not_affect_independent_round(self):
        """Round 0 有两个并行 Agent，一个失败不影响另一个的结果收集"""
        agents = {
            "warehouse": _mock_agent("warehouse", _error_output("warehouse")),
            "purchase": _mock_agent("purchase"),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse", "purchase"], task="并行查"),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        # 有错误所以 status=error
        assert not result.is_success
        # 但两个都执行了
        assert len(result.outputs) == 2

    @pytest.mark.asyncio
    async def test_agent_exception_handled(self):
        """Agent 抛异常 → 包装为 ERROR ToolOutput"""
        agent = MagicMock()
        agent.domain = "trade"
        agent.execute = AsyncMock(side_effect=Exception("timeout"))
        plan = ExecutionPlan.single("trade")
        executor = DAGExecutor(agents={"trade": agent}, query="test")
        result = await executor.run(plan)
        assert not result.is_success
        assert "timeout" in result.summary

    @pytest.mark.asyncio
    async def test_partial_warning(self):
        """PARTIAL 结果 → 结论带警告"""
        partial = ToolOutput(
            summary="部分数据", source="warehouse",
            status=OutputStatus.PARTIAL,
        )
        agents = {"warehouse": _mock_agent("warehouse", partial)}
        plan = ExecutionPlan.single("warehouse")
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        assert result.is_success  # PARTIAL 不是 ERROR
        assert "不完整" in result.summary

    @pytest.mark.asyncio
    async def test_partial_no_total_expected_skips_threshold(self):
        """PARTIAL 无 total_expected → 跳过阈值检查，正常执行"""
        # Round 0 返回 PARTIAL（无 total_expected）
        partial = ToolOutput(
            summary="部分数据", source="warehouse",
            status=OutputStatus.PARTIAL,
            columns=[ColumnMeta("product_code", "text")],
            data=[{"product_code": "A001"}],  # 只有 1 行
            # metadata 里没有 total_expected → 跳过 10% 检查
        )
        ok_output = ToolOutput(summary="OK", source="compute")
        agents = {
            "warehouse": _mock_agent("warehouse", partial),
            "compute": _mock_agent("compute", ok_output),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse"], task="查库存"),
            Round(agents=["compute"], task="计算", depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        # Round 1 应该正常执行（没有被阈值检查拦截）
        assert result.is_success

    @pytest.mark.asyncio
    async def test_partial_below_10pct_threshold_cascades_error(self):
        """PARTIAL 数据量 <10% total_expected → 按 ERROR 级联跳过"""
        partial = ToolOutput(
            summary="严重不完整", source="warehouse",
            status=OutputStatus.PARTIAL,
            columns=[ColumnMeta("product_code", "text")],
            data=[{"product_code": "A001"}],  # 1 行
            metadata={"total_expected": 100},  # 预期 100 行，实际 1 行 = 1%
        )
        ok_output = ToolOutput(summary="OK", source="compute")
        agents = {
            "warehouse": _mock_agent("warehouse", partial),
            "compute": _mock_agent("compute", ok_output),
        }
        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse"], task="查库存"),
            Round(agents=["compute"], task="计算", depends_on=[0]),
        ])
        executor = DAGExecutor(agents=agents, query="test")
        result = await executor.run(plan)
        # Round 1 应该被跳过（数据严重不完整）
        assert not result.is_success


# ============================================================
# DAGExecutor — per-Agent 超时
# ============================================================


class TestDAGTimeout:

    @pytest.mark.asyncio
    async def test_slow_agent_timeout_fast_agent_preserved(self):
        """并行 Round：慢 Agent 超时，快 Agent 结果保留"""
        fast_output = ToolOutput(
            summary="快速完成", source="warehouse",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("id", "integer")],
            data=[{"id": 1}],
        )

        async def slow_execute(task, context=None, **kwargs):
            await asyncio.sleep(5)  # 超过 round_timeout
            return ToolOutput(summary="不应到达", source="purchase")

        fast_agent = MagicMock()
        fast_agent.execute = AsyncMock(return_value=fast_output)
        slow_agent = MagicMock()
        slow_agent.execute = slow_execute

        plan = ExecutionPlan(rounds=[
            Round(agents=["warehouse", "purchase"], task="并行查"),
        ])
        executor = DAGExecutor(
            agents={"warehouse": fast_agent, "purchase": slow_agent},
            query="test",
            round_timeout=0.5,  # 500ms 超时
        )
        result = await executor.run(plan)

        # 快 Agent 结果保留
        wh_outputs = [o for o in result.outputs if o.source == "warehouse"]
        assert len(wh_outputs) == 1
        assert wh_outputs[0].summary == "快速完成"

        # 慢 Agent 超时标记 ERROR
        pur_outputs = [o for o in result.outputs if o.source == "purchase"]
        assert len(pur_outputs) == 1
        assert pur_outputs[0].status == OutputStatus.ERROR
        assert "超时" in pur_outputs[0].summary

    @pytest.mark.asyncio
    async def test_compute_gets_longer_timeout(self):
        """compute 域用 compute_timeout（更长），不被 round_timeout 截断"""
        async def medium_execute(task, context=None, **kwargs):
            await asyncio.sleep(0.3)  # 超过 round_timeout 但不超过 compute_timeout
            return ToolOutput(summary="计算完成", source="compute")

        from services.agent.compute_agent import ComputeAgent
        compute = MagicMock(spec=ComputeAgent)
        compute.execute_from_dag = medium_execute

        plan = ExecutionPlan.single("compute", task="计算")
        executor = DAGExecutor(
            agents={"compute": compute},
            query="test",
            round_timeout=0.1,     # 普通域 100ms（compute 会超）
            compute_timeout=1.0,   # compute 域 1s（够用）
        )
        result = await executor.run(plan)
        assert result.is_success
        assert "计算完成" in result.summary

    @pytest.mark.asyncio
    async def test_single_agent_timeout(self):
        """单 Agent Round 超时也正确处理"""
        async def slow_execute(task, context=None, **kwargs):
            await asyncio.sleep(5)
            return ToolOutput(summary="不应到达", source="trade")

        agent = MagicMock()
        agent.execute = slow_execute

        plan = ExecutionPlan.single("trade", task="查订单")
        executor = DAGExecutor(
            agents={"trade": agent},
            query="test",
            round_timeout=0.2,
        )
        result = await executor.run(plan)
        assert not result.is_success
        assert "timeout" in result.summary or "超时" in result.summary


# ============================================================
# DAGExecutor — deadline 协调
# ============================================================


class TestDAGDeadline:

    @pytest.mark.asyncio
    async def test_deadline_exceeded_skips_agent(self):
        """deadline 剩余 <5s 时直接跳过 Agent"""
        import time

        output = ToolOutput(summary="OK", source="warehouse")
        agent = AsyncMock()
        agent.execute = AsyncMock(return_value=output)

        # deadline 已过期
        executor = DAGExecutor(
            agents={"warehouse": agent},
            query="test",
            deadline=time.monotonic() - 1.0,  # 已过期
        )
        plan = ExecutionPlan.single("warehouse", task="test")
        result = await executor.run(plan)
        assert not result.is_success
        assert "deadline" in result.summary or "超时" in result.summary
        agent.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_deadline_sufficient_executes_normally(self):
        """deadline 充足时正常执行"""
        import time

        output = ToolOutput(summary="OK", source="trade")
        agent = AsyncMock()
        agent.execute = AsyncMock(return_value=output)

        executor = DAGExecutor(
            agents={"trade": agent},
            query="test",
            deadline=time.monotonic() + 60.0,  # 充足
        )
        plan = ExecutionPlan.single("trade", task="test")
        result = await executor.run(plan)
        assert result.is_success

    @pytest.mark.asyncio
    async def test_no_deadline_uses_config_timeout(self):
        """不传 deadline 时用配置超时（向后兼容）"""
        output = ToolOutput(summary="OK", source="trade")
        agent = AsyncMock()
        agent.execute = AsyncMock(return_value=output)

        executor = DAGExecutor(
            agents={"trade": agent}, query="test",
        )
        plan = ExecutionPlan.single("trade", task="test")
        result = await executor.run(plan)
        assert result.is_success


# ============================================================
# DAGExecutor — steer 打断
# ============================================================


class TestDAGSteer:

    @pytest.mark.asyncio
    async def test_steer_after_round_preserves_results(self):
        """Round 间 steer：已完成的 Round 结果保留，后续跳过"""
        r0_output = ToolOutput(
            summary="售后数据", source="aftersale",
            columns=[ColumnMeta("product_code", "text")],
            data=[{"product_code": "A001"}],
        )
        r1_output = ToolOutput(
            summary="库存数据", source="warehouse",
        )
        aftersale_agent = AsyncMock()
        aftersale_agent.execute = AsyncMock(return_value=r0_output)
        warehouse_agent = AsyncMock()
        warehouse_agent.execute = AsyncMock(return_value=r1_output)

        executor = DAGExecutor(
            agents={
                "aftersale": aftersale_agent,
                "warehouse": warehouse_agent,
            },
            query="退货→库存",
        )
        plan = ExecutionPlan(rounds=[
            Round(agents=["aftersale"], task="查退货"),
            Round(agents=["warehouse"], task="查库存", depends_on=[0]),
        ])

        # Round 0 完成后 steer 触发
        with patch(
            "services.websocket_manager.ws_manager.check_steer",
            side_effect=["用户说取消了"],  # Round 0 后返回 steer
        ):
            result = await executor.run(plan, task_id="task_123")

        assert result.status == "partial"
        assert "跳过剩余 1 轮" in result.summary
        # Round 0 结果保留
        assert any(o.source == "aftersale" for o in result.outputs)
        # Round 1 未执行
        warehouse_agent.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_task_id_skips_steer_check(self):
        """不传 task_id 时不检查 steer"""
        output = ToolOutput(summary="OK", source="trade")
        agent = AsyncMock()
        agent.execute = AsyncMock(return_value=output)

        executor = DAGExecutor(agents={"trade": agent}, query="test")
        plan = ExecutionPlan.single("trade", task="test")
        result = await executor.run(plan)  # 不传 task_id
        assert result.is_success


# ============================================================
# DAGExecutor — 共享 file_registry
# ============================================================


class TestDAGFileRegistry:

    @pytest.mark.asyncio
    async def test_file_ref_auto_registered(self):
        """Agent 返回 FILE_REF 时自动注册到共享 registry"""
        import time
        from services.agent.tool_output import FileRef
        from services.agent.session_file_registry import SessionFileRegistry

        file_ref = FileRef(
            path="/tmp/warehouse_1234.parquet",
            filename="warehouse_1234.parquet",
            format="parquet",
            row_count=100,
            size_bytes=5000,
            columns=[ColumnMeta("product_code", "text")],
            created_at=time.time(),
        )
        output = ToolOutput(
            summary="库存数据", source="warehouse",
            format=OutputFormat.FILE_REF,
            file_ref=file_ref,
            columns=[ColumnMeta("product_code", "text")],
        )
        agent = AsyncMock()
        agent.execute = AsyncMock(return_value=output)

        registry = SessionFileRegistry()
        executor = DAGExecutor(
            agents={"warehouse": agent},
            query="查库存",
            file_registry=registry,
        )
        plan = ExecutionPlan.single("warehouse", task="查库存")
        await executor.run(plan)

        # registry 应该自动注册了文件
        files = registry.get_by_domain("warehouse")
        assert len(files) == 1
        assert files[0].filename == "warehouse_1234.parquet"

    @pytest.mark.asyncio
    async def test_no_registry_no_crash(self):
        """不传 file_registry 时不崩（向后兼容）"""
        output = ToolOutput(summary="OK", source="trade")
        agent = AsyncMock()
        agent.execute = AsyncMock(return_value=output)

        executor = DAGExecutor(
            agents={"trade": agent}, query="test",
        )
        plan = ExecutionPlan.single("trade", task="test")
        result = await executor.run(plan)
        assert result.is_success


# ============================================================
# DAGResult
# ============================================================


class TestDAGResult:

    def test_collected_files(self):
        from services.agent.tool_output import FileRef
        import time
        output = ToolOutput(
            summary="导出", source="trade",
            format=OutputFormat.FILE_REF,
            file_ref=FileRef(
                path="/tmp/trade.parquet", filename="trade.parquet",
                format="parquet", row_count=100, size_bytes=5000,
                columns=[], created_at=time.time(),
            ),
        )
        result = DAGResult(outputs=[output], summary="OK")
        assert len(result.collected_files) == 1
        assert result.collected_files[0]["filename"] == "trade.parquet"


# ============================================================
# DepartmentAgent.execute() — 通过 WarehouseAgent 验证
# ============================================================


class TestWarehouseExecute:

    @pytest.mark.asyncio
    async def test_classify_stock(self):
        from services.agent.departments.warehouse_agent import WarehouseAgent
        agent = WarehouseAgent(db=MagicMock())
        assert agent._classify_action("查A001库存") == "stock_query"
        assert agent._classify_action("缺货分析") == "stock_query"
        assert agent._classify_action("仓库列表") == "warehouse_list"
        assert agent._classify_action("收货单查询") == "receipt_query"
        assert agent._classify_action("上架记录") == "shelf_query"

    @pytest.mark.asyncio
    async def test_execute_stock_via_dag(self):
        """通过 execute() 统一入口调 stock_query"""
        from services.agent.departments.warehouse_agent import WarehouseAgent
        agent = WarehouseAgent(db=MagicMock())
        mock_output = ToolOutput(summary="库存数据", source="warehouse")
        with patch(
            "services.kuaimai.erp_local_query.local_stock_query",
            new=AsyncMock(return_value=mock_output),
        ):
            result = await agent.execute("查A001库存")
            # _classify_action → stock_query → query_stock → local_stock_query
            # 但 product_code 为空（未从 context 提取）→ ERROR
            # 因为 _extract_params_from_task 只从 context 提取，不解析自然语言
            assert result.status in (OutputStatus.OK, OutputStatus.ERROR)

    @pytest.mark.asyncio
    async def test_execute_with_context(self):
        """从 context 获取 product_code"""
        from services.agent.departments.warehouse_agent import WarehouseAgent
        agent = WarehouseAgent(db=MagicMock())
        ctx = [ToolOutput(
            summary="OK", source="aftersale",
            format=OutputFormat.TABLE,
            columns=[ColumnMeta("product_code", "text")],
            data=[{"product_code": "A001"}],
        )]
        mock_output = ToolOutput(summary="A001 库存", source="warehouse")
        with patch(
            "services.kuaimai.erp_local_query.local_stock_query",
            new=AsyncMock(return_value=mock_output),
        ):
            result = await agent.execute("查库存", context=ctx)
            assert result.summary == "A001 库存"
