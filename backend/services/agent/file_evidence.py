"""file_analyze 重构 - 代码扫描产出的统一证据池。

EvidencePool 是 4 条路径独立扫描后产出的统一格式，作为 AI 一次裁决的输入。

设计文档：docs/document/TECH_file_analyze_重构.md §4.1
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CellSample:
    """单元格采样（含坐标）。

    用于精确指向 Excel 中某个值的位置，AI 看到时能定位到具体 cell。
    """
    row: int                # Excel 1-indexed 行号
    col: str                # Excel 列字母 (A/B/.../AA)
    raw_value: Any          # 原始值（未清洗）
    classified: str = ""    # _classify_cell 结果（long_id/date/numeric/text/empty）


@dataclass
class SuspiciousRow:
    """代码扫描出的可疑行（待 AI 裁决）。

    reason 可选值：
      - "keyword_match"   含"合计/总计/小计/Total"等关键词
      - "multi_null"      整行 ≥ N 列缺失
      - "type_outlier"    类型分布与上下文显著不同
      - "structural"      结构性怀疑（如标题行/分隔行）
    """
    row: int                                                    # Excel 1-indexed 行号
    reason: str                                                 # 触发可疑的原因
    keywords: list[str] = field(default_factory=list)           # 匹配到的关键词
    null_ratio: float = 0.0                                     # 该行 null 比例
    raw_values: list[Any] = field(default_factory=list)         # 该行原始值列表（最多 15 个）
    surrounding: dict[str, Any] = field(default_factory=dict)   # 上下文（前/后一行片段）


@dataclass
class ColumnEvidence:
    """列级证据。

    AI 据此判断每列的业务语义、是否 ID 列、是否含单位/货币等。
    """
    col_letter: str                                          # Excel 列字母
    raw_header: str                                          # 原始表头单元格的字符串
    sample_values: list[Any] = field(default_factory=list)   # 头 5 + 中 3 + 尾 5 共 13 个值
    classified_dist: dict[str, int] = field(default_factory=dict)  # {long_id: 9, numeric: 3, empty: 1}
    null_ratio: float = 0.0                                  # 该列 null 比例
    is_long_id_candidate: bool = False                       # 长度 > 10 且全数字的占比 > 70%
    # V3：删除 has_currency_prefix / has_unit_suffix_candidates
    # 业务格式识别下沉到 AI 看 sample 自判，扫描器只产出纯统计字段


@dataclass
class RegionEvidence:
    """单 sheet 多区域候选（路径 C）。

    suspected_type 可选：
      - "primary"     主数据区
      - "summary"     已汇总区
      - "meta"        说明/筛选条件区
      - "unknown"     代码无法分类
    """
    region_id: int
    range_str: str                                          # "A1:H100"
    header_row: int                                         # 0-indexed
    header_cells: list[str] = field(default_factory=list)   # 表头单元格内容列表
    head_sample: list[list[Any]] = field(default_factory=list)   # 前 5 行
    tail_sample: list[list[Any]] = field(default_factory=list)   # 后 5 行
    row_count: int = 0
    suspected_type: str = "unknown"


@dataclass
class SheetEvidence:
    """单 sheet 元信息（路径 D 多 sheet 用）。

    rows == -1 表示未采样（多 sheet 超过 MAX_SHEETS_SAMPLED 时未扫描的 sheet）。
    """
    name: str
    rows: int                                                    # 实际行数；-1 = 未采样
    cols: int
    header_candidates: list[list[Any]] = field(default_factory=list)   # 前 3 行
    head_sample: list[list[Any]] = field(default_factory=list)        # 头 3 行（去表头）
    tail_sample: list[list[Any]] = field(default_factory=list)        # 尾 3 行
    column_names: list[str] = field(default_factory=list)             # detect_header_row 检出


@dataclass
class FormulaEvidence:
    """单个公式证据。"""
    cell: str           # "Sheet1!H501"
    expression: str     # "=SUM(H3:H500)"
    value: Any          # 计算结果
    col_name: str = ""  # 列业务名（若能定位）


@dataclass
class EvidencePool:
    """代码扫描完整产出，作为 AI 一次裁决的输入。

    4 条路径都产出此结构，AI 根据 path_type 字段读取相应的路径专属证据
    （regions 仅路径 C，sheets 仅路径 D）。
    """

    # ── 文件元信息（所有路径必填）──
    file_path: str
    file_name: str
    file_size_bytes: int
    total_rows: int
    total_cols: int
    sheet_names: list[str]
    target_sheet: str            # 实际处理的 sheet 名（路径 D 时为 "*"）
    path_type: str               # "A" | "B" | "C" | "D"

    # ── 表头候选（所有路径）──
    header_candidates: list[list[Any]] = field(default_factory=list)   # 前 5 行原始
    detected_header_row_code: int = 0                                  # 代码兜底检测的表头行

    # ── 结构元信息（来自 _detect_structure）──
    merged_ranges: list[tuple[int, int, int, int]] = field(default_factory=list)
    # (min_row, max_row, min_col, max_col) — 1-indexed
    hidden_rows: list[int] = field(default_factory=list)
    hidden_cols: list[int] = field(default_factory=list)
    has_auto_filter: bool = False

    # ── 列证据（所有路径）──
    columns: list[ColumnEvidence] = field(default_factory=list)

    # ── 关键样本（动态行数：路径 A 13 / 路径 B 30 / 路径 C/D 各自实现）──
    key_samples: list[dict[str, Any]] = field(default_factory=list)
    # 每个元素 {"row": int, "cells": list[Any]}

    # ── 可疑行（所有路径，待 AI 裁决）──
    suspicious_rows: list[SuspiciousRow] = field(default_factory=list)

    # ── 路径 C 独有 ──
    regions: list[RegionEvidence] = field(default_factory=list)

    # ── 路径 D 独有 ──
    sheets: list[SheetEvidence] = field(default_factory=list)

    # ── 公式（所有路径，仅 xlsx）──
    formulas: list[FormulaEvidence] = field(default_factory=list)
    formula_total_count: int = 0           # 实际公式总数（formulas 列表可能截断）

    # ── 代码已确定的清洗事实（不需要 AI 决策的部分）──
    confirmed_facts: list[dict[str, Any]] = field(default_factory=list)
    # 每个元素：{"type": str, "action": str, "details": Any}
