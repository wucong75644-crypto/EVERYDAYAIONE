"""
子 Agent 标准返回格式 — 主 Agent ↔ 子 Agent 通信协议的输出层。

所有子 Agent（ERPAgent、未来的 FinanceAgent 等）必须返回 AgentResult，
主 Agent 通过 to_message_content() 获取结构化 content block 注入 LLM messages。

设计文档: docs/document/TECH_Agent通信协议结构化.md §2.2
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from services.agent.tool_output import FileRef, ColumnMeta


@dataclass
class AgentResult:
    """子 Agent 标准返回格式 — 所有子 Agent 必须遵循。

    字段分三层：
    - 必填层：status + summary，任何场景都有
    - 数据层：file_ref / data / columns，按场景填充
    - 元信息层：agent_name / tokens_used / confidence 等
    """

    # ── 必填 ──
    status: str
    """执行状态：success | partial | error | timeout | ask_user"""
    summary: str
    """人类可读的结果摘要（给主 Agent LLM 看）"""

    # ── 数据（按场景填充）──
    file_ref: FileRef | None = None
    """文件引用（导出/大数据场景，>200行自动生成）"""
    data: list[dict[str, Any]] | None = None
    """内联数据（少量数据直接返回，≤200行）"""
    columns: list[ColumnMeta] | None = None
    """列定义（TABLE/FILE_REF 场景）"""

    # ── 前端展示通道 ──
    collected_files: list[dict[str, Any]] | None = None
    """文件卡片信息（供前端 content_block_add 展示）。
    每项: {"url": str, "name": str, "mime_type": str, "size": int}
    与 file_ref 的区别：file_ref 给 LLM 看路径/行数，
    collected_files 给前端展示卡片。
    """

    # ── 元信息 ──
    agent_name: str = ""
    """哪个子 Agent 产出的（如 "erp_agent"）"""
    tokens_used: int = 0
    """消耗的 tokens"""
    confidence: float = 1.0
    """结果置信度（降级时 0.6）"""
    error_message: str = ""
    """status=error 时填写"""
    ask_user_question: str = ""
    """status=ask_user 时填写（冒泡到主循环追问用户）"""
    insights: list[str] | None = None
    """子 Agent 的分析洞察（可选，未来分析能力用）"""
    follow_up: list[str] | None = None
    """建议的后续操作（可选，未来分析能力用）"""
    metadata: dict[str, Any] = field(default_factory=dict)
    """扩展字段（Agent 自主决定内容）"""

    # ----------------------------------------------------------
    # 序列化：转为主 Agent LLM 的 message content
    # ----------------------------------------------------------

    def to_message_content(self) -> list[dict[str, Any]]:
        """AgentResult → 结构化 content block（传给主 Agent LLM）。

        返回 list[dict]，每个 dict 是一个 content block：
        - {"type": "text", "text": "..."}          — 文本摘要（始终有）
        所有 block 统一用 type="text"（模型 API 只支持 text/image_url/video_url/video），
        结构化信息以可读文本格式嵌入。
        """
        blocks: list[dict[str, Any]] = []

        # 文本摘要（始终有）
        blocks.append({"type": "text", "text": self.summary})

        # 文件引用（有数据文件时）→ 文本描述
        if self.file_ref:
            blocks.append({
                "type": "text",
                "text": (
                    f"[文件: {self.file_ref.path} | "
                    f"{self.file_ref.row_count}行 | "
                    f"{self.file_ref.format} | "
                    f"{self.file_ref.size_bytes // 1024}KB]"
                ),
            })

        # 内联数据（少量数据、无文件引用时）→ 文本描述
        if self.data and not self.file_ref:
            col_names = [c.name for c in self.columns] if self.columns else []
            preview = json.dumps(self.data[:5], ensure_ascii=False)
            blocks.append({
                "type": "text",
                "text": (
                    f"[数据: {len(self.data)}行 | "
                    f"列: {', '.join(col_names)}]\n"
                    f"{preview}"
                ),
            })

        # 分析洞察（子 Agent 有分析能力时）→ 文本描述
        if self.insights:
            blocks.append({
                "type": "text",
                "text": "分析洞察：\n" + "\n".join(
                    f"· {i}" for i in self.insights
                ),
            })

        return blocks

    def to_text(self) -> str:
        """AgentResult → 纯文本（供 tool_context 等期望 str 的消费方使用）。"""
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
        """序列化为 JSON 字符串（供日志/调试使用）。"""
        return json.dumps(
            {
                "status": self.status,
                "summary": self.summary[:200],
                "has_file_ref": self.file_ref is not None,
                "has_data": self.data is not None,
                "agent_name": self.agent_name,
                "tokens_used": self.tokens_used,
                "confidence": self.confidence,
            },
            ensure_ascii=False,
        )
