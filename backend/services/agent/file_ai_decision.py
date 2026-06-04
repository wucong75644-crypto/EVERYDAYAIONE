"""file_analyze 重构 - AI 一次裁决的结构化输出。

AIDecision 是 AI 看完 EvidencePool 后产出的完整决策，
由 file_ai_judge.adjudicate 调用 qwen 模型后解析 JSON 得到。

设计文档：docs/document/TECH_file_analyze_重构.md §4.2
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 枚举值常量（用于 schema 校验） ──

SEMANTIC_TYPES = frozenset({
    "id", "name", "datetime", "amount", "quantity",
    "address", "note", "category", "other",
})

MERGED_CELL_ACTIONS = frozenset({
    "treat_as_header",   # 表头跨列合并 → 由 _flatten_multi_header 处理
    "fill_down",         # 纵向合并 → 用左上角值向下填充
    "preserve_as_group", # 分组结构 → 保留 NaN
    "skip",              # 不处理
})

MIXED_TYPE_ACTIONS = frozenset({
    "force_str",                # 强转字符串（默认硬规则）
    "extract_unit_number",      # "1.5kg" → 1.5
    "extract_currency_amount",  # "¥99.5" → 99.5
    "to_datetime",              # "2024年4月" → datetime
})

REGION_ROLES = frozenset({"primary", "secondary", "metadata", "skip"})
SHEET_ROLES = frozenset({"data", "meta", "aggregated", "skip"})
HEADER_TYPES = frozenset({"single", "multi_level"})
NOTE_SEVERITIES = frozenset({"info", "warning", "error"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})  # V2.2 #11


# ── 子对象 ──

@dataclass
class ColumnSemantic:
    """单列业务语义裁决。"""
    letter: str               # Excel 列字母
    business_name: str        # 推断的业务列名
    semantic_type: str        # 见 SEMANTIC_TYPES
    is_order_level: bool = False   # AI 判断的订单级标签（SUM 前需 DISTINCT）
    is_id_column: bool = False     # ID/订单号类（清洗时不转 Int64）
    notes: str = ""


@dataclass
class MergedCellAction:
    """单个合并单元格的处理决策。"""
    range_str: str             # "A2:H2"
    action: str                # 见 MERGED_CELL_ACTIONS
    reason: str = ""


@dataclass
class MixedTypeAction:
    """单列混合类型的处理决策。"""
    col_letter: str
    action: str                # 见 MIXED_TYPE_ACTIONS
    unit: str = ""             # 当 action == "extract_unit_number" 时填
    reason: str = ""


@dataclass
class EmptyRowDecision:
    """需要保留的空行（默认全空行删除）。"""
    row: int                   # Excel 1-indexed 行号
    reason: str = ""


@dataclass
class RegionDecision:
    """单数据区域的裁决（路径 C）。"""
    region_id: int
    range_str: str
    role: str                  # 见 REGION_ROLES
    relation_to_primary: str = ""
    skip_reason: str = ""


@dataclass
class SheetDecision:
    """单 sheet 的裁决（路径 D）。"""
    name: str
    role: str                  # 见 SHEET_ROLES
    merge_group: str = ""      # 同组的合并；空表示独立
    skip_reason: str = ""


@dataclass
class DataQualityNote:
    """数据质量提示（给主 Agent 看）。"""
    severity: str              # 见 NOTE_SEVERITIES
    note: str
    affected_rows: list[int] = field(default_factory=list)
    affected_cols: list[str] = field(default_factory=list)


# ── 主决策对象 ──

@dataclass
class AIDecision:
    """AI 一次裁决的完整输出。

    通过 file_ai_judge.adjudicate 调用 qwen 模型，
    解析 JSON 后实例化此对象。

    summary_rows / unit_rows / note_rows 都是 Excel 1-indexed 行号。
    空列表 = AI 确认无此类行（不是"AI 未检查"）。
    """

    # ── 基础结构 ──
    header_row: int                                              # Excel 1-indexed
    data_start_row: int                                          # Excel 1-indexed
    header_type: str = "single"                                  # 见 HEADER_TYPES
    header_note: str = ""                                        # 特殊说明

    # ── 列业务语义（所有路径必填）──
    column_semantics: list[ColumnSemantic] = field(default_factory=list)

    # ── 特殊行 ──
    summary_rows: list[int] = field(default_factory=list)
    unit_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)

    # ── 清洗策略相关（被 CleaningStrategy 消费）──
    merged_cell_actions: list[MergedCellAction] = field(default_factory=list)
    mixed_type_handling: list[MixedTypeAction] = field(default_factory=list)
    preserve_empty_rows: list[EmptyRowDecision] = field(default_factory=list)

    # ── 多区域决策（路径 C 才有）──
    regions: list[RegionDecision] = field(default_factory=list)

    # ── 多 sheet 决策（路径 D 才有）──
    sheets: list[SheetDecision] = field(default_factory=list)

    # ── 数据质量 ──
    data_quality_notes: list[DataQualityNote] = field(default_factory=list)

    # ── 整体总结（给主 Agent 看）──
    overall_summary: str = ""

    # ── V2.2 #11: AI 整体置信度 ──
    # AI 通过 prompt 输出对此次裁决的整体置信度。
    # 主 Agent 可据此决定是否给用户提示"AI 判断不确定，建议复核"。
    # 缺失时默认 "high"（向后兼容；旧 prompt 不要求输出）
    confidence: str = "high"       # 见 CONFIDENCE_LEVELS

    # ── 内部元信息（不在 prompt schema 中）──
    model_used: str = ""           # "qwen-turbo" / "qwen-plus"
    attempt_count: int = 1         # 第几次尝试成功
    elapsed_ms: int = 0


# ── 校验辅助 ──

def validate_decision(decision: AIDecision) -> list[str]:
    """校验 AIDecision 各字段合法性，返回错误列表（空 = OK）。

    用于 _parse_and_validate 解析 LLM 输出后做完整性检查。
    """
    errors: list[str] = []

    if decision.header_row < 1:
        errors.append(f"header_row 必须 ≥ 1，实际 {decision.header_row}")
    if decision.data_start_row < 1:
        errors.append(f"data_start_row 必须 ≥ 1，实际 {decision.data_start_row}")
    if decision.data_start_row <= decision.header_row:
        errors.append(
            f"data_start_row ({decision.data_start_row}) 必须 > "
            f"header_row ({decision.header_row})"
        )
    if decision.header_type not in HEADER_TYPES:
        errors.append(f"header_type 非法：{decision.header_type}")
    if not decision.column_semantics:
        errors.append("column_semantics 不能为空")

    for cs in decision.column_semantics:
        # business_name 允许为空：LLM 对空列如实输出 business_name="" 是合理行为
        if not cs.letter:
            errors.append(f"ColumnSemantic 缺 letter: {cs}")
        if cs.semantic_type not in SEMANTIC_TYPES:
            errors.append(
                f"ColumnSemantic.semantic_type 非法：{cs.semantic_type} (列 {cs.letter})"
            )

    for mca in decision.merged_cell_actions:
        if mca.action not in MERGED_CELL_ACTIONS:
            errors.append(f"MergedCellAction.action 非法：{mca.action}")

    for mta in decision.mixed_type_handling:
        if mta.action not in MIXED_TYPE_ACTIONS:
            errors.append(f"MixedTypeAction.action 非法：{mta.action}")
        if mta.action == "extract_unit_number" and not mta.unit:
            errors.append(f"列 {mta.col_letter} extract_unit_number 缺 unit")

    for r in decision.regions:
        if r.role not in REGION_ROLES:
            errors.append(f"RegionDecision.role 非法：{r.role}")

    for s in decision.sheets:
        if s.role not in SHEET_ROLES:
            errors.append(f"SheetDecision.role 非法：{s.role}")

    for n in decision.data_quality_notes:
        if n.severity not in NOTE_SEVERITIES:
            errors.append(f"DataQualityNote.severity 非法：{n.severity}")

    # V2.2 #11: 置信度校验（缺失默认 high 兜底，非法报错）
    if decision.confidence not in CONFIDENCE_LEVELS:
        errors.append(f"AIDecision.confidence 非法：{decision.confidence}")

    return errors
