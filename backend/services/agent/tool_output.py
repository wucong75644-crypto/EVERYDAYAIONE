"""
结构化工具输出协议。

所有执行层工具返回 ToolOutput，不再返回裸字符串。
- 部门 Agent 读 .data 拿原始数据
- ToolLoopExecutor 调 .to_message_content() 拿文本给 LLM
- 一个函数、一个返回类型、两种用法

设计文档：docs/document/TECH_多Agent单一职责重构.md §4.1 + §13.10
"""
from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ============================================================
# 枚举类型
# ============================================================

class OutputFormat(Enum):
    """工具输出格式类型"""
    TEXT = "text"           # 纯文本（给 LLM 阅读）
    TABLE = "table"         # 结构化表格（≤200行内联 JSON）
    FILE_REF = "file_ref"   # 大数据走文件（>200行写 staging）


class OutputStatus(Enum):
    """执行状态"""
    OK = "ok"              # 查询成功，有数据
    EMPTY = "empty"        # 查询成功，确实没数据（业务合理）
    PARTIAL = "partial"    # 查询成功但不完整（超时截断）
    ERROR = "error"        # 查询失败（异常/权限/接口错误）


# ============================================================
# 列元信息
# ============================================================

@dataclass(frozen=True)
class ColumnMeta:
    """列元信息 — 让下游 Agent 知道每列叫什么、是什么类型。

    name:  列名（英文，和 parquet/JSON key 一致，必须是标准名）
    dtype: text / integer / numeric / timestamp / boolean
    label: 中文标签（给 LLM 看，可选）
    """
    name: str
    dtype: str
    label: str = ""


# ============================================================
# Staging 文件引用
# ============================================================

@dataclass(frozen=True)
class FileRef:
    """Staging 文件引用 — 大数据的结构化传输凭证。

    row_count 由写文件的函数在写完后立即填入（DuckDB COPY TO
    返回写入行数 / pandas to_parquet 后用 len(df)），准确性由
    写入操作保证，不是事后读文件猜的。
    """
    path: str                       # 相对路径 staging/{conv_id}/{filename}
    filename: str                   # 文件名（带域标识，如 warehouse_stock_xxx.parquet）
    format: str                     # parquet / csv / xlsx
    row_count: int                  # 行数
    size_bytes: int                 # 字节数
    columns: list[ColumnMeta]       # 完整列元信息（名称+类型+中文标签）
    preview: str = ""               # 前3行预览文本
    created_at: float = 0.0         # 创建时间戳（Unix epoch）

    def is_valid(self, max_age_seconds: int = 86400) -> bool:
        """检查文件是否仍然有效（存在 + 未过期）。"""
        if not Path(self.path).exists():
            return False
        if self.created_at and (_time.time() - self.created_at) > max_age_seconds:
            return False
        return True


# ============================================================
# 统一工具输出
# ============================================================

@dataclass
class ToolOutput:
    """统一工具输出 — 所有 Agent/工具的标准返回格式。

    协议层字段（固定，和业务无关）：
        summary   — 文本摘要（必填，始终存在）
        format    — TEXT / TABLE / FILE_REF
        source    — 哪个 Agent 产出的（必填）
        status    — 执行状态 OK/EMPTY/PARTIAL/ERROR
        error_message — 错误信息（ERROR 时必填）
        columns   — 列名+类型+标签（TABLE/FILE_REF 必填）
        data      — 内联数据（TABLE 模式）
        file_ref  — 文件引用（FILE_REF 模式）

    业务层字段（动态，Agent 自主决定）：
        metadata  — dict，Agent 根据任务放 doc_type / time_range 等
    """
    # ── 协议层（固定）──
    summary: str
    format: OutputFormat = OutputFormat.TEXT
    source: str = ""
    status: OutputStatus = OutputStatus.OK
    error_message: str = ""
    columns: list[ColumnMeta] | None = None
    data: list[dict[str, Any]] | None = None
    file_ref: FileRef | None = None
    # ── 业务层（动态，Agent 自主决定）──
    metadata: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------
    # 序列化方法
    # ----------------------------------------------------------

    def to_message_content(self) -> str:
        """转为 messages 数组里的 content 字符串。

        - 纯文本（TEXT）：直接返回 summary，不加标签
        - 结构化（TABLE/FILE_REF）：summary + [DATA_REF] 标签

        注意：timestamp 由通道层（ToolLoopExecutor）注入，
        不是 ToolOutput 的职责。DATA_REF 的动态字段由 Agent
        根据任务结果自行填充，不强制所有字段都出现。
        """
        if self.format == OutputFormat.TEXT:
            return self.summary

        parts = [self.summary]
        tag_lines = ["\n[DATA_REF]"]

        # ── 最小必填字段 ──
        tag_lines.append(f"source: {self.source}")
        if self.file_ref:
            tag_lines.append("storage: file")
            tag_lines.append(f"rows: {self.file_ref.row_count}")
            tag_lines.append(f"path: {self.file_ref.path}")
            tag_lines.append(f"format: {self.file_ref.format}")
            tag_lines.append(f"size_kb: {self.file_ref.size_bytes // 1024}")
        elif self.data is not None:
            tag_lines.append("storage: inline")
            tag_lines.append(f"rows: {len(self.data)}")

        # ── 动态字段（全走 metadata，Agent 自己决定放什么）──
        for key, val in self.metadata.items():
            if val is not None and val != "":
                tag_lines.append(f"{key}: {val}")

        # ── 列信息（必填：有 DATA_REF 就必须有 columns）──
        cols = self.columns or (self.file_ref.columns if self.file_ref else None)
        if cols:
            tag_lines.append("columns:")
            for col in cols:
                label_part = f"  # {col.label}" if col.label else ""
                tag_lines.append(f"  - {col.name}: {col.dtype}{label_part}")

        # ── 内联数据 or 文件预览 ──
        if self.data is not None and len(self.data) <= 200:
            tag_lines.append("data:")
            tag_lines.append(f"  {json.dumps(self.data, ensure_ascii=False)}")
        elif self.file_ref and self.file_ref.preview:
            tag_lines.append(f"preview:\n  {self.file_ref.preview}")

        tag_lines.append("[/DATA_REF]")
        parts.append("\n".join(tag_lines))
        return "\n".join(parts)

    def to_compute_input(self) -> dict[str, Any]:
        """转为 ComputeAgent 的结构化输入（Python dict，不是文本）。"""
        result: dict[str, Any] = {
            "source": self.source,
            "summary": self.summary,
        }
        if self.metadata:
            result["metadata"] = self.metadata

        cols = self.columns or (self.file_ref.columns if self.file_ref else None)
        if cols:
            result["columns"] = [
                {"name": c.name, "dtype": c.dtype, "label": c.label}
                for c in cols
            ]
        if self.data is not None:
            result["data"] = self.data
        if self.file_ref:
            result["file_ref"] = {
                "path": self.file_ref.path,
                "filename": self.file_ref.filename,
                "format": self.file_ref.format,
                "row_count": self.file_ref.row_count,
            }
        return result
