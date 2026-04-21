"""
工具输出类型定义。

定义 FileRef / ColumnMeta / OutputFormat / OutputStatus 等基础类型。
ToolOutput 已合并到 AgentResult（agent_result.py），此处保留别名。
- 部门 Agent 读 .data 拿原始数据
- ToolLoopExecutor 调 .to_tool_content() 拿文本给 LLM

设计文档：docs/document/TECH_Agent通信协议结构化.md
"""
from __future__ import annotations

import json
import time as _time
import uuid as _uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ============================================================
# FileRef MIME 类型映射
# ============================================================

_FORMAT_MIME: dict[str, str] = {
    "parquet": "application/x-parquet",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}


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

    路径协议（对标 OpenAI /mnt/data/ 固定前缀方案）：
    - path: 绝对路径，仅供内部文件操作（is_valid / pd.read_parquet）
    - filename: 文件名，全局唯一（带域标识 + 时间戳）
    - sandbox_ref: LLM 朝向的标准引用，所有 to_message_content 统一用此属性
    """
    path: str                       # 绝对路径（内部文件操作用，不暴露给 LLM）
    filename: str                   # 文件名（带域标识，如 warehouse_stock_xxx.parquet）
    format: str                     # parquet / csv / xlsx
    row_count: int                  # 行数
    size_bytes: int                 # 字节数
    columns: list[ColumnMeta]       # 完整列元信息（名称+类型+中文标签）
    preview: str = ""               # 前3行预览文本
    created_at: float = 0.0         # 创建时间戳（Unix epoch）
    # ── v6 新增字段（均有默认值，向后兼容）──
    id: str = ""                    # UUID 全局唯一（跨工具引用）
    mime_type: str = ""             # 显式 MIME（不靠 format 推断）
    created_by: str = ""            # 哪个 agent/工具创建
    ttl_seconds: int = 86400        # 文件有效期（秒），导出可设 172800
    derived_from: tuple[str, ...] = ()  # v6: 血缘追踪（输入 artifact id 列表）

    @property
    def sandbox_ref(self) -> str:
        """LLM 朝向的标准文件引用 — 等价于 OpenAI /mnt/data/filename。

        所有 to_message_content() 统一用此属性，禁止直接输出 path。
        """
        return f"STAGING_DIR + '/{self.filename}'"

    def is_valid(self, max_age_seconds: int = 0) -> bool:
        """检查文件是否仍然有效（存在 + 未过期）。

        max_age_seconds 为 0 时读 self.ttl_seconds（v6 行为）；
        传入非零值则用传入值（向后兼容旧调用方）。
        """
        if not Path(self.path).exists():
            return False
        ttl = max_age_seconds or self.ttl_seconds
        if self.created_at and ttl and (_time.time() - self.created_at) > ttl:
            return False
        return True


# ============================================================
# ToolOutput 别名（统一到 AgentResult）
# ============================================================
# ToolOutput 已合并到 AgentResult（services/agent/agent_result.py）。
# 保留 ToolOutput 名称作为延迟别名，确保 150+ 处现有代码无需改动：
#   from services.agent.tool_output import ToolOutput  → 实际拿到 AgentResult
#   isinstance(x, ToolOutput)                          → 等价 isinstance(x, AgentResult)
#   ToolOutput(summary="...", status=OutputStatus.OK)   → 创建 AgentResult 实例
#
# 使用 module __getattr__ 延迟导入，避免 agent_result ↔ tool_output 循环引用。


def __getattr__(name: str):
    if name == "ToolOutput":
        from services.agent.agent_result import AgentResult
        return AgentResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

