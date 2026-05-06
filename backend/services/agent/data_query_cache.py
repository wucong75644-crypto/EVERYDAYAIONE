"""
data_query 文件检测与 Excel → Parquet 缓存模块

文件类型/编码检测 + 双重检查锁 + (mtime,size) 快照校验 + 原子写入。
从 data_query_executor.py 拆分。
"""

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

_SCAN_NROWS = 5          # 扫描每个 sheet 的前 N 行（只需列名+行数）
_MAX_SCAN_SHEETS = 200   # 超过此数量只扫描前 N 个（防超大 workbook 卡住）


def scan_sheet_structures(excel_path: str) -> list[dict]:
    """快速扫描所有 sheet 的结构（列名+行数），不全量加载数据。

    Returns:
        [{"name": "Sheet1", "columns": ["col1", "col2"], "row_count": 500}, ...]
    """
    import pandas as pd

    xl = pd.ExcelFile(excel_path, engine="calamine")
    sheet_names = xl.sheet_names[:_MAX_SCAN_SHEETS]
    results: list[dict] = []

    for name in sheet_names:
        try:
            # 只读前几行检测表头
            df_raw = pd.read_excel(
                xl, sheet_name=name, engine="calamine",
                header=None, nrows=_HEADER_MAX_SCAN,
            )
            header_row = detect_header_row(df_raw.values.tolist())

            # 用检测到的表头行读取列名
            df_head = pd.read_excel(
                xl, sheet_name=name, engine="calamine",
                header=header_row, nrows=_SCAN_NROWS,
            )
            columns = [str(c) for c in df_head.columns
                        if not str(c).startswith("Unnamed:")]

            # 行数：用 calamine 引擎全量计数（只读行数，不加载数据）
            df_count = pd.read_excel(
                xl, sheet_name=name, engine="calamine",
                header=header_row, usecols=[0],
            )
            row_count = len(df_count)

            results.append({
                "name": name,
                "columns": columns,
                "row_count": row_count,
            })
        except Exception as e:
            logger.warning(f"Sheet scan failed | sheet={name} | error={e}")
            results.append({"name": name, "columns": [], "row_count": 0})

    xl.close()
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


_TYPE_THRESHOLD = 0.95   # 95% 非空值成功转换才采纳（行业标准）
_SAMPLE_SIZE = 1000      # 采样行数（Polars 默认值）


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


def _coerce_object_columns(df) -> None:
    """瀑布式类型推断：numeric → datetime → str 兜底。

    只处理 dtype=object 的列（pandas 无法自动推断的混合类型列）。
    纯数值/日期列由 pandas 在 read_excel 时已正确推断，不进此分支。
    合并单元格产生的 Unnamed 空列直接删除。
    """
    import pandas as pd

    # 先删除合并单元格产生的全空 Unnamed 列（ERP 导出常见，1404 列中大量是空列）
    unnamed_empty = [
        c for c in df.columns
        if str(c).startswith("Unnamed:") and df[c].isna().all()
    ]
    if unnamed_empty:
        df.drop(columns=unnamed_empty, inplace=True)

    for col in df.columns:
        if df[col].dtype != object:
            continue

        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue

        sample = non_null.head(_SAMPLE_SIZE)

        # 1. 尝试数值（int 和 float 统一检测，pandas 自动推断精度）
        as_num = pd.to_numeric(sample, errors="coerce")
        if as_num.notna().sum() / len(sample) >= _TYPE_THRESHOLD:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            continue

        # 2. 尝试日期
        try:
            as_dt = pd.to_datetime(sample, errors="coerce", format="mixed")
            if as_dt.notna().sum() / len(sample) >= _TYPE_THRESHOLD:
                df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
                continue
        except Exception:
            pass

        # 3. 兜底：确保类型统一为 str（防止 PyArrow 崩溃）
        types = set(type(v).__name__ for v in sample.head(100))
        if len(types) > 1:
            df[col] = df[col].astype(str).replace({"nan": None})


def _convert_excel_to_parquet(
    excel_path: str, cache_path: str, sheet: str | None,
    src_mtime: float, src_size: int, snapshot_path: str,
) -> list[str]:
    """Excel → Parquet（同步，线程池执行）。返回所有 Sheet 名称。"""
    import pandas as pd

    start = time.monotonic()
    xl = pd.ExcelFile(excel_path, engine="calamine")
    sheet_names = xl.sheet_names

    target_sheet: str | int
    if sheet is None:
        target_sheet = 0
    elif sheet.isdigit():
        target_sheet = int(sheet)
    else:
        target_sheet = fuzzy_match_sheet(sheet, sheet_names)

    # 自动检测表头行（messytables 众数法 + csv.Sniffer 类型验证）
    df_raw = pd.read_excel(
        xl, sheet_name=target_sheet, engine="calamine",
        header=None, nrows=_HEADER_MAX_SCAN,
    )
    header_row = detect_header_row(df_raw.values.tolist())

    df = pd.read_excel(
        xl, sheet_name=target_sheet, engine="calamine",
        header=header_row,
    )
    xl.close()

    if header_row > 0:
        logger.info(
            f"Excel header auto-detected | src={Path(excel_path).name} "
            f"| header_row={header_row}"
        )

    # 瀑布式类型推断（行业标准：Spark/Polars/Airbyte 同模式）
    # 对 object 列按 int → float → datetime → str 顺序尝试转换
    # 阈值 95%：允许少量脏值变 NaN，但不误转文本列
    _coerce_object_columns(df)

    tmp_path = str(Path(cache_path).parent / f"_tmp_{uuid.uuid4().hex[:8]}.parquet")
    try:
        df.to_parquet(tmp_path, index=False, engine="pyarrow")
        os.rename(tmp_path, cache_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    Path(snapshot_path).write_text(f"{src_mtime},{src_size}")
    logger.info(
        f"Excel→Parquet cache | src={Path(excel_path).name} "
        f"sheet={sheet or 'default'} | rows={len(df):,} "
        f"| elapsed={time.monotonic() - start:.1f}s"
    )
    del df
    return sheet_names


def _convert_all_sheets_to_parquet(
    excel_path: str, cache_path: str,
    src_mtime: float, src_size: int, snapshot_path: str,
) -> list[str]:
    """所有同结构 Sheet 合并为单个 Parquet（加 _sheet 列标识来源）。"""
    import pandas as pd

    start = time.monotonic()
    xl = pd.ExcelFile(excel_path, engine="calamine")
    sheet_names = xl.sheet_names

    frames: list = []
    for name in sheet_names:
        try:
            df_raw = pd.read_excel(
                xl, sheet_name=name, engine="calamine",
                header=None, nrows=_HEADER_MAX_SCAN,
            )
            header_row = detect_header_row(df_raw.values.tolist())

            df = pd.read_excel(
                xl, sheet_name=name, engine="calamine",
                header=header_row,
            )
            if df.empty:
                continue
            _coerce_object_columns(df)
            df.insert(0, "_sheet", name)
            frames.append(df)
        except Exception as e:
            logger.warning(f"Sheet merge skip | sheet={name} | error={e}")
            continue

    xl.close()

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

    Path(snapshot_path).write_text(f"{src_mtime},{src_size}")
    logger.info(
        f"Excel→Parquet merge-all | src={Path(excel_path).name} "
        f"| sheets={len(frames)} | rows={len(merged):,} "
        f"| elapsed={time.monotonic() - start:.1f}s"
    )
    del merged
    return sheet_names
