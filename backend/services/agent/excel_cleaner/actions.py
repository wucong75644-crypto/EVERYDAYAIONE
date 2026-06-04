"""Excel 清洗动作 (Layer 2)。

8 个清洗动作 + 5 个 _strategy_xxx 私有辅助：
  - _flatten_multi_header / _deduplicate_columns / _mark_hidden_cols
  - _apply_merge_fill / _mark_summary_rows / _mark_summary_rows_from_strategy
  - _remove_empty_rows_cols / _fix_int_columns / _coerce_object_columns

每个动作可选接受 strategy（CleaningStrategy）。
strategy=None 时全部走硬规则（向后兼容）。
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from services.agent.excel_cleaner._strategy_helpers import (
    _strategy_id_columns,
    _strategy_merge_actions,
    _strategy_mixed_handling,
    _strategy_preserve_rows,
)
from services.agent.excel_cleaner.report import CleaningReport, ExcelStructure
from services.agent.excel_cleaner.structure import _col_index_to_letter_local


# ── 表头/列名 ──

def _flatten_multi_header(df: pd.DataFrame, report: CleaningReport | None = None) -> None:
    """将 MultiIndex 列名展平为单行（用 _ 连接）+ 标注原始多级结构。"""
    if not isinstance(df.columns, pd.MultiIndex):
        return
    original_levels = [list(level) for level in df.columns.levels]
    flat: list[str] = []
    for col_tuple in df.columns:
        parts = [str(p).strip() for p in col_tuple if str(p).strip() and str(p) != "nan"]
        name = "_".join(parts) if parts else "Unnamed"
        flat.append(name)
    df.columns = flat
    if report is not None:
        report.issues.append({
            "type": "header_flattened",
            "severity": "info",
            "location": {},
            "preserved": False,
            "action": f"多级表头（{len(original_levels)}层）已用 _ 连接展平为单行",
            "recovery_hint": "原始层级信息见 merged_cells，可根据业务语义重命名列",
        })


def _deduplicate_columns(df: pd.DataFrame, report: CleaningReport) -> None:
    """列名重复时加后缀 _1 _2 去重 + 标注到 report。"""
    cols = list(df.columns)
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    duplicated: list[str] = []
    for c in cols:
        c_str = str(c)
        if c_str in seen:
            seen[c_str] += 1
            new_cols.append(f"{c_str}_{seen[c_str]}")
            if c_str not in duplicated:
                duplicated.append(c_str)
        else:
            seen[c_str] = 0
            new_cols.append(c_str)
    if new_cols != [str(c) for c in cols]:
        df.columns = new_cols
        report.issues.append({
            "type": "column_deduplicated",
            "severity": "info",
            "location": {"cols": duplicated},
            "preserved": False,
            "action": f"重复列名已加后缀 _1/_2: {duplicated}",
            "recovery_hint": "结合 merged_cells 信息重命名（如 3月_金额 / 4月_金额）",
        })


def _mark_hidden_cols(
    df: pd.DataFrame,
    structure: ExcelStructure,
    report: CleaningReport,
) -> None:
    """标注隐藏列（不删除，数据保留）。"""
    if not structure.hidden_cols:
        return
    hidden_names = []
    for col_1indexed in sorted(structure.hidden_cols):
        pandas_col = col_1indexed - 1
        if pandas_col < len(df.columns):
            hidden_names.append(str(df.columns[pandas_col]))
    if hidden_names:
        report.hidden_cols_names = hidden_names
        report.issues.append({
            "type": "hidden_cols",
            "severity": "info",
            "location": {"cols": hidden_names},
            "preserved": True,
            "action": "数据保留，未删除隐藏列",
            "recovery_hint": "按需排除隐藏列: SELECT 时不选这些列",
        })


# ── 合并单元格 ──

def _apply_merge_fill(
    df: pd.DataFrame,
    structure: ExcelStructure,
    header_row: int,
    report: CleaningReport,
    chunk_row_offset: int = 0,
    strategy: Any = None,
) -> None:
    """按 merge range 精确填充合并单元格。

    Phase 4：strategy.merged_cell_actions 指定每个 range 的处理方式：
      - "treat_as_header"     → 跳过（已由 _flatten_multi_header 处理）
      - "fill_down" (默认)    → 填充（现行行为）
      - "preserve_as_group"   → 不填充，记 issue 提示 AI 判断为分组
      - "skip"                → 不填充
    AI 没决策的 range → fill_down（向后兼容）
    """
    if not structure.merged_ranges:
        return

    data_start_excel = header_row + 2
    chunk_start_excel = data_start_excel + chunk_row_offset
    chunk_end_excel = chunk_start_excel + len(df) - 1
    filled = 0
    filled_cols: set[str] = set()
    preserved_groups: list[str] = []

    actions_map = _strategy_merge_actions(strategy)

    for min_row, max_row, min_col, max_col in structure.merged_ranges:
        if max_row < data_start_excel:
            continue
        eff_min_row = max(min_row, data_start_excel)
        if eff_min_row > chunk_end_excel or max_row < chunk_start_excel:
            continue
        if eff_min_row < chunk_start_excel:
            continue

        # 查询 AI 决策（用 range_str 作 key）
        col_start_letter = _col_index_to_letter_local(min_col - 1)
        col_end_letter = _col_index_to_letter_local(max_col - 1)
        range_key = f"{col_start_letter}{min_row}:{col_end_letter}{max_row}"
        action_obj = actions_map.get(range_key)
        action_type = action_obj.action if action_obj else "fill_down"

        if action_type in ("treat_as_header", "skip"):
            continue
        if action_type == "preserve_as_group":
            preserved_groups.append(range_key)
            continue

        # Excel 行号 → DataFrame 行索引
        df_start = eff_min_row - chunk_start_excel
        df_end = min(max_row, chunk_end_excel) - chunk_start_excel
        col_start = min_col - 1
        col_end = min(max_col - 1, len(df.columns) - 1)

        for ci in range(col_start, col_end + 1):
            if ci >= len(df.columns):
                break
            fill_val = df.iloc[df_start, ci]
            if pd.isna(fill_val):
                continue
            for ri in range(df_start + 1, df_end + 1):
                if ri >= len(df):
                    break
                if pd.isna(df.iloc[ri, ci]):
                    df.iloc[ri, ci] = fill_val
                    filled += 1
                    filled_cols.add(str(df.columns[ci]))

    if preserved_groups:
        report.issues.append({
            "type": "merge_preserved_as_group",
            "severity": "info",
            "location": {"ranges": preserved_groups[:10]},
            "preserved": True,
            "action": f"AI 判断为分组结构，保留 NaN：{len(preserved_groups)} 个范围",
            "recovery_hint": "AI 决策 preserve_as_group：行业务上属同组分类，不应填充",
        })

    report.merged_cols_filled = filled
    if filled:
        report.issues.append({
            "type": "merge_filled",
            "severity": "info",
            "location": {"cols": sorted(filled_cols)},
            "preserved": False,
            "action": f"合并单元格精确填充（{filled}个单元格，涉及{len(filled_cols)}列）：{', '.join(sorted(filled_cols))}",
            "recovery_hint": "合并区域内的空值已用左上角值填充，非全列ffill",
        })


# ── 行（合计/空行）──

def _mark_summary_rows(
    df: pd.DataFrame,
    special_rows: dict[str, list[int]],
    header_row: int,
    report: CleaningReport,
    chunk_row_offset: int = 0,
) -> None:
    """用 prescan 识别的合计行位置标记 _is_summary 列。

    只要 special_rows 有 summary 条目就始终添加 _is_summary 列（默认 False），
    确保分块场景下每个 chunk 都有此列，不会被 _cast_to_schema 丢弃。
    """
    summary_excel_rows = special_rows.get("summary", [])
    if not summary_excel_rows:
        return

    df["_is_summary"] = False

    data_start_excel = header_row + 2
    chunk_start_excel = data_start_excel + chunk_row_offset

    matched = []
    for excel_row in summary_excel_rows:
        df_idx = excel_row - chunk_start_excel
        if 0 <= df_idx < len(df):
            matched.append(df_idx)

    if not matched:
        return

    df.loc[matched, "_is_summary"] = True
    report.summary_rows_marked = len(matched)
    report.issues.append({
        "type": "summary_rows_marked",
        "severity": "info",
        "location": {"rows": [i + chunk_start_excel for i in matched]},
        "preserved": True,
        "action": f"标记{len(matched)}个合计行（_is_summary=True）",
        "recovery_hint": "查询时加: WHERE _is_summary = false 排除合计行",
    })


def _mark_summary_rows_from_strategy(
    df: pd.DataFrame,
    summary_rows: list[int],
    header_row: int,
    report: CleaningReport,
    chunk_row_offset: int = 0,
) -> None:
    """Phase 4：用 AI 决策的 summary_rows 标记 _is_summary 列。

    与 _mark_summary_rows 行为等同，只是来源从 prescan special_rows 改为
    AI 一次裁决的 AIDecision.summary_rows（已在 strategy 中）。
    """
    if not summary_rows:
        return
    _mark_summary_rows(
        df, {"summary": summary_rows}, header_row, report, chunk_row_offset,
    )


def _remove_empty_rows_cols(
    df: pd.DataFrame,
    report: CleaningReport,
    structure: ExcelStructure | None = None,
    strategy: Any = None,
    header_row: int = 0,
    chunk_row_offset: int = 0,
) -> None:
    """删除全 NaN 行和全 NaN 列，但保留合并区域内的空列。

    Phase 4：strategy.preserve_empty_rows 中的 Excel 行号会被排除删除
    （AI 判断为业务有意义的分隔行）。
    """
    # 合并区域覆盖的列索引（1-indexed → 0-indexed）
    merged_col_indices: set[int] = set()
    if structure:
        for min_row, max_row, min_col, max_col in structure.merged_ranges:
            for c in range(min_col, max_col + 1):
                merged_col_indices.add(c - 1)

    # 空列：不删除，只标注位置（AI 决定是否需要）
    # 用 mask("", NaN) 后判空：fastexcel fallback-to-string 列里空 cell 是 ""
    # 不识别会漏标真实全空列（如订单备注列实际 100% 空但 isna()=False）
    df_for_empty_check = df.mask(df.eq(""), np.nan)
    empty_col_names: list[str] = []
    for i, col in enumerate(df.columns):
        col_str = str(col)
        if col_str.startswith("_is_"):
            continue
        if i in merged_col_indices:
            continue
        if df_for_empty_check.iloc[:, i].isna().all():
            empty_col_names.append(col_str)
    report.empty_cols_removed = 0
    if empty_col_names:
        report.issues.append({
            "type": "empty_cols",
            "severity": "info",
            "location": {"cols": empty_col_names},
            "preserved": True,
            "action": "全空列已保留，未删除",
            "recovery_hint": "如确认无用，查询时不选这些列即可",
        })

    # 空行：删除所有全空行（含中间和尾部）
    data_cols = [c for c in df.columns if not str(c).startswith("_is_")]
    if data_cols:
        blank_mask = df[data_cols].apply(
            lambda col: col.isna() | col.astype(str).str.strip().eq("") | col.astype(str).eq("nan")
        ).all(axis=1)
    else:
        blank_mask = pd.Series(False, index=df.index)
    empty_row_indices = list(df[blank_mask].index)

    # Phase 4：排除 AI 决策保留的空行
    preserve_set = _strategy_preserve_rows(strategy)
    preserved_rows: list[int] = []
    if preserve_set:
        data_start_excel = header_row + 2
        chunk_start_excel = data_start_excel + chunk_row_offset
        kept_indices = []
        for idx in empty_row_indices:
            excel_row = idx + chunk_start_excel
            if excel_row in preserve_set:
                preserved_rows.append(excel_row)
            else:
                kept_indices.append(idx)
        empty_row_indices = kept_indices

    if empty_row_indices:
        df.drop(empty_row_indices, inplace=True)
        df.reset_index(drop=True, inplace=True)
        report.empty_rows_removed = len(empty_row_indices)
        report.issues.append({
            "type": "empty_rows_removed",
            "severity": "info",
            "location": {"rows": [i + 1 for i in empty_row_indices[:20]]},
            "preserved": False,
            "action": f"删除了 {len(empty_row_indices)} 个全空行",
            "recovery_hint": "原始文件中的空行已被删除",
        })
    else:
        report.empty_rows_removed = 0

    if preserved_rows:
        report.issues.append({
            "type": "empty_rows_preserved",
            "severity": "info",
            "location": {"rows": preserved_rows[:20]},
            "preserved": True,
            "action": f"AI 判断为业务分隔行，保留 {len(preserved_rows)} 个空行",
            "recovery_hint": "AI 决策 preserve_section_separators：行业务上有意义",
        })


# ── 类型修正 ──

def _fix_int_columns(
    df: pd.DataFrame, report: CleaningReport, strategy: Any = None,
) -> None:
    """将全整数的 float64 列转回 nullable Int64（防止 123 → 123.0）。

    Bug-4 + Phase 4 双重保护：
      1. AI 决策的 id_columns（业务列名匹配） → 跳过（最高优先级）
      2. 绝对值 ≥ 10^15 → 跳过（兜底，AI 没指定时防精度丢失）
    """
    id_cols = _strategy_id_columns(strategy)
    fixed_cols: list[str] = []
    for col in df.columns:
        col_str = str(col)
        if col_str.startswith("_is_"):
            continue
        if col_str in id_cols:
            continue
        if df[col].dtype != "float64":
            continue
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue
        try:
            max_abs = non_null.abs().max()
            if max_abs >= 1e15:
                continue
            if (non_null == non_null.astype("int64")).all():
                df[col] = df[col].astype("Int64")
                fixed_cols.append(col_str)
        except (OverflowError, ValueError):
            pass
    report.int_cols_fixed = len(fixed_cols)
    if fixed_cols:
        report.issues.append({
            "type": "int_cols_fixed",
            "severity": "info",
            "location": {"cols": fixed_cols},
            "preserved": False,
            "action": f"整数修复（{len(fixed_cols)}列）：{', '.join(fixed_cols)}",
            "recovery_hint": "float64 全为整数的列已转 Int64，防止 123→123.0",
        })


def _coerce_object_columns(
    df: pd.DataFrame, report: CleaningReport, strategy: Any = None,
) -> None:
    """混合类型列统一为 str（防止 PyArrow 崩溃）+ 标注到 report。

    Phase 4：strategy.mixed_type_handling 按列字母指定动作：
      - "force_str" (默认)            → 强转 string（现行硬规则）
      - "extract_unit_number"         → 提取单位前的数值（如 "1.5kg" → 1.5）
      - "extract_currency_amount"     → 去货币前缀转 float（如 "¥99.5" → 99.5）
      - "to_datetime"                 → 转 datetime
    AI 未指定的列走 force_str（向后兼容）。
    """
    handling = _strategy_mixed_handling(strategy)
    coerced: list[str] = []
    extracted: list[dict] = []
    for i, col in enumerate(df.columns):
        col_str = str(col)
        if col_str.startswith("_is_"):
            continue
        if df[col].dtype != object:
            continue
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue
        inferred = pd.api.types.infer_dtype(non_null, skipna=True)
        if inferred not in ("mixed", "mixed-integer", "mixed-integer-float"):
            continue

        col_letter = _col_index_to_letter_local(i)
        action_obj = handling.get(col_letter)
        action = action_obj.action if action_obj else "force_str"

        if action == "extract_unit_number" and action_obj.unit:
            try:
                unit = action_obj.unit
                pattern = re.compile(r"(-?\d+\.?\d*)\s*" + re.escape(unit))
                df[col] = df[col].astype(str).str.extract(pattern, expand=False).astype(float)
                extracted.append({"col": col_str, "unit": unit, "action": action})
                continue
            except Exception:
                pass
        elif action == "extract_currency_amount":
            try:
                df[col] = (
                    df[col].astype(str)
                    .str.replace(r"[¥$￥,]", "", regex=True)
                    .replace({"nan": None})
                    .astype(float)
                )
                extracted.append({"col": col_str, "action": action})
                continue
            except Exception:
                pass
        elif action == "to_datetime":
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
                extracted.append({"col": col_str, "action": action})
                continue
            except Exception:
                pass

        df[col] = df[col].astype(str).replace({"nan": None})
        coerced.append(col_str)

    if coerced:
        report.issues.append({
            "type": "mixed_type_coerced",
            "severity": "warning",
            "location": {"cols": coerced},
            "preserved": False,
            "action": f"混合类型列已转为文本: {coerced}",
            "recovery_hint": "用 pd.to_numeric(df['列名'], errors='coerce') 还原数字",
        })
    if extracted:
        action_summary = ", ".join(
            f"{e['col']}({e['action']}{':' + e.get('unit', '') if e.get('unit') else ''})"
            for e in extracted
        )
        report.issues.append({
            "type": "mixed_type_extracted",
            "severity": "info",
            "location": {"cols": [e["col"] for e in extracted]},
            "preserved": False,
            "action": f"AI 决策提取混合类型: {action_summary}",
            "recovery_hint": "AI 识别出含单位/货币/日期的列，已按业务语义转换",
        })
