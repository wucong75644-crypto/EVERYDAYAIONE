"""
ComputeAgent + compute_types 单元测试。

覆盖: compute_types.py（ComputeTask/ComputeResult/validate_compute_result）
      compute_agent.py（prompt构建/输入格式化/prepare_code_context/execute）

设计文档: docs/document/TECH_多Agent单一职责重构.md §5 + §13.9
"""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.compute_types import (
    ComputeResult,
    ComputeTask,
    ValidationChecks,
    validate_compute_result,
)
from services.agent.tool_output import (
    ColumnMeta,
    FileRef,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)


# ============================================================
# 测试数据工厂
# ============================================================


def _cols_stock() -> list[ColumnMeta]:
    return [
        ColumnMeta("product_code", "text", "商品编码"),
        ColumnMeta("sellable", "integer", "可售库存"),
    ]


def _inline_output(source: str = "warehouse", rows: int = 3) -> ToolOutput:
    return ToolOutput(
        summary=f"{source} 查询完成",
        format=OutputFormat.TABLE,
        source=source,
        columns=_cols_stock(),
        data=[{"product_code": f"A{i:03d}", "sellable": i * 10} for i in range(rows)],
    )


def _file_output(source: str = "trade", rows: int = 500) -> ToolOutput:
    return ToolOutput(
        summary=f"{source} 导出完成",
        format=OutputFormat.FILE_REF,
        source=source,
        file_ref=FileRef(
            path=f"/tmp/staging/{source}_data.parquet",
            filename=f"{source}_data.parquet",
            format="parquet",
            row_count=rows,
            size_bytes=51200,
            columns=_cols_stock(),
            created_at=time.time(),
        ),
    )


def _task(
    instruction: str = "合并计算",
    inputs: list[ToolOutput] | None = None,
    output_format: str = "text",
) -> ComputeTask:
    return ComputeTask(
        instruction=instruction,
        inputs=inputs if inputs is not None else [_inline_output()],
        output_format=output_format,
    )


# ============================================================
# ValidationChecks
# ============================================================


class TestValidationChecks:

    def test_empty_checks_pass(self):
        c = ValidationChecks()
        assert c.passed
        assert not c.has_warnings

    def test_critical_fails(self):
        c = ValidationChecks()
        c.add_critical("数据为空")
        assert not c.passed

    def test_warning_still_passes(self):
        c = ValidationChecks()
        c.add_warning("空值率高")
        assert c.passed
        assert c.has_warnings


# ============================================================
# validate_compute_result — 硬校验纯函数
# ============================================================


class TestValidateComputeResult:

    def test_normal_passes(self):
        """正常结果通过校验"""
        task = _task()
        result = ComputeResult(
            conclusion="计算完成",
            output=ToolOutput(
                summary="OK",
                source="compute",
                format=OutputFormat.TABLE,
                columns=_cols_stock(),
                data=[{"product_code": "A001", "sellable": 30}],
            ),
        )
        checks = validate_compute_result(task, result)
        assert checks.passed

    def test_merge_zero_rows_critical(self):
        """输入有数据但结果0行 → critical"""
        task = _task(inputs=[_inline_output(rows=5)])
        result = ComputeResult(
            conclusion="合并完成",
            output=ToolOutput(
                summary="OK",
                source="compute",
                format=OutputFormat.TABLE,
                columns=_cols_stock(),
                data=[],
            ),
        )
        checks = validate_compute_result(task, result)
        assert not checks.passed
        assert any("0行" in c for c in checks.critical)

    def test_export_no_file_critical(self):
        """要求导出xlsx但没有file_ref → critical"""
        task = _task(output_format="xlsx")
        result = ComputeResult(
            conclusion="导出完成",
            output=ToolOutput(summary="OK", source="compute"),
        )
        checks = validate_compute_result(task, result)
        assert not checks.passed
        assert any("导出文件" in c for c in checks.critical)

    def test_export_with_file_passes(self):
        """有file_ref的导出 → 通过"""
        task = _task(output_format="xlsx")
        result = ComputeResult(
            conclusion="导出完成",
            output=_file_output("compute"),
        )
        checks = validate_compute_result(task, result)
        assert checks.passed

    def test_error_message_critical(self):
        """code_execute 报错 → critical"""
        task = _task()
        result = ComputeResult(
            conclusion="失败",
            output=ToolOutput(
                summary="失败",
                source="compute",
                status=OutputStatus.ERROR,
                error_message="NameError: name 'df' is not defined",
            ),
        )
        checks = validate_compute_result(task, result)
        assert not checks.passed
        assert any("NameError" in c for c in checks.critical)

    def test_no_output_no_crash(self):
        """output=None 不崩溃，但输入有数据+结果0行 → critical"""
        task = _task()
        result = ComputeResult(conclusion="空")
        checks = validate_compute_result(task, result)
        # task 有 inputs（3行），result_rows=0 → 触发 "0行" critical
        assert not checks.passed

    def test_no_output_no_inputs_passes(self):
        """无 output + 无 inputs → 通过"""
        task = _task(inputs=[])
        result = ComputeResult(conclusion="空")
        checks = validate_compute_result(task, result)
        assert checks.passed

    def test_file_ref_row_count_used(self):
        """FILE_REF 模式用 file_ref.row_count 而非 data"""
        task = _task(inputs=[_file_output(rows=100)])
        result = ComputeResult(
            conclusion="OK",
            output=_file_output("compute", rows=80),
        )
        checks = validate_compute_result(task, result)
        assert checks.passed  # 80行 > 0，没触发 "0行" 校验


# ============================================================
# ComputeTask / ComputeResult
# ============================================================


class TestComputeTypes:

    def test_task_defaults(self):
        t = ComputeTask(instruction="求和", inputs=[])
        assert t.output_format == "text"

    def test_result_has_file(self):
        r = ComputeResult(conclusion="OK", output=_file_output())
        assert r.has_file

    def test_result_no_file(self):
        r = ComputeResult(conclusion="OK", output=_inline_output())
        assert not r.has_file

    def test_result_is_error(self):
        r = ComputeResult(
            conclusion="失败",
            output=ToolOutput(
                summary="err", source="compute",
                status=OutputStatus.ERROR,
            ),
        )
        assert r.is_error

    def test_result_not_error(self):
        r = ComputeResult(conclusion="OK", output=_inline_output())
        assert not r.is_error

    def test_result_none_output(self):
        r = ComputeResult(conclusion="空")
        assert not r.has_file
        assert not r.is_error


# ============================================================
# ComputeAgent
# ============================================================


class TestComputeAgent:

    def _make_agent(self, staging_dir="/tmp/test_staging"):
        from services.agent.compute_agent import ComputeAgent
        from services.agent.session_file_registry import SessionFileRegistry
        reg = SessionFileRegistry()
        return ComputeAgent(staging_dir=staging_dir, file_registry=reg)

    def test_build_system_prompt_has_sections(self):
        agent = self._make_agent()
        task = _task(instruction="计算可售天数")
        prompt = agent.build_system_prompt(task)
        assert "计算专家" in prompt
        assert "当前可用数据" in prompt
        assert "输入数据" in prompt
        assert "不能做" in prompt

    def test_build_system_prompt_with_file_registry(self):
        from services.agent.compute_agent import ComputeAgent
        from services.agent.session_file_registry import SessionFileRegistry
        reg = SessionFileRegistry()
        fr = FileRef(
            path="/tmp/wh.parquet", filename="wh.parquet",
            format="parquet", row_count=100, size_bytes=5000,
            columns=_cols_stock(), created_at=time.time(),
        )
        reg.register("warehouse", "stock", fr)
        agent = ComputeAgent(staging_dir="/tmp", file_registry=reg)
        prompt = agent.build_system_prompt(_task())
        assert "wh.parquet" in prompt
        assert "warehouse" in prompt

    def test_format_inputs_inline(self):
        from services.agent.compute_agent import ComputeAgent
        text = ComputeAgent._format_inputs([_inline_output(rows=5)])
        assert "输入1(warehouse)" in text
        assert "5行" in text
        assert "inline" in text
        assert "product_code" in text

    def test_format_inputs_file_ref(self):
        from services.agent.compute_agent import ComputeAgent
        text = ComputeAgent._format_inputs([_file_output(rows=500)])
        assert "输入1(trade)" in text
        assert "500行" in text
        assert "file(" in text

    def test_format_inputs_empty(self):
        from services.agent.compute_agent import ComputeAgent
        text = ComputeAgent._format_inputs([])
        assert "无输入" in text

    def test_format_inputs_multiple(self):
        from services.agent.compute_agent import ComputeAgent
        text = ComputeAgent._format_inputs([
            _inline_output("warehouse", 10),
            _file_output("purchase", 200),
        ])
        assert "输入1(warehouse)" in text
        assert "输入2(purchase)" in text

    def test_prepare_code_context_inline(self):
        agent = self._make_agent()
        task = _task(instruction="求和", inputs=[_inline_output(rows=3)])
        ctx = agent.prepare_code_context(task)
        assert ctx["STAGING_DIR"] == "/tmp/test_staging"
        assert ctx["instruction"] == "求和"
        assert len(ctx["inputs"]) == 1
        assert ctx["inputs"][0]["source"] == "warehouse"
        assert ctx["inputs"][0]["row_count"] == 3
        assert "data" in ctx["inputs"][0]

    def test_prepare_code_context_file(self):
        agent = self._make_agent()
        task = _task(inputs=[_file_output(rows=500)])
        ctx = agent.prepare_code_context(task)
        inp = ctx["inputs"][0]
        assert inp["file_path"].endswith(".parquet")
        assert inp["row_count"] == 500
        assert "data" not in inp

    @pytest.mark.asyncio
    async def test_execute_returns_compute_result(self):
        """execute 返回 ComputeResult"""
        agent = self._make_agent()
        task = _task(instruction="合并计算")
        with patch.object(agent, "_generate_code", new=AsyncMock(return_value="")):
            result = await agent.execute(task)
            assert isinstance(result, ComputeResult)
            assert result.conclusion  # 非空（无法生成代码 → 错误结论）

    @pytest.mark.asyncio
    async def test_execute_critical_blocks_as_error(self):
        """CRITICAL 校验失败 → output.status=ERROR（硬阻断）"""
        agent = self._make_agent()
        task = _task(output_format="xlsx", inputs=[_inline_output(rows=3)])
        with patch.object(
            agent, "_generate_code", new=AsyncMock(return_value="print('ok')"),
        ), patch.object(
            agent, "_run_code", new=AsyncMock(return_value="ok"),
        ):
            result = await agent.execute(task)
            # 没有 file_ref，xlsx 校验应触发 CRITICAL → ERROR
            assert result.output.status == OutputStatus.ERROR
            assert any("导出文件" in w for w in result.warnings)
            assert "校验失败" in result.output.summary

    @pytest.mark.asyncio
    async def test_execute_ok_result_not_blocked(self):
        """正常结果通过校验，不被阻断"""
        agent = self._make_agent()
        task = _task(output_format="text", inputs=[])
        with patch.object(
            agent, "_generate_code",
            new=AsyncMock(return_value="print('hello')"),
        ), patch.object(
            agent, "_run_code", new=AsyncMock(return_value="hello"),
        ):
            result = await agent.execute(task)
            assert result.output.status == OutputStatus.OK
            assert not result.warnings

    # ── execute_from_dag ──

    @pytest.mark.asyncio
    async def test_execute_from_dag_wraps_tooloutput(self):
        """execute_from_dag 将 task+context 转 ComputeTask 后返回 ToolOutput"""
        agent = self._make_agent()
        # 用空 inputs 避免触发 "0行" 校验
        with patch.object(
            agent, "_generate_code",
            new=AsyncMock(return_value="print('done')"),
        ), patch.object(
            agent, "_run_code", new=AsyncMock(return_value="计算完成"),
        ):
            result = await agent.execute_from_dag(
                "简单计算", context=[],
            )
            assert isinstance(result, ToolOutput)
            assert result.source == "compute"
            assert result.status == OutputStatus.OK

    @pytest.mark.asyncio
    async def test_execute_from_dag_empty_context(self):
        """execute_from_dag context=None 不崩"""
        agent = self._make_agent()
        with patch.object(
            agent, "_generate_code",
            new=AsyncMock(return_value="print('ok')"),
        ), patch.object(
            agent, "_run_code", new=AsyncMock(return_value="ok"),
        ):
            result = await agent.execute_from_dag("空任务", context=None)
            assert isinstance(result, ToolOutput)

    # ── token 计数 ──

    @pytest.mark.asyncio
    async def test_tokens_used_accumulated(self):
        """_generate_code 的 LLM 调用 token 计入 _tokens_used"""
        agent = self._make_agent()
        assert agent._tokens_used == 0

        # 模拟 adapter.chat 返回带 usage 的 response
        mock_response = {
            "content": "print('hello')",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        }
        mock_adapter = AsyncMock()
        mock_adapter.chat = AsyncMock(return_value=mock_response)
        mock_adapter.close = AsyncMock()

        with patch(
            "services.adapters.factory.create_chat_adapter",
            return_value=mock_adapter,
        ):
            code = await agent._generate_code("system", _task())
            assert code  # 代码非空
            assert agent._tokens_used == 150
