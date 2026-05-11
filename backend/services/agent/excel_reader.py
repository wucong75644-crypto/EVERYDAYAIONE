"""
Excel 结构化读取（openpyxl 两次读取，保留公式+编号）

输出格式对标 Claude：
  ['A2:义乌部门', 'B2:义乌租金', 'C2:76800', 'D2:[公式]=C2/12']
  空单元格跳过，不做 ffill。

设计文档：docs/document/TECH_file_read统一工具.md
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from services.agent.agent_result import AgentResult

# ── 常量 ──

_MAX_ROWS_FULL = 10000       # 全量输出行数上限，超过则截断
_MAX_FORMULA_CELLS = 500     # 公式单元格输出上限（Pass 1 扫描提前终止）
_PREVIEW_HEAD = 5            # 大文件预览：前 N 行
_PREVIEW_TAIL = 5            # 大文件预览：后 N 行
_FORMULA_PREFIX = "[公式]"   # 公式标记前缀


def _col_letter(col_idx: int) -> str:
    """1-indexed 列号 → Excel 列字母（1=A, 27=AA）。"""
    result = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _read_sheet_structured(
    excel_path: str,
    sheet_name: str | None = None,
) -> tuple[list[list[str]], list[str], int, int, int]:
    """读取单个 sheet，返回结构化行数据。

    Returns:
        (rows, formula_cross_refs, total_rows, total_cols, formula_count)
        - rows: 每行是 ['A2:义乌部门', 'D2:[公式]=C2/12'] 格式的列表
        - formula_cross_refs: 跨 sheet 引用列表
        - total_rows, total_cols: 实际行列数
        - formula_count: 公式单元格总数
    """
    import openpyxl

    # Pass 1: 读公式字符串
    wb_formula = openpyxl.load_workbook(
        excel_path, read_only=True, data_only=False,
    )
    if sheet_name:
        ws_formula = wb_formula[sheet_name]
    else:
        ws_formula = wb_formula.active
    if ws_formula is None:
        wb_formula.close()
        return [], [], 0, 0, 0

    # 收集公式 {(row, col): formula_str}，达到上限提前终止
    formulas: dict[tuple[int, int], str] = {}
    _scan_done = False
    for row in ws_formula.iter_rows():
        if _scan_done:
            break
        for cell in row:
            if not hasattr(cell, "data_type"):
                continue
            if cell.data_type == "f" and cell.value:
                formulas[(cell.row, cell.column)] = str(cell.value)
                if len(formulas) >= _MAX_FORMULA_CELLS:
                    _scan_done = True
                    break
    wb_formula.close()

    # Pass 2: 读计算值
    wb_value = openpyxl.load_workbook(
        excel_path, read_only=True, data_only=True,
    )
    if sheet_name:
        ws_value = wb_value[sheet_name]
    else:
        ws_value = wb_value.active
    if ws_value is None:
        wb_value.close()
        return [], [], 0, 0, 0

    rows: list[list[str]] = []
    cross_refs: list[str] = []
    total_rows = 0
    max_col = 0

    for row in ws_value.iter_rows():
        total_rows += 1
        row_cells: list[str] = []
        for cell in row:
            # read_only 模式下空区域产生 EmptyCell，跳过
            if not hasattr(cell, "column") or cell.column is None:
                continue
            if cell.column > max_col:
                max_col = cell.column
            col_letter = _col_letter(cell.column)
            coord = f"{col_letter}{cell.row}"
            formula = formulas.get((cell.row, cell.column))

            if formula:
                # 公式单元格
                row_cells.append(f"{coord}:{_FORMULA_PREFIX}{formula}")
                # 检测跨 sheet 引用
                if "!" in formula:
                    cross_refs.append(f"{coord} → {formula}")
            elif cell.value is not None:
                # 有值单元格（空值跳过）
                row_cells.append(f"{coord}:{cell.value}")

        if row_cells:
            rows.append(row_cells)

    wb_value.close()
    return rows, cross_refs, total_rows, max_col, len(formulas)


def _format_structured_output(
    rows: list[list[str]],
    cross_refs: list[str],
    total_rows: int,
    total_cols: int,
    formula_count: int,
    sheet_name: str,
    sheet_overview: str,
    rel_path: str,
) -> str:
    """将结构化行数据格式化为输出文本。"""
    lines: list[str] = []
    lines.append(f"=== Sheet: {sheet_name} ===")
    lines.append(f"行数: {total_rows}, 列数: {total_cols}")
    lines.append("")

    is_large = total_rows > _MAX_ROWS_FULL

    if is_large:
        # 大文件：前5行 + 后5行
        head_rows = rows[:_PREVIEW_HEAD]
        tail_rows = rows[-_PREVIEW_TAIL:] if len(rows) > _PREVIEW_TAIL else []

        for row in head_rows:
            lines.append(str(row))
        if tail_rows:
            lines.append(f"... (省略 {len(rows) - _PREVIEW_HEAD - _PREVIEW_TAIL} 行) ...")
            for row in tail_rows:
                lines.append(str(row))
    else:
        # 小文件：全量输出
        for row in rows:
            lines.append(str(row))

    # 公式统计
    lines.append("")
    if formula_count > 0:
        lines.append(f"公式统计: {formula_count}个公式单元格")
        if cross_refs:
            for ref in cross_refs[:10]:
                lines.append(f"跨Sheet引用: {ref}")
            if len(cross_refs) > 10:
                lines.append(f"... 等{len(cross_refs)}个跨Sheet引用")
    else:
        lines.append("公式统计: 0个（纯数据表）")

    # Sheet 概览
    if sheet_overview:
        lines.append("")
        lines.append(sheet_overview)

    # 后续操作提示
    lines.append("")
    lines.append(f"后续查询: file_read(path=\"{rel_path}\", sql=\"SELECT ... FROM data\")")

    return "\n".join(lines)


def _build_sheet_overview(excel_path: str) -> tuple[str, list[str]]:
    """扫描所有 sheet，返回概览文本和 sheet 名列表。"""
    from services.agent.data_query_cache import scan_sheet_structures

    try:
        structures = scan_sheet_structures(excel_path)
    except Exception:
        return "", []

    if len(structures) <= 1:
        return "", [s["name"] for s in structures]

    lines = [f"[Sheet 概览] 共 {len(structures)} 个 Sheet"]
    for s in structures[:10]:
        cols = ", ".join(s["columns"][:5])
        if len(s["columns"]) > 5:
            cols += f" (+{len(s['columns']) - 5}列)"
        lines.append(f"- \"{s['name']}\" | {s['row_count']}行 × {len(s['columns'])}列 | 列: {cols}")
    if len(structures) > 10:
        lines.append(f"- ... 等{len(structures)}个 Sheet")

    return "\n".join(lines), [s["name"] for s in structures]


async def read_excel_structured(
    abs_path: str,
    sheet: str | None,
    staging_dir: str,
) -> AgentResult:
    """Excel 结构化读取入口（异步包装）。

    Args:
        abs_path: Excel 文件绝对路径
        sheet: Sheet 名称（None=第一个 sheet）
        staging_dir: staging 目录路径（写带编号 Parquet）
    """
    start = time.monotonic()
    filename = Path(abs_path).name

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, _read_excel_structured_sync,
            abs_path, sheet, staging_dir,
        )
        elapsed = time.monotonic() - start
        logger.info(
            f"Excel structured read | file={filename} | "
            f"elapsed={elapsed:.2f}s | status={result.status}"
        )
        return result
    except Exception as e:
        logger.error(f"Excel structured read failed | file={filename} | error={e}")
        return AgentResult(
            summary=f"Excel 读取失败: {e}",
            status="error",
            error_message=str(e),
        )


def _read_excel_structured_sync(
    abs_path: str,
    sheet: str | None,
    staging_dir: str,
) -> AgentResult:
    """Excel 结构化读取（同步，线程池执行）。"""
    filename = Path(abs_path).name

    # 扫描所有 sheet
    sheet_overview, sheet_names = _build_sheet_overview(abs_path)

    # sheet 名模糊匹配
    target_sheet = sheet
    if sheet and sheet_names:
        from services.agent.data_query_cache import fuzzy_match_sheet
        target_sheet = fuzzy_match_sheet(sheet, sheet_names)

    # 读取目标 sheet
    rows, cross_refs, total_rows, total_cols, formula_count = (
        _read_sheet_structured(abs_path, target_sheet)
    )

    if not rows:
        return AgentResult(
            summary=f"Excel 文件为空或无数据: {filename}",
            status="empty",
        )

    # 计算相对路径（用于提示）
    rel_path = filename

    # 格式化输出
    display_sheet = target_sheet or sheet_names[0] if sheet_names else "Sheet1"
    text = _format_structured_output(
        rows, cross_refs, total_rows, total_cols, formula_count,
        display_sheet, sheet_overview, rel_path,
    )

    # 写 staging 带编号 Parquet
    _write_staging_parquet(rows, staging_dir, filename)

    return AgentResult(summary=text, status="success")


def _write_staging_parquet(
    rows: list[list[str]],
    staging_dir: str,
    filename: str,
) -> None:
    """将结构化行数据写入 staging Parquet（cell/row/col/value/formula）。"""
    import re

    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    # 解析 'A2:义乌部门' 或 'D2:[公式]=C2/12' 格式
    pattern = re.compile(r"^([A-Z]+)(\d+):(.*)$")

    for row_cells in rows:
        for cell_str in row_cells:
            m = pattern.match(cell_str)
            if not m:
                continue
            col_letter, row_num, content = m.group(1), int(m.group(2)), m.group(3)
            coord = f"{col_letter}{row_num}"

            if content.startswith(_FORMULA_PREFIX):
                formula = content[len(_FORMULA_PREFIX):]
                records.append({
                    "cell": coord,
                    "row": row_num,
                    "col": col_letter,
                    "value": None,
                    "formula": formula,
                })
            else:
                records.append({
                    "cell": coord,
                    "row": row_num,
                    "col": col_letter,
                    "value": str(content),
                    "formula": None,
                })

    if not records:
        return

    try:
        import pandas as pd
        df = pd.DataFrame(records)
        stem = Path(filename).stem
        parquet_name = f"_structured_{stem}.parquet"
        df.to_parquet(str(staging / parquet_name), index=False, engine="pyarrow")
        logger.info(
            f"Structured Parquet written | file={parquet_name} | "
            f"cells={len(records)}"
        )
    except Exception as e:
        logger.warning(f"Structured Parquet write failed: {e}")
