"""Agent 安全层 Phase 7：事实正确性 Guardrail。

防御 LLM 在自然语言层产生事实幻觉（日期/数值/ID/枚举等）。
作为 erp_agent._run_tool_loop 的输出中间件，在返回前扫描并修正。

设计文档:
- docs/document/TECH_ERP时间准确性架构.md §14
- docs/document/TECH_Agent架构安全层补全.md Phase 7（追加）
"""

from services.agent.guardrails.fact_deviation_log import (
    emit_deviation_records,
)
from services.agent.guardrails.temporal_validator import (
    TemporalDeviation,
    validate_and_patch,
)

__all__ = [
    "TemporalDeviation",
    "validate_and_patch",
    "emit_deviation_records",
]
