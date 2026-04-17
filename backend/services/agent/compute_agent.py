"""
独立计算Agent。

职责：接收结构化数据输入，执行聚合/对比/导出，返回结论。
不做查询、不选工具、不管数据从哪来。

流程：LLM 生成计算代码 → sandbox 执行 → 结果校验 → 返回 ComputeResult。

设计文档: docs/document/TECH_多Agent单一职责重构.md §5
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from services.agent.compute_types import (
    ComputeResult,
    ComputeTask,
    ValidationChecks,
    validate_compute_result,
)
from services.agent.session_file_registry import SessionFileRegistry
from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)


# ── System Prompt 模板 ──

_SYSTEM_PROMPT = """\
你是计算专家。你的唯一职责是对已获取的数据进行计算、统计、对比和导出。

## 你能做的
- 读取 staging 文件（路径已提供，不需要猜）
- pandas 聚合、分组、透视
- 时间差计算、环比同比
- 导出 Excel / CSV
- 生成统计结论

## 你不能做的
- 查询数据（数据已由其他 Agent 获取）
- 调用 ERP API
- 修改数据

## 计算完成后必须自检
1. merge后行数：输入N行+M行，merge后应接近min(N,M)。\
0行 → 停止，输出"⚠ 数据关联失败，可能编码格式不一致"
2. 数值范围：可售天数<0、退货率>1.0、金额为负（非退款） → 异常
3. 空值占比：任何列空值>50% → 标注"⚠ {{列名}}空值率高"
4. 任何检查失败，结论开头加⚠，不能输出"一切正常"

{file_section}

{input_section}
"""


class ComputeAgent:
    """计算Agent — 单一职责：数据计算与导出。

    不需要 db（不查数据库）、不需要 org_id（不做权限）。
    只需要：staging 目录 + 文件注册表 + 时间上下文。
    """

    def __init__(
        self,
        staging_dir: str,
        file_registry: SessionFileRegistry | None = None,
        request_ctx: Any = None,
        user_id: str = "",
        org_id: str | None = None,
        conversation_id: str = "",
    ):
        self._staging_dir = staging_dir
        self._file_registry = file_registry or SessionFileRegistry()
        self._request_ctx = request_ctx
        self._user_id = user_id
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._tokens_used: int = 0

    def build_system_prompt(self, task: ComputeTask) -> str:
        """构建 system prompt（只含计算指令，不含查询规则）。

        注入：
        1. file_registry 文件清单（路径+列名，code_execute 不用猜）
        2. inputs 的结构化摘要（来源+行数+列名）
        """
        file_section = (
            "## 当前可用数据\n" + self._file_registry.to_prompt_text()
        )
        input_section = "## 输入数据\n" + self._format_inputs(task.inputs)

        return _SYSTEM_PROMPT.format(
            file_section=file_section,
            input_section=input_section,
        )

    async def execute(self, task: ComputeTask) -> ComputeResult:
        """执行计算任务。

        单轮执行：LLM 生成计算代码 → sandbox 执行 → 解析结果。
        """
        prompt = self.build_system_prompt(task)
        code_ctx = self.prepare_code_context(task)
        logger.info(
            f"ComputeAgent | instruction={task.instruction[:80]} | "
            f"inputs={len(task.inputs)} | format={task.output_format}",
        )

        # 1. 用 LLM 生成计算代码
        code = await self._generate_code(prompt, task)
        if not code:
            return ComputeResult(
                conclusion="无法生成计算代码",
                output=ToolOutput(
                    summary="ComputeAgent 未能生成代码",
                    source="compute",
                    status=OutputStatus.ERROR,
                    error_message="LLM 未返回代码",
                ),
            )

        # 2. 执行代码
        exec_result = await self._run_code(code, task.instruction)

        # 3. 构建结果
        is_error = exec_result.startswith("❌") or exec_result.startswith("⏱")
        result = ComputeResult(
            conclusion=exec_result,
            output=ToolOutput(
                summary=exec_result[:500] if len(exec_result) > 500 else exec_result,
                source="compute",
                status=OutputStatus.ERROR if is_error else OutputStatus.OK,
                error_message=exec_result if is_error else "",
            ),
        )

        # 4. 硬校验（CRITICAL 不 passed → 阻断，标记为 ERROR）
        checks = validate_compute_result(task, result)
        if not checks.passed:
            result.output = ToolOutput(
                summary="⚠ 计算结果校验失败：" + "；".join(checks.critical),
                source="compute",
                status=OutputStatus.ERROR,
                error_message="；".join(checks.critical),
            )
            result.warnings.extend(checks.critical)
            logger.warning(
                f"ComputeAgent validation BLOCKED | "
                f"critical={checks.critical}",
            )
        elif checks.has_warnings and result.output:
            result.output = ToolOutput(
                summary=(
                    "⚠ " + "；".join(checks.warnings)
                    + "\n\n" + result.output.summary
                ),
                format=result.output.format,
                source=result.output.source,
                status=result.output.status,
                columns=result.output.columns,
                data=result.output.data,
                file_ref=result.output.file_ref,
                metadata=result.output.metadata,
            )
            result.warnings.extend(checks.warnings)

        return result

    async def execute_from_dag(
        self, task: str, context: list[ToolOutput] | None = None,
    ) -> ToolOutput:
        """DAG 兼容入口（签名与 DepartmentAgent.execute 一致）。

        从 task 描述 + context 构建 ComputeTask，执行并返回 ToolOutput。
        """
        compute_task = ComputeTask(
            instruction=task,
            inputs=context or [],
            output_format="text",
        )
        result = await self.execute(compute_task)
        if result.output:
            return result.output
        return ToolOutput(
            summary=result.conclusion,
            source="compute",
            status=OutputStatus.OK,
        )

    async def _generate_code(self, system_prompt: str, task: ComputeTask) -> str:
        """用 LLM 生成计算代码（单轮，不走 tool loop）。"""
        try:
            from services.adapters.factory import create_chat_adapter
            from core.config import settings

            adapter = create_chat_adapter(
                settings.agent_loop_model,
                org_id=self._org_id, db=None,
            )
            try:
                user_msg = (
                    f"请写 Python 代码完成以下计算任务：\n\n"
                    f"{task.instruction}\n\n"
                    f"STAGING_DIR = '{self._staging_dir}'\n"
                    f"输出格式要求: {task.output_format}\n\n"
                    f"只返回可执行的 Python 代码，不需要 markdown 围栏。"
                )
                response = await adapter.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    tools=None,
                    temperature=0.0,
                )
                # 收集 token 消耗（供 ERPAgent 汇总计费）
                usage = getattr(response, "usage", None)
                if usage:
                    self._tokens_used += getattr(usage, "prompt_tokens", 0)
                    self._tokens_used += getattr(usage, "completion_tokens", 0)
                elif isinstance(response, dict):
                    self._tokens_used += response.get("prompt_tokens", 0)
                    self._tokens_used += response.get("completion_tokens", 0)
                raw = (
                    response.get("content", "")
                    if isinstance(response, dict)
                    else getattr(response, "content", "")
                )
                # 去除可能的 markdown 围栏
                import re
                cleaned = re.sub(r"```(?:python)?\s*", "", raw)
                cleaned = cleaned.replace("```", "").strip()
                return cleaned
            finally:
                await adapter.close()
        except Exception as e:
            logger.error(f"ComputeAgent code generation failed: {e}")
            return ""

    async def _run_code(self, code: str, description: str) -> str:
        """在沙盒中执行代码。"""
        try:
            from core.config import get_settings
            from services.sandbox.functions import build_sandbox_executor

            settings = get_settings()
            if not settings.sandbox_enabled:
                return "❌ 沙盒未启用"

            executor = build_sandbox_executor(
                timeout=settings.sandbox_timeout,
                max_result_chars=settings.sandbox_max_result_chars,
                user_id=self._user_id,
                org_id=self._org_id,
                conversation_id=self._conversation_id,
            )
            return await executor.execute(code, description)
        except Exception as e:
            logger.error(f"ComputeAgent sandbox execution failed: {e}")
            return f"❌ 执行异常: {e}"

    def prepare_code_context(self, task: ComputeTask) -> dict[str, Any]:
        """准备 code_execute 的上下文变量。

        返回可注入沙盒的变量字典：
        - STAGING_DIR: staging 目录路径
        - inputs: 结构化输入摘要（列名+路径+行数）
        - instruction: 计算指令
        """
        inputs_for_code: list[dict[str, Any]] = []
        for inp in task.inputs:
            entry: dict[str, Any] = {
                "source": inp.source,
                "summary": inp.summary,
            }
            cols = inp.columns or (
                inp.file_ref.columns if inp.file_ref else None
            )
            if cols:
                entry["columns"] = [
                    {"name": c.name, "dtype": c.dtype, "label": c.label}
                    for c in cols
                ]
            if inp.data is not None:
                entry["data"] = inp.data
                entry["row_count"] = len(inp.data)
            elif inp.file_ref:
                entry["file_path"] = inp.file_ref.path
                entry["row_count"] = inp.file_ref.row_count
                entry["format"] = inp.file_ref.format
            inputs_for_code.append(entry)

        return {
            "STAGING_DIR": self._staging_dir,
            "inputs": inputs_for_code,
            "instruction": task.instruction,
            "output_format": task.output_format,
        }

    # ── 内部方法 ──

    @staticmethod
    def _format_inputs(inputs: list[ToolOutput]) -> str:
        """格式化输入数据摘要（注入 system prompt）。"""
        if not inputs:
            return "无输入数据。"
        lines: list[str] = []
        for i, inp in enumerate(inputs, 1):
            cols = inp.columns or (
                inp.file_ref.columns if inp.file_ref else None
            )
            col_desc = ""
            if cols:
                col_parts = [
                    f"{c.name}({c.label})" if c.label else c.name
                    for c in cols
                ]
                col_desc = f"，列=[{', '.join(col_parts)}]"

            row_count = 0
            storage = "inline"
            if inp.data is not None:
                row_count = len(inp.data)
            elif inp.file_ref:
                row_count = inp.file_ref.row_count
                storage = f"file({inp.file_ref.path})"

            lines.append(
                f"输入{i}({inp.source}): {row_count}行, "
                f"存储={storage}{col_desc}"
            )
        return "\n".join(lines)
