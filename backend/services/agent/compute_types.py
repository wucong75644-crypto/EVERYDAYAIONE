"""
ComputeAgent 类型定义。

ComputeTask  — 计算任务输入（指令 + 结构化数据 + 输出格式）
ComputeResult — 计算任务输出（结论 + 可选文件）
ValidationChecks — 结果硬校验（纯函数，可单元测试）

设计文档: docs/document/TECH_多Agent单一职责重构.md §5.1 + §13.9
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.agent.tool_output import FileRef, OutputStatus, ToolOutput


@dataclass
class ComputeTask:
    """计算任务输入。

    instruction: 计算指令（自然语言，如"合并三份数据，计算库存可售天数"）
    inputs:      结构化输入数据（各部门Agent的ToolOutput）
    output_format: 期望输出格式 "text" | "excel" | "csv" | "chart"
    """
    instruction: str
    inputs: list[ToolOutput]
    output_format: str = "text"


@dataclass
class ComputeResult:
    """计算任务输出。

    conclusion: 文字结论（必填）
    output:     ToolOutput 结构化结果（含 data 或 file_ref）
    warnings:   校验警告列表
    """
    conclusion: str
    output: ToolOutput | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def has_file(self) -> bool:
        return self.output is not None and self.output.file_ref is not None

    @property
    def is_error(self) -> bool:
        return (
            self.output is not None
            and self.output.status == OutputStatus.ERROR
        )


# ── 结果硬校验（纯函数，不依赖沙盒/数据库/文件系统）──


@dataclass
class ValidationChecks:
    """计算结果校验报告。"""
    critical: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_critical(self, msg: str) -> None:
        self.critical.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def passed(self) -> bool:
        return len(self.critical) == 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


def validate_compute_result(
    task: ComputeTask, result: ComputeResult,
) -> ValidationChecks:
    """纯函数硬校验：不依赖沙盒/数据库/文件系统。

    检查项：
    1. merge 后行数（输入有数据但结果0行 → 可能字段名不一致）
    2. 要求导出文件但未生成
    3. code_execute 报错
    """
    checks = ValidationChecks()

    # 获取结果行数（inline 或 FILE_REF 统一处理）
    result_rows = 0
    if result.output:
        if result.output.data is not None:
            result_rows = len(result.output.data)
        elif result.output.file_ref:
            result_rows = result.output.file_ref.row_count

    # 检查1：merge 后行数
    input_rows: list[int] = []
    for inp in task.inputs:
        if inp.data:
            input_rows.append(len(inp.data))
        elif inp.file_ref:
            input_rows.append(inp.file_ref.row_count)

    if input_rows and result_rows == 0 and all(r > 0 for r in input_rows):
        checks.add_critical(
            "合并后0行数据，输入数据可能无匹配字段（检查字段名是否一致）",
        )

    # 检查2：导出文件未生成
    if task.output_format in ("xlsx", "csv") and not result.has_file:
        checks.add_critical("要求导出文件但未生成")

    # 检查3：code_execute 报错
    if result.output and result.output.error_message:
        checks.add_critical(result.output.error_message)

    return checks
