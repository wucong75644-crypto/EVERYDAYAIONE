"""
统一结果类型 — 工具返回和 Agent 返回共用。

所有工具（local_stock_query 等）和子 Agent（ERPAgent 等）统一返回 AgentResult。
消费者通过不同序列化方法获取适合自己的格式：
- to_message_content() → list[dict]  给主 Agent LLM（结构化 block）
- to_tool_content()    → str         给工具循环 LLM（文本 + [DATA_REF]）
- to_text()            → str         纯文本兜底

设计文档: docs/document/TECH_Agent通信协议结构化.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from services.agent.tool_output import (
    FileRef, ColumnMeta, OutputFormat, OutputStatus,
)


@dataclass
class AgentResult:
    """统一结果类型 — 工具和 Agent 共用。

    字段分四层：
    1. 核心层（必填）：status + summary
    2. 数据层（按场景）：format / file_ref / data / columns
    3. 上下文层（可选）：source / error_message / metadata
    4. Agent 层（Agent 返回时填，工具返回时留空）
    """

    # ── 第一层：核心 ──
    summary: str
    """人类可读的结果摘要"""
    status: str | OutputStatus = "success"
    """执行状态：success | error | empty | partial | timeout | ask_user | plan
    构造时可传 OutputStatus 枚举，__post_init__ 自动转为 str。
    默认 "success"（兼容原 ToolOutput 的 OutputStatus.OK 默认值）。
    plan: 超出一次执行能力，返回执行计划由调用方逐步执行。
    """

    # ── 第二层：数据（按场景填充）──
    format: OutputFormat = OutputFormat.TEXT
    """数据格式：TEXT（纯文本）/ TABLE（内联表格）/ FILE_REF（文件引用）"""
    file_ref: FileRef | None = None
    """大数据文件引用（>200行自动生成 staging parquet）"""
    data: list[dict[str, Any]] | None = None
    """内联数据（少量数据直接返回）"""
    columns: list[ColumnMeta] | None = None
    """列定义（TABLE/FILE_REF 场景）"""

    # ── 第三层：上下文 ──
    source: str = ""
    """产出者标识（域名如 "warehouse"，或 Agent 名如 "erp_agent"）"""
    error_message: str = ""
    """错误详情（status=error 时填写）"""
    metadata: dict[str, Any] = field(default_factory=dict)
    """扩展字段（业务上下文：doc_type / time_range 等）"""

    # ── 第四层：Agent 专属（工具调用时不填）──
    collected_files: list[dict[str, Any]] | None = None
    """前端文件卡片（供 content_block_add 展示）"""
    tokens_used: int = 0
    """消耗的 tokens"""
    confidence: float = 1.0
    """结果置信度（降级时 0.6）"""
    ask_user_question: str = ""
    """追问内容（status=ask_user 时填写）"""
    insights: list[str] | None = None
    """分析洞察（可选，未来分析能力用）"""
    follow_up: list[str] | None = None
    """后续建议（可选，未来分析能力用）"""
    thinking_text: str = ""
    """子Agent的思考/进度文本（持久化到消息的thinking_content中）"""

    # ── 内部（不参与比较/显示）──
    _valid_cache: dict[str, bool] = field(
        default_factory=dict, repr=False, compare=False,
    )

    # 失败状态集合（单一事实来源，所有调用方统一用 is_failure）
    _FAILURE_STATUSES: ClassVar[frozenset[str]] = frozenset({"error", "timeout"})

    def __post_init__(self) -> None:
        # OutputStatus 枚举 → str 自动转换
        if isinstance(self.status, OutputStatus):
            self.status = self.status.value
        # "ok" → "success" 归一化（OutputStatus.OK.value == "ok"）
        if self.status == "ok":
            self.status = "success"

    def __contains__(self, item: str) -> bool:
        """支持 `"xxx" in result` — 在 summary 中查找。"""
        return item in self.summary

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.summary == other
        return super().__eq__(other)

    def __hash__(self) -> int:
        return id(self)

    def __str__(self) -> str:
        return self.summary

    @property
    def is_failure(self) -> bool:
        """是否为失败状态（error / timeout）。

        所有需要判断"工具是否失败"的地方统一用此属性，
        不要自行硬编码 status == "error" 或 status in (...)。
        """
        return self.status in self._FAILURE_STATUSES

    # ----------------------------------------------------------
    # 序列化 1：主 Agent LLM（结构化 content block）
    # ----------------------------------------------------------

    def _col_label_map(self) -> dict[str, str]:
        """从 columns 构建 英文name→中文label 映射（label 非空时）。"""
        if not self.columns:
            return {}
        return {c.name: c.label for c in self.columns if c.label and c.label != c.name}

    def _localize_data(self, rows: list[dict]) -> list[dict]:
        """将 data 中的英文 key 替换为中文 label（基于 columns 映射）。

        这是 LLM 看到数据前的唯一翻译点——确保 LLM 用中文列名写代码，
        导出 Excel 时自然产出中文表头。
        """
        mapping = self._col_label_map()
        if not mapping:
            return rows
        return [
            {mapping.get(k, k): v for k, v in row.items()}
            for row in rows
        ]

    def to_message_content(self) -> list[dict[str, Any]]:
        """AgentResult → 结构化 content block（传给主 Agent LLM）。

        返回 list[dict]，每个 dict 是一个 content block：
        - {"type": "text", "text": "..."}
        所有 block 统一用 type="text"（模型 API 只支持 text/image_url）。
        """
        blocks: list[dict[str, Any]] = []

        # 文本摘要（始终有）
        blocks.append({"type": "text", "text": self.summary})

        # 文件引用 → sandbox 标准引用
        if self.file_ref:
            blocks.append({
                "type": "text",
                "text": (
                    f"[文件已存入 staging | "
                    f"读取: pd.read_parquet({self.file_ref.sandbox_ref}) | "
                    f"{self.file_ref.row_count}行 | "
                    f"{self.file_ref.format} | "
                    f"{self.file_ref.size_bytes // 1024}KB]"
                ),
            })

        # 内联数据（少量数据、无文件引用时）
        if self.data and not self.file_ref:
            col_labels = [c.label or c.name for c in self.columns] if self.columns else []
            localized = self._localize_data(self.data[:5])
            preview = json.dumps(localized, ensure_ascii=False)
            blocks.append({
                "type": "text",
                "text": (
                    f"[数据: {len(self.data)}行 | "
                    f"列: {', '.join(col_labels)}]\n"
                    f"{preview}"
                ),
            })

        # 分析洞察
        if self.insights:
            blocks.append({
                "type": "text",
                "text": "分析洞察：\n" + "\n".join(
                    f"· {i}" for i in self.insights
                ),
            })

        return blocks

    # ----------------------------------------------------------
    # 序列化 2：工具循环 LLM（文本 + [DATA_REF] 标签）
    # ----------------------------------------------------------

    def to_tool_content(self) -> str:
        """AgentResult → 工具循环 LLM 的文本格式。

        - TEXT 格式：直接返回 summary
        - TABLE/FILE_REF 格式：summary + [DATA_REF] 标签（含元数据/列/预览）

        此方法替代原 ToolOutput.to_message_content()，由 ToolLoopExecutor 调用。
        """
        if self.format == OutputFormat.TEXT:
            return self.summary

        parts = [self.summary]
        tag_lines = ["\n[DATA_REF]"]

        # 最小必填字段
        tag_lines.append(f"source: {self.source}")
        if self.file_ref:
            tag_lines.append("storage: file")
            tag_lines.append(f"rows: {self.file_ref.row_count}")
            tag_lines.append(f"path: {self.file_ref.sandbox_ref}")
            tag_lines.append(f"format: {self.file_ref.format}")
            tag_lines.append(f"size_kb: {self.file_ref.size_bytes // 1024}")
        elif self.data is not None:
            tag_lines.append("storage: inline")
            tag_lines.append(f"rows: {len(self.data)}")

        # 动态字段（全走 metadata）
        for key, val in self.metadata.items():
            if val is not None and val != "":
                if isinstance(val, (dict, list)):
                    tag_lines.append(
                        f"{key}: {json.dumps(val, ensure_ascii=False)}",
                    )
                else:
                    tag_lines.append(f"{key}: {val}")

        # 列信息（有 label 时用 label 作为列名，LLM 直接用中文）
        cols = self.columns or (
            self.file_ref.columns if self.file_ref else None
        )
        if cols:
            tag_lines.append("columns:")
            for col in cols:
                display_name = col.label if col.label else col.name
                tag_lines.append(f"  - {display_name}: {col.dtype}")

        # 内联数据 or 文件预览（key 翻译为中文）
        if self.data is not None and len(self.data) <= 200:
            localized = self._localize_data(self.data)
            tag_lines.append("data:")
            tag_lines.append(
                f"  {json.dumps(localized, ensure_ascii=False)}",
            )
        elif self.file_ref and self.file_ref.preview:
            tag_lines.append(f"preview:\n  {self.file_ref.preview}")

        tag_lines.append("[/DATA_REF]")
        parts.append("\n".join(tag_lines))
        return "\n".join(parts)

    # ----------------------------------------------------------
    # 序列化 3：纯文本兜底
    # ----------------------------------------------------------

    def to_text(self) -> str:
        """AgentResult → 纯文本（供 tool_context 等期望 str 的消费方）。"""
        parts = [self.summary]
        if self.file_ref:
            parts.append(
                f"[文件: {self.file_ref.path} | "
                f"{self.file_ref.row_count}行 | "
                f"{self.file_ref.format}]"
            )
        if self.insights:
            parts.append("洞察：" + "；".join(self.insights))
        return "\n".join(parts)

    def to_json(self) -> str:
        """序列化为 JSON 字符串（供日志/调试）。"""
        return json.dumps(
            {
                "status": self.status,
                "summary": self.summary[:200],
                "has_file_ref": self.file_ref is not None,
                "has_data": self.data is not None,
                "source": self.source,
                "tokens_used": self.tokens_used,
                "confidence": self.confidence,
            },
            ensure_ascii=False,
        )

    # ----------------------------------------------------------
    # 校验
    # ----------------------------------------------------------

    def validate(self) -> list[str]:
        """校验内部一致性，返回违规项列表（空=有效）。

        规则：
        1. summary 非空
        2. FILE_REF 格式必须有 file_ref
        3. TABLE 格式必须有 columns
        4. ERROR 状态必须有 error_message
        5. file_ref 存在时检查 is_valid()（首次调用后缓存）
        """
        issues: list[str] = []
        if not self.summary:
            issues.append("summary 为空")
        if self.format == OutputFormat.FILE_REF and not self.file_ref:
            issues.append("FILE_REF 格式但缺少 file_ref")
        if self.format == OutputFormat.TABLE and not self.columns:
            issues.append("TABLE 格式但缺少 columns")
        if self.status == "error" and not self.error_message:
            issues.append("ERROR 状态但缺少 error_message")
        if self.file_ref:
            cache_key = self.file_ref.path
            if cache_key not in self._valid_cache:
                self._valid_cache[cache_key] = self.file_ref.is_valid()
            if not self._valid_cache[cache_key]:
                issues.append(f"file_ref 无效: {self.file_ref.path}")
        return issues
