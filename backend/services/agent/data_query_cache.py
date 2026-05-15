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


_CHUNK_THRESHOLD = 100_000  # 超过此行数走分块读取
_CHUNK_SIZE = 50_000        # 每块行数


def _prescan_schema(reader, target_sheet, actual_start, excel_path: str):
    """三段采样确定 target_schema。

    开头 300 + 中间 200 + 末尾 300 行采样，
    每段走 clean_excel 保持列名一致，
    跨段对比确定每列最稳定的类型。
    """
    import pyarrow as pa
    import pandas as pd
    from services.agent.excel_cleaner import clean_excel as _clean

    # 拿总行数
    try:
        probe_all = reader.load_sheet(target_sheet, header_row=actual_start)
        total_rows = probe_all.total_height
    except Exception:
        total_rows = 0

    # 收集三段采样
    segments: list[pd.DataFrame] = []
    _seg_params = {"header_row": actual_start}  # 与分块读取完全一致的参数

    # 开头 300 行
    head_n = min(300, max(total_rows, 1))
    head_df = reader.load_sheet(target_sheet, **_seg_params, n_rows=head_n).to_pandas()
    head_cleaned, _ = _clean(head_df, excel_path, "prescan_head", actual_start)
    segments.append(head_cleaned)

    # 中间 200 行（文件够大时）
    if total_rows > 1000:
        mid_skip = total_rows // 2
        try:
            mid_df = reader.load_sheet(
                target_sheet, **_seg_params, skip_rows=mid_skip, n_rows=200,
            ).to_pandas()
            mid_cleaned, _ = _clean(mid_df, excel_path, "prescan_mid", actual_start)
            segments.append(mid_cleaned)
        except Exception:
            pass  # 中间段读取失败不阻塞

    # 末尾 300 行（最关键：合计行在这里）
    if total_rows > 600:
        tail_skip = max(0, total_rows - 300)
        try:
            tail_df = reader.load_sheet(
                target_sheet, **_seg_params, skip_rows=tail_skip, n_rows=300,
            ).to_pandas()
            tail_cleaned, _ = _clean(tail_df, excel_path, "prescan_tail", actual_start)
            segments.append(tail_cleaned)
        except Exception:
            pass

    # 跨段对比：每列在每段的类型，取最保守的
    base_cols = segments[0].columns
    fields = []
    for col in base_cols:
        seg_types = []
        for seg in segments:
            if col in seg.columns:
                seg_types.append(_infer_segment_type(seg[col]))
        unified = _unify_column_types(seg_types) if seg_types else pa.string()
        fields.append(pa.field(str(col), unified))

    return pa.schema(fields)


def _infer_segment_type(series):
    """单段类型推断：只信任 pandas 已确定的纯类型，object 用 99% 阈值。"""
    import pyarrow as pa
    import pandas as pd

    dtype = str(series.dtype)
    if dtype in ("int64", "Int64", "int32", "int16"):
        # 超长数字可能是订单号，按 string 处理
        non_null = series.dropna()
        if len(non_null) > 0 and non_null.astype(str).str.len().max() > 15:
            return pa.string()
        return pa.int64()
    if dtype in ("float64", "Float64", "float32"):
        return pa.float64()
    if "datetime" in dtype:
        return pa.timestamp("ms")
    if dtype == "bool":
        return pa.bool_()
    # object 列：99% 阈值判断
    non_null = series.dropna()
    if len(non_null) == 0:
        return pa.string()
    numeric = pd.to_numeric(non_null, errors="coerce")
    if numeric.notna().sum() / len(non_null) >= 0.99:
        if (numeric.dropna() == numeric.dropna().astype("int64", errors="ignore")).all():
            if non_null.astype(str).str.len().max() > 15:
                return pa.string()
            return pa.int64()
        return pa.float64()
    dates = pd.to_datetime(non_null, errors="coerce", format="mixed")
    if dates.notna().sum() / len(non_null) >= 0.99:
        return pa.timestamp("ms")
    return pa.string()


def _unify_column_types(types) -> "pa.DataType":
    """跨段类型合并：取最保守的公共类型。"""
    import pyarrow as pa

    unique = list(set(str(t) for t in types))
    if len(unique) == 1:
        return types[0]  # 三段完全一致

    # 任一段是 string → 全部 string（最安全）
    if any(pa.types.is_string(t) or pa.types.is_large_string(t) for t in types):
        return pa.string()

    # 都是数值但不一致（int vs float）→ float
    if all(pa.types.is_integer(t) or pa.types.is_floating(t) for t in types):
        return pa.float64()

    # 兜底
    return pa.string()


def _cast_to_schema(df, target_schema):
    """将 DataFrame 强制对齐到 target_schema。cast 失败的列降级 string。"""
    import pyarrow as pa

    columns = []
    fields = []
    for field in target_schema:
        col_name = field.name
        if col_name not in df.columns:
            columns.append(pa.nulls(len(df), type=field.type))
            fields.append(field)
            continue

        series = df[col_name]
        try:
            arr = pa.array(series, type=field.type, from_pandas=True)
            fields.append(field)
        except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError,
                ValueError, TypeError):
            # cast 失败 → 降级 string，field 也改为 string
            arr = pa.array(
                series.astype(str).replace({"nan": None, "<NA>": None}),
                type=pa.string(),
            )
            fields.append(pa.field(col_name, pa.string()))
            logger.debug(f"Schema cast fallback | col={col_name} | {field.type} → string")
        columns.append(arr)

    return pa.Table.from_arrays(columns, schema=pa.schema(fields))


def _convert_excel_to_parquet(
    excel_path: str, cache_path: str, sheet: str | None,
    src_mtime: float, src_size: int, snapshot_path: str,
) -> list[str]:
    """Excel → Parquet（同步，线程池执行）。

    小文件（<10万行）：全量读取 + clean_excel 预处理
    大文件（≥10万行）：分块读取 + 分批写 Parquet，内存恒定 ~55MB
    """
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

    from services.agent.excel_cleaner import (
        clean_excel, write_cleaning_report, CleaningReport,
    )
    from services.agent.file_meta import extract_formulas, generate_file_meta, write_file_meta
    from services.agent.session_files import update_session_files

    resolved_name = target_sheet if isinstance(target_sheet, str) else sheet_names[0]

    # 自动检测表头行
    sheet_raw = reader.load_sheet(target_sheet, header_row=None, n_rows=_HEADER_MAX_SCAN)
    df_raw = sheet_raw.to_pandas()
    header_row = detect_header_row(df_raw.values.tolist())
    actual_start, header_depth = detect_header_depth(header_row, None)

    if actual_start > 0 or header_depth > 1:
        logger.info(
            f"Excel header auto-detected | src={Path(excel_path).name} "
            f"| header_row={actual_start} | depth={header_depth}"
        )

    # ── 单 Sheet 多表格检测 ──
    from services.agent.table_region_detector import convert_multi_region, detect_table_regions
    scan_raw = reader.load_sheet(target_sheet, header_row=None, n_rows=5000)
    scan_rows = scan_raw.to_pandas().values.tolist()
    regions = detect_table_regions(scan_rows)
    if len(regions) >= 2:
        convert_multi_region(
            excel_path, str(cache_path), regions, sheet_names,
            resolved_name, src_mtime, src_size, str(snapshot_path),
        )
        return sheet_names

    # 估算总行数（从 fastexcel 快速获取）
    try:
        probe = reader.load_sheet(target_sheet, header_row=actual_start)
        total_rows = probe.total_height
    except Exception:
        total_rows = 0

    tmp_path = str(Path(cache_path).parent / f"_tmp_{uuid.uuid4().hex[:8]}.parquet")

    # ── 小文件：全量读取 + 完整预处理 ──
    if total_rows < _CHUNK_THRESHOLD or header_depth > 1:
        if header_depth > 1:
            header_param = list(range(actual_start, actual_start + header_depth))
            df = pd.read_excel(excel_path, sheet_name=target_sheet, header=header_param)
        else:
            sheet_data = reader.load_sheet(target_sheet, header_row=actual_start)
            df = sheet_data.to_pandas()

        df, cleaning_report = clean_excel(
            df, excel_path, resolved_name, actual_start,
        )
        # 设置行号映射参数
        cleaning_report.header_row = actual_start
        cleaning_report.data_start_row = actual_start + header_depth + 1
        cleaning_report.row_offset = header_depth
        try:
            df.to_parquet(tmp_path, index=False, engine="pyarrow")
            os.rename(tmp_path, cache_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        # 旧格式兼容 + 新完整 meta
        write_cleaning_report(cache_path, cleaning_report)
        formulas, formula_skip = extract_formulas(excel_path, resolved_name)
        file_meta = generate_file_meta(
            df, cleaning_report,
            source_file=excel_path,
            sheet_count=len(sheet_names),
            formulas=formulas,
            formula_skip_reason=formula_skip,
        )
        write_file_meta(cache_path, file_meta)
        update_session_files(
            str(Path(cache_path).parent), cache_path,
            columns=[str(c) for c in df.columns if not str(c).startswith("_is_")],
            row_count=len(df),
            source_file=excel_path,
        )
        row_count = len(df)
        del df
    else:
        # ── 大文件：预扫描 schema + 分块读取 + 强制 cast ──
        import pyarrow as pa
        import pyarrow.parquet as pq

        # 预扫描：读 500 行 + clean_excel → 确定 target_schema
        target_schema = _prescan_schema(reader, target_sheet, actual_start, excel_path)

        writer = pq.ParquetWriter(tmp_path, target_schema)
        row_count = 0
        merged_report = CleaningReport()
        chunk_idx = 0

        while True:
            skip = _CHUNK_SIZE * chunk_idx
            try:
                chunk = reader.load_sheet(
                    target_sheet, header_row=actual_start,
                    n_rows=_CHUNK_SIZE, skip_rows=skip if skip > 0 else None,
                )
                df_chunk = chunk.to_pandas()
            except Exception:
                break

            if len(df_chunk) == 0:
                break

            # 每块独立清洗（与预扫描用相同参数）
            df_chunk, chunk_report = clean_excel(
                df_chunk, excel_path, resolved_name, actual_start,
            )
            merged_report.merge(chunk_report)

            # 强制对齐到预扫描 schema
            table = _cast_to_schema(df_chunk, target_schema)
            writer.write_table(table)
            row_count += len(df_chunk)
            chunk_idx += 1
            del df_chunk, table

        writer.close()
        if row_count > 0:
            os.rename(tmp_path, cache_path)
        else:
            Path(tmp_path).unlink(missing_ok=True)

        if row_count > 0:
            # 设置行号映射参数
            # 大文件路径条件保证 header_depth == 1（381行 if 分支的反条件）
            merged_report.header_row = actual_start
            merged_report.data_start_row = actual_start + header_depth + 1
            merged_report.row_offset = header_depth
            write_cleaning_report(cache_path, merged_report)
            # 从 Parquet 读采样数据生成完整 meta
            try:
                import duckdb
                sample_df = duckdb.sql(
                    f"SELECT * FROM '{cache_path}' LIMIT 500"
                ).to_df()
                formulas, formula_skip = extract_formulas(excel_path)
                file_meta = generate_file_meta(
                    sample_df, merged_report,
                    source_file=excel_path,
                    sheet_count=len(sheet_names),
                    formulas=formulas,
                    formula_skip_reason=formula_skip,
                )
                # 修正行数为实际总行数（采样 df 只有 500 行）
                file_meta.summary["row_count"] = row_count
                write_file_meta(cache_path, file_meta)
                update_session_files(
                    str(Path(cache_path).parent), cache_path,
                    columns=[str(c) for c in sample_df.columns if not str(c).startswith("_is_")],
                    row_count=row_count,
                    source_file=excel_path,
                )
                del sample_df
            except Exception as e:
                logger.warning(f"Failed to generate file meta for chunked file: {e}")

        logger.info(
            f"Excel chunked convert | src={Path(excel_path).name} "
            f"| chunks={chunk_idx} | chunk_size={_CHUNK_SIZE:,}"
        )

    Path(snapshot_path).write_text(f"{src_mtime},{src_size}")
    logger.info(
        f"Excel→Parquet cache | src={Path(excel_path).name} "
        f"sheet={sheet or 'default'} | rows={row_count:,} "
        f"| elapsed={time.monotonic() - start:.1f}s"
    )
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
        CleaningReport, clean_excel, write_cleaning_report,
    )
    from services.agent.file_meta import extract_formulas, generate_file_meta, write_file_meta
    from services.agent.session_files import update_session_files

    frames: list = []
    total_rows = 0
    merged_report = CleaningReport()
    first_data_start_row: int = 2  # 取第一个 Sheet 的行号映射，后续赋值
    for name in sheet_names:
        try:
            sheet_raw = reader.load_sheet(name, header_row=None, n_rows=_HEADER_MAX_SCAN)
            df_raw = sheet_raw.to_pandas()
            header_row = detect_header_row(df_raw.values.tolist())
            actual_start, header_depth = detect_header_depth(header_row, None)

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

            df, sheet_report = clean_excel(
                df, excel_path, name, actual_start,
            )
            merged_report.merge(sheet_report)
            # 记录第一个 Sheet 的行号映射（合并后沿用）
            if not frames:
                first_data_start_row = actual_start + header_depth + 1
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
    # 生成完整 meta（行号映射取第一个 Sheet 的值）
    merged_report.data_start_row = first_data_start_row
    formulas, formula_skip = extract_formulas(excel_path)
    file_meta = generate_file_meta(
        merged, merged_report,
        source_file=excel_path,
        sheet_count=len(sheet_names),
        formulas=formulas,
        formula_skip_reason=formula_skip,
    )
    write_file_meta(cache_path, file_meta)
    update_session_files(
        str(Path(cache_path).parent), cache_path,
        columns=[str(c) for c in merged.columns if not str(c).startswith("_is_")],
        row_count=len(merged),
        source_file=excel_path,
    )

    Path(snapshot_path).write_text(f"{src_mtime},{src_size}")
    logger.info(
        f"Excel→Parquet merge-all | src={Path(excel_path).name} "
        f"| sheets={len(frames)} | rows={len(merged):,} "
        f"| elapsed={time.monotonic() - start:.1f}s"
    )
    del merged
    return sheet_names
