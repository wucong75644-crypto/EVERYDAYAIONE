"""file_analyze 重构 - 清洗策略对象。

CleaningStrategy 是 AI 决策驱动的清洗参数，
代码层 clean_excel 据此执行清洗动作。

AI 没指定的部分用硬规则兜底（详见方案文档 §7.3 兜底降级矩阵）。

设计文档：docs/document/TECH_file_analyze_重构.md §4.3 + §7
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.agent.file_ai_decision import (
    AIDecision,
    EmptyRowDecision,
    MergedCellAction,
    MixedTypeAction,
)


EMPTY_ROW_POLICIES = frozenset({
    "strict_all_empty",            # 全空行才删（默认硬规则）
    "preserve_section_separators", # AI 决策：某些空行保留
})


@dataclass
class CleaningStrategy:
    """清洗策略。所有字段都允许空（清洗时走硬规则兜底）。

    使用方式：
        strategy = CleaningStrategy.from_decision(ai_decision)
        df, report = clean_excel(df, ..., strategy=strategy)
    """

    # ── 合并单元格语义 ──
    merged_cell_actions: list[MergedCellAction] = field(default_factory=list)

    # ── 空行处理 ──
    empty_row_policy: str = "strict_all_empty"
    preserve_empty_rows: list[EmptyRowDecision] = field(default_factory=list)

    # ── 混合类型列处理 ──
    mixed_type_handling: list[MixedTypeAction] = field(default_factory=list)

    # ── ID 列保护（不转 Int64）──
    # value 为业务列名（重命名后的名字）；策略生效在 _apply_column_mapping 之后
    id_columns: list[str] = field(default_factory=list)

    # ── 汇总行标记（生成 _is_summary 列）──
    summary_rows: list[int] = field(default_factory=list)   # Excel 1-indexed

    # ── 列重命名（列字母 → 业务名）──
    column_mapping: dict[str, str] = field(default_factory=dict)

    # ── 跳过列建议（不删除，主 Agent 自行决定）──
    skip_columns: list[str] = field(default_factory=list)

    @classmethod
    def from_decision(cls, decision: AIDecision) -> CleaningStrategy:
        """从 AIDecision 派生 CleaningStrategy。

        映射规则：
          column_semantics.is_id_column=True   → id_columns
          column_semantics                      → column_mapping (letter→business_name)
          summary_rows                          → summary_rows
          merged_cell_actions                   → merged_cell_actions（直接复用）
          mixed_type_handling                   → mixed_type_handling（直接复用）
          preserve_empty_rows                   → preserve_empty_rows（直接复用）
                                                  & policy 升级为 preserve_section_separators
        """
        # ID 列：业务列名（重命名后的名字）
        id_cols = [
            cs.business_name
            for cs in decision.column_semantics
            if cs.is_id_column
        ]

        # 列重命名：letter → business_name（仅当 business_name 与原列名不同时由清洗层应用）
        mapping = {
            cs.letter: cs.business_name
            for cs in decision.column_semantics
            if cs.business_name
        }

        # 空行政策
        empty_policy = (
            "preserve_section_separators"
            if decision.preserve_empty_rows
            else "strict_all_empty"
        )

        return cls(
            merged_cell_actions=list(decision.merged_cell_actions),
            empty_row_policy=empty_policy,
            preserve_empty_rows=list(decision.preserve_empty_rows),
            mixed_type_handling=list(decision.mixed_type_handling),
            id_columns=id_cols,
            summary_rows=list(decision.summary_rows),
            column_mapping=mapping,
            skip_columns=[],   # 暂不从 AIDecision 派生；未来扩展
        )
