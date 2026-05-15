"""单 Sheet 多表格区域检测 + 分区域转换。

电商报表常见"一个 Sheet 里放多张表"（空行分隔）。
本模块检测区域并分别输出独立 Parquet。

设计文档：docs/document/TECH_文件处理系统.md §二（单 Sheet 多表格检测）
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

_HEADER_STR_RATIO = 0.7   # 表头行字符串占比阈值（与 detect_header_row 一致）
_MAX_SCAN_ROWS = 5000      # 最多扫描前 N 行检测多表格


@dataclass
class TableRegion:
    """单 Sheet 内一个表格区域。"""
    name: str | None         # 表格名（如"退货表"），None 时自动命名
    header_row: int          # 表头在原始行数据中的索引（0-indexed）
    data_start: int          # 数据起始索引（0-indexed，= header_row + 1）
    data_end: int            # 数据结束索引（不含，= 下一个空行或末尾）
    columns: list[str]       # 列名列表
    row_count: int           # 数据行数


def detect_table_regions(rows: list[list]) -> list[TableRegion]:
    """检测单 Sheet 内的多个表格区域。

    算法：
    1. 找所有全空行作为候选分隔符
    2. 将行序列按空行切分为多个区间
    3. 在每个区间内找表头行（字符串占比 ≥ 70%）
    4. 表头上方如果有单值行，视为表格名称
    5. 只有 1 个区间 → 返回空列表（单表格，走现有逻辑）

    Args:
        rows: 原始行数据（list of list），每行是单元格值列表

    Returns:
        多表格时返回 TableRegion 列表（≥ 2 个），
        单表格或无法识别时返回空列表。
    """
    if not rows:
        return []

    scan_rows = rows[:_MAX_SCAN_ROWS]

    # Step 1: 找全空行位置
    empty_indices = _find_empty_rows(scan_rows)
    if not empty_indices:
        return []  # 无空行 → 单表格

    # Step 2: 按空行切分区间
    segments = _split_by_empty_rows(len(scan_rows), empty_indices)
    if len(segments) < 2:
        return []  # 只有一个区间 → 单表格

    # Step 3: 计算数据区的列数众数（用于判断表头）
    modal = _compute_modal_col_count(scan_rows)
    if modal < 2:
        return []

    threshold = modal * 0.5

    # Step 4: 在每个区间内找表头
    regions: list[TableRegion] = []
    for seg_start, seg_end in segments:
        if seg_end - seg_start < 2:
            continue  # 区间太小（不足1行表头+1行数据）
        region = _detect_region_in_segment(scan_rows, seg_start, seg_end, threshold)
        if region:
            regions.append(region)

    # 只有 1 个区域 → 说明空行是数据缺失不是分隔符
    if len(regions) < 2:
        return []

    return regions


def _find_empty_rows(rows: list[list]) -> list[int]:
    """找所有全空行的索引。"""
    empty = []
    for i, row in enumerate(rows):
        if all(c is None or str(c).strip() == "" for c in row):
            empty.append(i)
    return empty


def _split_by_empty_rows(
    total_rows: int, empty_indices: list[int],
) -> list[tuple[int, int]]:
    """按空行位置将行序列切分为多个区间 (start, end)。"""
    segments: list[tuple[int, int]] = []
    prev = 0
    for idx in empty_indices:
        if idx > prev:
            segments.append((prev, idx))
        prev = idx + 1
    if prev < total_rows:
        segments.append((prev, total_rows))
    return segments


def _compute_modal_col_count(rows: list[list]) -> int:
    """计算数据行非空列数的众数。"""
    from collections import Counter
    counts = []
    for row in rows:
        n = sum(1 for c in row if c is not None and str(c).strip())
        if n > 1:
            counts.append(n)
    if not counts:
        return 0
    return Counter(counts).most_common(1)[0][0]


def _is_header_row(row: list, threshold: float) -> bool:
    """判断一行是否是表头（非空数 ≥ 阈值 且 字符串占比 ≥ 70%）。"""
    non_null = [c for c in row if c is not None and str(c).strip()]
    if len(non_null) < threshold:
        return False
    str_count = sum(1 for v in non_null if isinstance(v, str))
    return str_count / len(non_null) >= _HEADER_STR_RATIO


def _is_single_value_row(row: list) -> bool:
    """判断一行是否只有 1 个非空值（表格名称行特征）。"""
    non_null = [c for c in row if c is not None and str(c).strip()]
    return len(non_null) == 1


def _extract_region_name(rows: list[list], seg_start: int, header_idx: int) -> str | None:
    """从表头上方找表格名称（单值行）。"""
    for i in range(header_idx - 1, seg_start - 1, -1):
        if _is_single_value_row(rows[i]):
            non_null = [c for c in rows[i] if c is not None and str(c).strip()]
            return str(non_null[0]).strip()
    return None


def _detect_region_in_segment(
    rows: list[list],
    seg_start: int,
    seg_end: int,
    threshold: float,
) -> TableRegion | None:
    """在一个区间内检测表格区域。"""
    # 找表头行
    header_idx = None
    for i in range(seg_start, seg_end):
        if _is_header_row(rows[i], threshold):
            header_idx = i
            break

    if header_idx is None:
        return None

    # 空行后面紧跟的区间没有足够的数据行
    data_start = header_idx + 1
    if data_start >= seg_end:
        return None

    # 提取列名
    columns = [str(c).strip() if c is not None and str(c).strip() else f"列{j+1}"
               for j, c in enumerate(rows[header_idx])]
    # 去掉尾部空列名
    while columns and columns[-1].startswith("列"):
        columns.pop()

    name = _extract_region_name(rows, seg_start, header_idx)
    row_count = seg_end - data_start

    return TableRegion(
        name=name,
        header_row=header_idx,
        data_start=data_start,
        data_end=seg_end,
        columns=columns,
        row_count=row_count,
    )


def convert_multi_region(
    excel_path: str,
    cache_path: str,
    regions: list[TableRegion],
    sheet_names: list[str],
    resolved_name: str,
    src_mtime: float,
    src_size: int,
    snapshot_path: str,
) -> list[str]:
    """单 Sheet 多表格区域分别输出独立 Parquet + meta + session_files。"""
    import pandas as pd
    from services.agent.excel_cleaner import CleaningReport, clean_excel, write_cleaning_report
    from services.agent.file_meta import extract_formulas, generate_file_meta, write_file_meta
    from services.agent.session_files import update_session_files

    start = time.monotonic()
    output_paths: list[str] = []
    stem = Path(excel_path).stem
    staging_dir = str(Path(cache_path).parent)

    # 公式提取一次，所有区域共享
    formulas, formula_skip = extract_formulas(excel_path, resolved_name)

    for i, region in enumerate(regions):
        region_name = region.name or f"Region_{i + 1}"
        safe_name = re.sub(r'[^\w\-]', '_', region_name)
        region_cache = str(Path(staging_dir) / f"_cache_{safe_name}_{stem}.parquet")

        # 按行范围读取：skiprows 跳过表头前的行，header=0 表示剩余部分第一行是表头
        skip = list(range(0, region.header_row)) if region.header_row > 0 else None
        df = pd.read_excel(
            excel_path, sheet_name=resolved_name,
            header=0, skiprows=skip,
            nrows=region.row_count,
        )

        df, cleaning_report = clean_excel(df, excel_path, region_name, region.header_row)
        cleaning_report.header_row = region.header_row
        cleaning_report.data_start_row = region.data_start + 1  # 0-indexed → Excel 1-indexed
        cleaning_report.row_offset = 1

        tmp = str(Path(staging_dir) / f"_tmp_{uuid.uuid4().hex[:8]}.parquet")
        try:
            df.to_parquet(tmp, index=False, engine="pyarrow")
            os.rename(tmp, region_cache)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            continue

        write_cleaning_report(region_cache, cleaning_report)
        file_meta = generate_file_meta(
            df, cleaning_report,
            source_file=excel_path,
            sheet_count=len(sheet_names),
            formulas=formulas,
            formula_skip_reason=formula_skip,
        )
        file_meta.summary["description"] = (
            f"{stem} / {region_name}，{len(df)}行×{len(df.columns)}列"
        )
        write_file_meta(region_cache, file_meta)

        # 注册到 session_files（含同源标记）
        update_session_files(
            staging_dir, region_cache,
            columns=[str(c) for c in df.columns if not str(c).startswith("_is_")],
            row_count=len(df),
            source_file=excel_path,
            source_region=region_name,
        )
        output_paths.append(region_cache)
        del df

    Path(snapshot_path).write_text(f"{src_mtime},{src_size}")
    logger.info(
        f"Excel multi-region | src={Path(excel_path).name} "
        f"| regions={len(regions)} | elapsed={time.monotonic() - start:.1f}s"
    )
    return output_paths
