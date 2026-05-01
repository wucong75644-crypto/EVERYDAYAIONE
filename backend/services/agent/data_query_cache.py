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


def _convert_excel_to_parquet(
    excel_path: str, cache_path: str, sheet: str | None,
    src_mtime: float, src_size: int, snapshot_path: str,
) -> list[str]:
    """Excel → Parquet（同步，线程池执行）。返回所有 Sheet 名称。"""
    import pandas as pd

    start = time.monotonic()
    xl = pd.ExcelFile(excel_path, engine="calamine")
    sheet_names = xl.sheet_names

    target_sheet: str | int = (
        int(sheet) if sheet is not None and sheet.isdigit()
        else sheet if sheet is not None
        else 0
    )
    df = pd.read_excel(xl, sheet_name=target_sheet, engine="calamine")
    xl.close()

    # 混合类型列（object 列里有 int+str）强制转 str，防止 PyArrow 崩溃
    # 纯数值/日期列保留原始类型，DuckDB 查询时不需要 CAST
    for col in df.columns:
        if df[col].dtype == object:
            non_null = df[col].dropna()
            if len(non_null) > 0:
                types = set(type(v).__name__ for v in non_null.head(100))
                if len(types) > 1:
                    df[col] = df[col].astype(str).replace({"nan": None})

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
