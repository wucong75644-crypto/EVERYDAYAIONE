"""Excel 清洗入口 (Layer 3)。

clean_excel 串联 7 个清洗步骤：
  1. 多级表头展平
  2. 合并单元格填充（按 strategy.merged_cell_actions）
  3. 列名去重
  4. 合计行标记（strategy.summary_rows 优先，special_rows 兼容旧 prescan）
  5. 空行空列处理（strategy.preserve_empty_rows 保留 AI 指定空行）
  6. 混合类型修正（strategy.mixed_type_handling）
  7. 整数列修复（strategy.id_columns 保护 ID 列）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from services.agent.excel_cleaner._strategy_helpers import _strategy_summary_rows
from services.agent.excel_cleaner.actions import (
    _apply_merge_fill,
    _coerce_object_columns,
    _deduplicate_columns,
    _fix_int_columns,
    _flatten_multi_header,
    _mark_summary_rows,
    _mark_summary_rows_from_strategy,
    _remove_empty_rows_cols,
)
from services.agent.excel_cleaner.report import CleaningReport, ExcelStructure


def clean_excel(
    df: pd.DataFrame,
    excel_path: str,
    sheet_name: str | int,
    header_row: int = 0,
    structure: ExcelStructure | None = None,
    special_rows: dict[str, list[int]] | None = None,
    chunk_row_offset: int = 0,  # 大文件分块时的数据行偏移量（不含 header）
    strategy: Any = None,         # Phase 4: CleaningStrategy（None = 全部硬规则）
) -> tuple[pd.DataFrame, CleaningReport]:
    """清洗入口：merge 填充 + 表头展平 + 去重 + 合计行标记 + 空行列 + 类型修正。

    Phase 4 改造：增加 strategy 参数（CleaningStrategy 或 None）。
    strategy=None 时行为与现版本完全等同（向后兼容）。
    strategy 提供时按 AI 决策执行，缺失字段自动走硬规则兜底。
    """
    report = CleaningReport(original_shape=(len(df), len(df.columns)))

    # Step 1: 多级表头展平（无策略影响）
    _flatten_multi_header(df, report)

    # Step 2: 合并单元格精确填充
    if structure and structure.merged_ranges:
        _apply_merge_fill(df, structure, header_row, report,
                          chunk_row_offset, strategy=strategy)

    # Step 3: 列名去重（无策略影响）
    _deduplicate_columns(df, report)

    # Step 4: 合计行标记
    #   strategy.summary_rows 优先（Excel 1-indexed 行号 + 业务语义）
    #   未提供时退回 special_rows（旧 prescan 兼容）
    _strategy_summary = _strategy_summary_rows(strategy)
    if _strategy_summary:
        _mark_summary_rows_from_strategy(
            df, _strategy_summary, header_row, report, chunk_row_offset,
        )
    elif special_rows:
        _mark_summary_rows(df, special_rows, header_row, report, chunk_row_offset)

    # Step 5: 空行空列处理
    _remove_empty_rows_cols(df, report, structure, strategy=strategy,
                            header_row=header_row, chunk_row_offset=chunk_row_offset)

    # Step 6-7: 类型修正
    _coerce_object_columns(df, report, strategy=strategy)
    _fix_int_columns(df, report, strategy=strategy)

    report.final_shape = (len(df), len(df.columns))
    if report.has_changes():
        logger.info(
            f"Excel cleaned | src={Path(excel_path).name} "
            f"| {report.original_shape} → {report.final_shape}"
        )
    return df, report
