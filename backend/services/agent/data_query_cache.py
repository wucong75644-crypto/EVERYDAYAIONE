"""data_query 文件检测与 Excel → Parquet 缓存模块"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from pathlib import Path

from loguru import logger

# 文件 magic bytes
_PARQUET_MAGIC = b"PAR1"
_ZIP_MAGIC = b"PK"  # xlsx 是 zip 格式


def fuzzy_match_sheet(target: str, sheet_names: list[str]) -> str:
    """Sheet 名模糊匹配（对标 _find_similar_file_global 的文件名纠错思路）。

    匹配策略：
      1. 精确匹配 → 直接返回
      2. 归一化匹配（strip + 全角→半角 + 去空格/连字符 + 小写）→ 返回实际名
      3. 包含关系（归一化后互相包含，≥4 字符）→ 返回最佳候选
      4. 无匹配 → 返回原始值（让 pandas 报原始错误）
    """
    if target in sheet_names:
        return target

    def _normalize(s: str) -> str:
        s = s.strip()
        # 全角括号→半角
        s = s.replace("\uff08", "(").replace("\uff09", ")")
        # 全角逗号/句号/冒号
        s = s.replace("\uff0c", ",").replace("\u3002", ".").replace("\uff1a", ":")
        # 去空格/连字符/下划线
        s = s.replace(" ", "").replace("-", "").replace("_", "")
        return s.lower()

    target_norm = _normalize(target)

    # 策略 2：归一化后精确匹配
    for name in sheet_names:
        if _normalize(name) == target_norm:
            logger.info(f"Sheet fuzzy match | '{target}' → '{name}' (normalized)")
            return name

    # 策略 3：归一化后包含关系（≥4 字符防误匹配）
    if len(target_norm) >= 4:
        for name in sheet_names:
            name_norm = _normalize(name)
            if target_norm in name_norm or name_norm in target_norm:
                logger.info(f"Sheet fuzzy match | '{target}' → '{name}' (contains)")
                return name

    return target


def detect_file_type(abs_path: str) -> str:
    """扩展名 + magic bytes 双重检测。"""
    ext = Path(abs_path).suffix.lower()
    if ext == ".parquet":
        return "parquet"
    if ext in (".xlsx", ".xls"):
        return "excel"
    if ext in (".csv", ".tsv"):
        return "csv"
    try:
        with open(abs_path, "rb") as f:
            header = f.read(4)
        if header == _PARQUET_MAGIC:
            return "parquet"
        if header[:2] == _ZIP_MAGIC:
            return "excel"
    except OSError:
        pass
    if ext in (".txt", ".dat", ""):
        return "csv"
    return "unknown"


def detect_encoding(abs_path: str) -> str:
    """检测文件编码，非 UTF-8 返回实际编码。"""
    try:
        import chardet
        with open(abs_path, "rb") as f:
            raw = f.read(64 * 1024)
        result = chardet.detect(raw)
        encoding = result.get("encoding", "utf-8") or "utf-8"
        if result.get("confidence", 0) > 0.7 and encoding.lower() not in ("utf-8", "ascii"):
            return encoding
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Encoding detection failed: {e}")
    return "utf-8"


# ── 多 Sheet 结构扫描 ──────────────────────────────

_MAX_SCAN_SHEETS = 200   # 超过此数量只扫描前 N 个（防超大 workbook 卡住）
_STRUCTURE_SAMPLE = 10   # 结构判断采样数（前N个+最后1个，不全扫）


def scan_sheet_structures(excel_path: str) -> list[dict]:
    """快速扫描 sheet 结构（列名+行数），采样判断而非全扫。

    策略：sheet 数>采样阈值时，只扫描前 N 个 + 最后 1 个，
    用采样结果推断全量。每个 sheet 只读 1 次（表头检测+列名合并）。

    Returns:
        [{"name": "Sheet1", "columns": ["col1", "col2"], "row_count": 500}, ...]
    """
    import fastexcel

    reader = fastexcel.read_excel(excel_path)
    all_names = reader.sheet_names[:_MAX_SCAN_SHEETS]

    # 采样：前 N 个 + 最后 1 个（去重）
    if len(all_names) <= _STRUCTURE_SAMPLE:
        scan_names = all_names
    else:
        scan_names = list(all_names[:_STRUCTURE_SAMPLE])
        if all_names[-1] not in scan_names:
            scan_names.append(all_names[-1])

    scanned: dict[str, dict] = {}
    for name in scan_names:
        try:
            # 读前 _HEADER_MAX_SCAN 行检测表头
            sheet_raw = reader.load_sheet(
                name, header_row=None, n_rows=_HEADER_MAX_SCAN,
            )
            df_raw = sheet_raw.to_pandas()
            header_row = detect_header_row(df_raw.values.tolist())

            # 用检测到的表头重新读取列名 + 行数
            sheet = reader.load_sheet(name, header_row=header_row)
            df = sheet.to_pandas()
            columns = [str(c) for c in df.columns if not str(c).startswith("Unnamed:")]
            row_count = len(df)

            scanned[name] = {"name": name, "columns": columns, "row_count": row_count}
        except Exception as e:
            logger.warning(f"Sheet scan failed | sheet={name} | error={e}")
            scanned[name] = {"name": name, "columns": [], "row_count": 0}

    # 未扫描的 sheet 用第一个扫描结果的结构推断（列名相同，行数标 -1 表示未知）
    first_scanned = next((s for s in scanned.values() if s["columns"]), None)
    results: list[dict] = []
    for name in all_names:
        if name in scanned:
            results.append(scanned[name])
        elif first_scanned:
            results.append({"name": name, "columns": first_scanned["columns"], "row_count": -1})
        else:
            results.append({"name": name, "columns": [], "row_count": 0})

    return results


def detect_same_structure(sheets: list[dict]) -> bool:
    """判断所有 sheet 是否结构相同（列名集合一致）。"""
    non_empty = [s for s in sheets if s["columns"]]
    if len(non_empty) < 2:
        return False
    first_cols = set(non_empty[0]["columns"])
    return all(set(s["columns"]) == first_cols for s in non_empty[1:])


# 按文件路径隔离的转换锁（LRU 上限 100，防止长期运行内存泄漏）
_MAX_LOCKS = 100
_convert_locks: dict[str, asyncio.Lock] = {}


async def ensure_parquet_cache(
    excel_path: str,
    sheet: str | None,
    staging_dir: str,
) -> tuple[str, list[str] | None]:
    """确保 Excel 文件有对应的 Parquet 缓存。

    Returns:
        (parquet_cache_path, sheet_names)
    """
    path_hash = hashlib.md5(excel_path.encode()).hexdigest()[:8]
    sheet_label = sheet or "sheet0"
    safe_sheet = re.sub(r'[^\w\-]', '_', str(sheet_label))
    cache_name = f"_cache_{path_hash}_{safe_sheet}_{Path(excel_path).stem}.parquet"

    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    cache_path = staging / cache_name
    snapshot_path = cache_path.with_suffix(".snapshot")

    stat = os.stat(excel_path)
    src_mtime, src_size = stat.st_mtime, stat.st_size

    if _snapshot_matches(cache_path, snapshot_path, src_mtime, src_size):
        return str(cache_path), None

    lock_key = f"{excel_path}:{sheet_label}"
    if lock_key not in _convert_locks:
        # LRU 淘汰：超过上限时删除最早的 key
        if len(_convert_locks) >= _MAX_LOCKS:
            oldest_key = next(iter(_convert_locks))
            del _convert_locks[oldest_key]
        _convert_locks[lock_key] = asyncio.Lock()

    async with _convert_locks[lock_key]:
        if _snapshot_matches(cache_path, snapshot_path, src_mtime, src_size):
            return str(cache_path), None

        loop = asyncio.get_running_loop()
        if sheet == "*":
            sheet_names = await loop.run_in_executor(
                None, _convert_all_sheets_to_parquet,
                excel_path, str(cache_path), src_mtime, src_size,
                str(snapshot_path),
            )
        else:
            sheet_names = await loop.run_in_executor(
                None, _convert_excel_to_parquet,
                excel_path, str(cache_path), sheet, src_mtime, src_size,
                str(snapshot_path),
            )
        return str(cache_path), sheet_names


def _snapshot_matches(
    cache_path: Path, snapshot_path: Path,
    src_mtime: float, src_size: int,
) -> bool:
    if not cache_path.exists() or not snapshot_path.exists():
        return False
    try:
        snap = snapshot_path.read_text().strip().split(",")
        if len(snap) != 2:
            return False
        # float 精度容差比较（避免 str→float→str 精度丢失）
        return abs(float(snap[0]) - src_mtime) < 0.001 and int(snap[1]) == src_size
    except (ValueError, OSError):
        return False

_HEADER_MAX_SCAN = 20   # 扫描前 N 行寻找表头
_HEADER_STR_RATIO = 0.7  # 非空值中字符串占比阈值


def detect_header_row(rows: list[list]) -> int:
    """自动检测 Excel 表头行号（messytables 列数众数法 + csv.Sniffer 类型验证）。

    算法：
    1. 统计每行非空单元格数，取众数（= 数据区期望列数）
    2. 从上往下扫，找第一行同时满足：
       - 非空单元格数 ≥ 众数 × 0.5（messytables 思路：标题行通常只有 1-2 个非空格）
       - 非空值中字符串占比 ≥ 70%（csv.Sniffer 思路：数据行多为数字/日期）
    3. 找不到 → 返回 0（标准表格，不影响现有行为）
    """
    if not rows:
        return 0

    # ── Step 1: 统计每行非空数，取众数 ──
    from collections import Counter

    counts: list[int] = []
    for row in rows[:_HEADER_MAX_SCAN]:
        n = sum(1 for c in row if c is not None and str(c).strip())
        counts.append(n)

    # 排除只有 0-1 个非空值的行（标题/空行），只统计数据区
    data_counts = [c for c in counts if c > 1]
    if not data_counts:
        return 0
    modal = Counter(data_counts).most_common(1)[0][0]

    threshold = modal * 0.5

    # ── Step 2: 找第一行满足 非空数≥阈值 + 字符串占比≥70% ──
    for i, row in enumerate(rows[:_HEADER_MAX_SCAN]):
        non_null = [c for c in row if c is not None and str(c).strip()]
        if len(non_null) < threshold:
            continue

        # 类型验证：表头行的值应该大部分是字符串
        str_count = sum(1 for v in non_null if isinstance(v, str))
        if str_count / len(non_null) >= _HEADER_STR_RATIO:
            return i

    return 0


def detect_header_depth(
    header_row: int,
    merged_ranges: list[tuple[int, int, int, int]] | None = None,
) -> tuple[int, int]:
    """基于合并元数据检测多级表头（Spark-excel 同模式）。返回 (actual_start, depth)。"""
    if not merged_ranges:
        return header_row, 1

    # 找表头区域（header_row 上方）的水平合并：跨列且在 header_row+1 行及以上
    header_excel_row = header_row + 1  # 0-indexed → 1-indexed
    min_merge_row = header_excel_row  # 最上层合并行
    has_header_merge = False

    for min_row, max_row, min_col, max_col in merged_ranges:
        if max_col <= min_col:
            continue  # 非水平合并，跳过
        if min_row > header_excel_row:
            continue  # 在数据区，不是表头
        if min_row < min_merge_row:
            min_merge_row = min_row
        has_header_merge = True

    if not has_header_merge:
        return header_row, 1

    # depth = 从最上层合并行到 header_row（含）
    actual_start_0indexed = min_merge_row - 1  # 1-indexed → 0-indexed
    depth = header_excel_row - min_merge_row + 1
    depth = min(depth, 3)  # 安全上限
    return actual_start_0indexed, depth


def _convert_excel_to_parquet(
    excel_path: str, cache_path: str, sheet: str | None,
    src_mtime: float, src_size: int, snapshot_path: str,
) -> list[str]:
    """Excel → Parquet（同步，线程池执行）。返回所有 Sheet 名称。"""
    import pandas as pd

    import fastexcel

    start = time.monotonic()
    reader = fastexcel.read_excel(excel_path)
    sheet_names = reader.sheet_names

    target_sheet: str | int
    if sheet is None:
        target_sheet = 0
    elif sheet.isdigit():
        target_sheet = int(sheet)
    else:
        target_sheet = fuzzy_match_sheet(sheet, sheet_names)

    # Layer 1: 结构检测（合并区域 / 隐藏行列 / 筛选状态）
    from services.agent.excel_cleaner import (
        _detect_structure, clean_excel, write_cleaning_report,
    )

    resolved_name = target_sheet if isinstance(target_sheet, str) else sheet_names[0]
    structure = _detect_structure(excel_path, resolved_name)
    merged = structure.merged_ranges if structure else None

    # 自动检测表头行 + 基于合并元数据的多级表头深度
    sheet_raw = reader.load_sheet(target_sheet, header_row=None, n_rows=_HEADER_MAX_SCAN)
    df_raw = sheet_raw.to_pandas()
    header_row = detect_header_row(df_raw.values.tolist())
    actual_start, header_depth = detect_header_depth(header_row, merged)

    # fastexcel header_row 只支持单行表头（int），多级表头用 pandas 兼容
    header_param: int | list[int] = actual_start
    if header_depth > 1:
        header_param = list(range(actual_start, actual_start + header_depth))
        # 多级表头回退 pandas（fastexcel 不支持 MultiIndex header）
        df = pd.read_excel(excel_path, sheet_name=target_sheet, header=header_param)
    else:
        sheet_data = reader.load_sheet(target_sheet, header_row=actual_start)
        df = sheet_data.to_pandas()

    if actual_start > 0 or header_depth > 1:
        logger.info(
            f"Excel header auto-detected | src={Path(excel_path).name} "
            f"| header_row={actual_start} | depth={header_depth}"
        )

    # Layer 2+3: 智能清洗 + 质量校验（复用已检测的 structure）
    df, cleaning_report = clean_excel(
        df, excel_path, resolved_name, actual_start, structure,
    )

    tmp_path = str(Path(cache_path).parent / f"_tmp_{uuid.uuid4().hex[:8]}.parquet")
    try:
        df.to_parquet(tmp_path, index=False, engine="pyarrow")
        os.rename(tmp_path, cache_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    write_cleaning_report(cache_path, cleaning_report)
    Path(snapshot_path).write_text(f"{src_mtime},{src_size}")
    logger.info(
        f"Excel→Parquet cache | src={Path(excel_path).name} "
        f"sheet={sheet or 'default'} | rows={len(df):,} "
        f"| elapsed={time.monotonic() - start:.1f}s"
    )
    del df
    return sheet_names


_MAX_MERGE_ROWS = 1_000_000  # 合并后总行数上限（防 OOM）


def _convert_all_sheets_to_parquet(
    excel_path: str, cache_path: str,
    src_mtime: float, src_size: int, snapshot_path: str,
) -> list[str]:
    """所有同结构 Sheet 合并为单个 Parquet（加 _sheet 列标识来源）。"""
    import fastexcel
    import pandas as pd

    start = time.monotonic()
    reader = fastexcel.read_excel(excel_path)
    sheet_names = reader.sheet_names

    from services.agent.excel_cleaner import (
        CleaningReport, _detect_structure, clean_excel, write_cleaning_report,
    )

    frames: list = []
    total_rows = 0
    merged_report = CleaningReport()
    for name in sheet_names:
        try:
            structure = _detect_structure(excel_path, name)
            merged = structure.merged_ranges if structure else None

            sheet_raw = reader.load_sheet(name, header_row=None, n_rows=_HEADER_MAX_SCAN)
            df_raw = sheet_raw.to_pandas()
            header_row = detect_header_row(df_raw.values.tolist())
            actual_start, header_depth = detect_header_depth(header_row, merged)

            if header_depth > 1:
                header_param = list(range(actual_start, actual_start + header_depth))
                df = pd.read_excel(excel_path, sheet_name=name, header=header_param)
            else:
                sheet_data = reader.load_sheet(name, header_row=actual_start)
                df = sheet_data.to_pandas()

            if df.empty:
                continue

            total_rows += len(df)
            if total_rows > _MAX_MERGE_ROWS:
                raise ValueError(
                    f"合并后总行数（{total_rows:,}）超过上限（{_MAX_MERGE_ROWS:,}），"
                    f"请用 sheet 参数逐个读取。"
                )

            # 三层清洗（复用已检测的 structure）
            df, sheet_report = clean_excel(
                df, excel_path, name, actual_start, structure,
            )
            merged_report.merge(sheet_report)
            df.insert(0, "_sheet", name)
            frames.append(df)
        except ValueError:
            raise
        except Exception as e:
            logger.warning(f"Sheet merge skip | sheet={name} | error={e}")
            continue

    if not frames:
        raise ValueError(f"所有 Sheet 均为空或读取失败: {Path(excel_path).name}")

    merged = pd.concat(frames, ignore_index=True)

    tmp_path = str(Path(cache_path).parent / f"_tmp_{uuid.uuid4().hex[:8]}.parquet")
    try:
        merged.to_parquet(tmp_path, index=False, engine="pyarrow")
        os.rename(tmp_path, cache_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    write_cleaning_report(cache_path, merged_report)
    Path(snapshot_path).write_text(f"{src_mtime},{src_size}")
    logger.info(
        f"Excel→Parquet merge-all | src={Path(excel_path).name} "
        f"| sheets={len(frames)} | rows={len(merged):,} "
        f"| elapsed={time.monotonic() - start:.1f}s"
    )
    del merged
    return sheet_names
