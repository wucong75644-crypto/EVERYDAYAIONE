"""data_query 文件检测与 Excel → Parquet 缓存模块"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
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


# V2.2 #L5 (CSV 兜底链): 优先中国主流 GBK（业务场景 70%+），其次 CJK 小众，最后 latin-1
# 设计权衡：
#   - 长文件依赖 chardet 准确检测主路，兜底链顺序不影响
#   - 短文件 chardet 不准时走兜底链 → 优先保 GBK（中国业务 90% 用 GBK / 用友/金蝶/快麦等 ERP 导出）
#   - 代价：极短 BIG5/SJIS/EUC-KR 文件可能被 gb18030 假成功（罕见且用户可立即看出乱码重新上传）
_CSV_FALLBACK_ENCODINGS = (
    "utf-8", "utf-8-sig",                # 最常见 + BOM
    "gb18030",                            # 中国主流（GBK/GB2312 超集）
    "big5", "shift_jis", "euc-kr",       # CJK 小众（出海场景）
    "latin-1",                            # 字节 1:1 映射，终极兜底永远不抛错
)


def _read_csv_smart(csv_path: str, sep: str):
    """V2.2: CSV 多编码兜底读取。

    流程：
      1. chardet 检测（detect_encoding 阈值 0.7，准则用，否则兜底 utf-8）
      2. 主路：用检测到的编码 read_csv
      3. 兜底链：按"严格 → 宽松"顺序尝试（避开 gb18030 对 BIG5/SJIS/EUC-KR 假成功）
      4. 最后 latin-1（字节 1:1 映射，永远不抛 UnicodeDecodeError）

    覆盖：UTF-8 / GBK / BIG5 / Shift_JIS / EUC-KR / UTF-8-SIG / Latin-1
    """
    import pandas as pd
    detected = detect_encoding(csv_path).lower()

    # 主路
    try:
        return pd.read_csv(csv_path, sep=sep, encoding=detected)
    except UnicodeDecodeError:
        pass
    except Exception:
        # 其他错误（sep 不对 / 文件损坏）直接抛
        raise

    # 兜底链
    for enc in _CSV_FALLBACK_ENCODINGS:
        if enc == detected:
            continue
        try:
            return pd.read_csv(csv_path, sep=sep, encoding=enc)
        except UnicodeDecodeError:
            continue
    # 永远不应该到这里（latin-1 不会抛 UnicodeDecodeError）
    # 但兜底兜底：用 latin-1 + errors=replace
    return pd.read_csv(csv_path, sep=sep, encoding="latin-1")


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

# V2 缓存 schema 版本：升级时改这个数字，所有用户旧缓存自动失效（强制重算）
# v2.0 → v2.1：PathB calamine 改造 + PathA/B null_ratio bug 修复 + ""列识别（2026-06-03）
# v2.1 → v2.2：cache_key 改用内容指纹（zip CRC / csv md5），snapshot 改存 fingerprint（2026-06-04）
_CACHE_SCHEMA_VERSION = "v2.2"

# V2.2 #19: ensure_parquet_cache 总超时（兜底防挂死，业界通用 10 分钟）
# 覆盖：AI 失败链 195s（3×65s 含网络抖动）+ scan 大文件 90s + 转换 60s + 写盘/meta 余量
# 正常场景 90% 在 60s 内完成；触发 600s 意味系统级异常，报错合理
_ENSURE_CACHE_TIMEOUT = 600.0  # 10 分钟兜底

# V2.2 #12: 失败软熔断（同 fingerprint 文件短期内不重跑 AI）
_FAILURE_CACHE_TTL = 300.0  # 5 分钟
# fingerprint → (failure_timestamp, error_category, user_message)
_failure_cache: dict[str, tuple[float, str, str]] = {}
_FAILURE_CACHE_MAX = 1000  # 上限

# V2.2 #20: staging Parquet 自动清理参数
_STAGING_TTL_SECONDS = 30 * 24 * 3600   # 30 天未访问 → 清理
_STAGING_MAX_FILES = 5000               # staging 单目录文件数上限（含 .meta/.snapshot 等 sidecar）
_STAGING_CLEANUP_INTERVAL = 3600        # 同目录上次清理 < 1h 跳过（lazy 触发）
_last_cleanup_ts: dict[str, float] = {}  # staging_dir → 上次清理时间戳


def _check_failure_cache(fingerprint: str):
    """V2.2 #12: 看 fingerprint 是否在失败软熔断窗口内。

    Returns (error_category, user_message) 或 None
    """
    now = time.monotonic()
    entry = _failure_cache.get(fingerprint)
    if entry is None:
        return None
    failed_at, category, user_msg = entry
    if now - failed_at > _FAILURE_CACHE_TTL:
        # 过期，自动删除
        _failure_cache.pop(fingerprint, None)
        return None
    return (category, user_msg)


def _record_failure(fingerprint: str, category: str, user_msg: str) -> None:
    """V2.2 #12: 记录 fingerprint 失败（软熔断）。"""
    now = time.monotonic()
    if len(_failure_cache) >= _FAILURE_CACHE_MAX:
        # 简单 FIFO 淘汰最旧
        oldest = min(_failure_cache.items(), key=lambda kv: kv[1][0])[0]
        _failure_cache.pop(oldest, None)
    _failure_cache[fingerprint] = (now, category, user_msg)


def _compute_schema_fingerprint(evidence) -> str:
    """V2.2 #16: 计算 EvidencePool 的结构指纹（不含具体数据值）。

    用于跨文件复用 AIDecision：同模板的月度报表（仅数据行不同，列结构相同）
    fingerprint 不会命中（文件变了），但 schema_fingerprint 相同 → 可复用 AIDecision，
    省一次 AI 调用。

    指纹内容：path_type + 每列 (col_letter, raw_header, classified_dist key 集合)
    """
    parts = [f"pt:{evidence.path_type}", f"cols:{evidence.total_cols}"]
    for c in evidence.columns:
        # 用 classified_dist 的 key 集合（数据类型分布），不用具体计数（避免行数差异）
        dist_keys = sorted(c.classified_dist.keys()) if c.classified_dist else []
        parts.append(f"{c.col_letter}:{c.raw_header}:{','.join(dist_keys)}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


def _try_reuse_decision(staging_dir: str, schema_fp: str):
    """V2.2 #16: 从 staging 同目录已有 meta.json 中查找 schema_fingerprint 匹配的 AIDecision。

    Returns AIDecision dict 或 None。
    复用条件：找到任意一份 meta.json 中 schema_fingerprint == schema_fp 且 ai_decision 非空。
    """
    try:
        staging = Path(staging_dir)
        if not staging.exists():
            return None
        # 扫所有 meta.json（限 100 个最近的，避免大目录扫死）
        meta_files = sorted(
            staging.glob("_cache_*.meta.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )[:100]
        import json as _json
        for mf in meta_files:
            try:
                meta_data = _json.loads(mf.read_text(encoding="utf-8"))
                if meta_data.get("schema_fingerprint") != schema_fp:
                    continue
                decision = meta_data.get("ai_decision")
                if decision:
                    logger.info(
                        f"schema fingerprint hit | source={mf.name} | "
                        f"fp={schema_fp}"
                    )
                    return decision
            except (OSError, ValueError):
                continue
    except Exception as e:
        logger.warning(f"schema fingerprint lookup failed | {e}")
    return None


def _maybe_cleanup_staging(staging_dir: str) -> None:
    """lazy LRU 清理 staging 目录：超过 TTL 或文件数过多时触发。

    触发频率：单目录最多每 1h 一次（避免每个请求都扫盘）。
    清理对象：超过 _STAGING_TTL_SECONDS 未访问的 .parquet/.snapshot/.meta.json/.cleaning.json/.lock
    """
    now = time.monotonic()
    last = _last_cleanup_ts.get(staging_dir, 0)
    if now - last < _STAGING_CLEANUP_INTERVAL:
        return
    _last_cleanup_ts[staging_dir] = now

    try:
        staging = Path(staging_dir)
        if not staging.exists():
            return

        # 收集所有 _cache_*.* 文件（按访问时间排序）
        cache_files = list(staging.glob("_cache_*"))
        if not cache_files:
            return

        cutoff_ts = time.time() - _STAGING_TTL_SECONDS
        n_deleted = 0
        # ① TTL 清理
        for p in cache_files:
            try:
                # atime 在很多 FS 不更新，回退用 mtime
                st = p.stat()
                last_access = max(st.st_atime, st.st_mtime)
                if last_access < cutoff_ts:
                    p.unlink(missing_ok=True)
                    n_deleted += 1
            except OSError:
                continue

        # ② 数量上限：超出按 mtime 升序删最旧
        cache_files = list(staging.glob("_cache_*"))
        if len(cache_files) > _STAGING_MAX_FILES:
            cache_files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
            n_extra = len(cache_files) - _STAGING_MAX_FILES
            for p in cache_files[:n_extra]:
                try:
                    p.unlink(missing_ok=True)
                    n_deleted += 1
                except OSError:
                    continue

        if n_deleted:
            logger.info(
                f"staging cleanup | dir={staging_dir} | deleted={n_deleted} "
                f"| remaining={len(list(staging.glob('_cache_*')))}"
            )
    except Exception as e:
        logger.warning(f"staging cleanup failed | dir={staging_dir} | err={e}")


# V2.2 #38: zip bomb 防御参数
_XLSX_MAX_COMPRESSION_RATIO = 100.0   # 压缩比上限（10MB 解压 / 100KB 压缩 = 100x，正常 xlsx < 30x）
_XLSX_MAX_ZIP_ENTRIES = 1000          # zip entry 数量上限（正常 xlsx 30-100 个 sheet → entries < 200）
_XLSX_MAGIC = b"PK\x03\x04"


def validate_xlsx_safety(abs_path: str) -> None:
    """V2.2 #38: xlsx 安全校验，防 zip bomb + 伪装文件。

    校验项：
      1. magic bytes（首 4 字节 = PK\\x03\\x04）— 防 PDF/exe 伪装
      2. zip entry 数量 ≤ _XLSX_MAX_ZIP_ENTRIES — 防 entry 炸弹
      3. 总解压大小 / 压缩文件大小 ≤ _XLSX_MAX_COMPRESSION_RATIO — 防 zip bomb

    Raises:
        FileAnalyzeError(error_category="file_corrupted" or "file_too_large")
    """
    try:
        # ① magic bytes
        with open(abs_path, "rb") as f:
            magic = f.read(4)
    except OSError as e:
        from services.agent.file_ai_judge import FileAnalyzeError
        raise FileAnalyzeError(
            error_category="file_corrupted",
            error_summary=f"无法读取文件: {e}",
            retryable=False,
            suggested_action="ask_user",
            user_message=f"文件「{Path(abs_path).name}」无法读取，请检查权限或重新上传。",
            file_path=abs_path,
            file_name=Path(abs_path).name,
            file_size_mb=0.0,
            total_rows=0,
            path_type="?",
        )

    if magic != _XLSX_MAGIC:
        from services.agent.file_ai_judge import FileAnalyzeError
        raise FileAnalyzeError(
            error_category="file_corrupted",
            error_summary=(
                f"文件 {Path(abs_path).name} 不是合法 xlsx（首 4 字节 {magic!r}）"
            ),
            retryable=False,
            suggested_action="ask_user",
            user_message=(
                f"文件「{Path(abs_path).name}」扩展名是 xlsx 但实际不是 Excel 格式"
                f"（可能是改名的 PDF/csv/其他），请重新上传真正的 xlsx 文件。"
            ),
            file_path=abs_path,
            file_name=Path(abs_path).name,
            file_size_mb=round(os.path.getsize(abs_path) / 1024 / 1024, 2),
            total_rows=0,
            path_type="?",
        )

    # ② + ③ zip 结构校验
    file_size = os.path.getsize(abs_path)
    try:
        with zipfile.ZipFile(abs_path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        from services.agent.file_ai_judge import FileAnalyzeError
        raise FileAnalyzeError(
            error_category="file_corrupted",
            error_summary=f"xlsx zip 容器损坏: {e}",
            retryable=False,
            suggested_action="ask_user",
            user_message=(
                f"文件「{Path(abs_path).name}」结构损坏，"
                f"建议用 Excel 打开后另存为新 xlsx 文件再上传。"
            ),
            file_path=abs_path,
            file_name=Path(abs_path).name,
            file_size_mb=round(file_size / 1024 / 1024, 2),
            total_rows=0,
            path_type="?",
        ) from e

    if len(infos) > _XLSX_MAX_ZIP_ENTRIES:
        from services.agent.file_ai_judge import FileAnalyzeError
        raise FileAnalyzeError(
            error_category="file_too_large",
            error_summary=(
                f"xlsx zip entry 数量 {len(infos)} > {_XLSX_MAX_ZIP_ENTRIES}（疑似炸弹）"
            ),
            retryable=False,
            suggested_action="ask_user",
            user_message=(
                f"文件「{Path(abs_path).name}」结构异常（含 {len(infos)} 个内部条目，"
                f"远超 Excel 正常值），可能是恶意文件，已拒绝处理。"
            ),
            file_path=abs_path,
            file_name=Path(abs_path).name,
            file_size_mb=round(file_size / 1024 / 1024, 2),
            total_rows=0,
            path_type="?",
        )

    total_uncompressed = sum(i.file_size for i in infos)
    ratio = (total_uncompressed / file_size) if file_size > 0 else 0
    if ratio > _XLSX_MAX_COMPRESSION_RATIO:
        from services.agent.file_ai_judge import FileAnalyzeError
        raise FileAnalyzeError(
            error_category="file_too_large",
            error_summary=(
                f"xlsx 压缩比 {ratio:.1f}x > {_XLSX_MAX_COMPRESSION_RATIO}x"
                f"（{file_size}B → {total_uncompressed}B，疑似 zip bomb）"
            ),
            retryable=False,
            suggested_action="ask_user",
            user_message=(
                f"文件「{Path(abs_path).name}」压缩比异常（{ratio:.0f} 倍），"
                f"解压后大小 {total_uncompressed//1024//1024}MB 远超原文件 "
                f"{file_size//1024//1024}MB，疑似恶意文件，已拒绝处理。"
            ),
            file_path=abs_path,
            file_name=Path(abs_path).name,
            file_size_mb=round(file_size / 1024 / 1024, 2),
            total_rows=0,
            path_type="?",
        )


def _compute_file_fingerprint(abs_path: str) -> str:
    """计算文件内容指纹（替代 mtime+size 弱校验）。

    xlsx/xls 是 zip 容器 → 用所有 entry 的 (filename, CRC32, size) 拼接 md5
    csv/tsv/其他 → 首 1MB md5

    返回 12 位 hex（48 bit），生日悖论下 ~1M 文件不会碰撞。
    """
    ext = Path(abs_path).suffix.lower()
    try:
        if ext in (".xlsx", ".xls"):
            try:
                with zipfile.ZipFile(abs_path) as z:
                    sig = "|".join(
                        f"{i.filename}:{i.CRC}:{i.file_size}"
                        for i in z.infolist()
                    )
                    return hashlib.md5(sig.encode()).hexdigest()[:12]
            except zipfile.BadZipFile:
                # 不是 zip（损坏或假 xlsx）→ 用首 64KB md5 兜底
                pass
        # csv/tsv/兜底：首 1MB md5
        with open(abs_path, "rb") as f:
            return hashlib.md5(f.read(1024 * 1024)).hexdigest()[:12]
    except OSError as e:
        logger.warning(f"fingerprint compute failed | path={abs_path} | err={e}")
        # 完全失败兜底用路径 hash（退化为 v2.1 行为）
        return hashlib.md5(abs_path.encode()).hexdigest()[:12]
_convert_locks: dict[str, asyncio.Lock] = {}


class _AIDecisionAdapter:
    """适配层：把 AIDecision 包装成与旧 PrescanResult 兼容的对象。

    现有 _convert_excel_to_parquet / convert_multi_region 内部仍读
    ai_decision.header_rows / .special_rows / .column_mapping / 等字段，
    通过此适配避免改动 360 行的转换函数。
    """

    def __init__(self, decision):
        self._d = decision
        # V2.2 #11: 从 AIDecision 读真实 confidence（兜底 high）
        self.confidence = getattr(decision, "confidence", None) or "high"
        self.header_rows = [decision.header_row] if decision.header_row else []
        self.data_start_row = decision.data_start_row
        self.reasoning = decision.overall_summary
        # column_mapping: letter → business_name
        self.column_mapping = {
            cs.letter: cs.business_name
            for cs in decision.column_semantics
            if cs.business_name
        }
        # special_rows: AIDecision → prescan dict 格式
        self.special_rows = {
            "summary": list(decision.summary_rows),
            "unit": list(decision.unit_rows),
            "note": list(decision.note_rows),
        }
        # regions: 只在路径 C 才有
        self.regions = [
            {
                "start_row": int(r.range_str.split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
                             if r.range_str else 0,
                "end_row": 0,
                "description": r.role,
            }
            for r in decision.regions
        ]
        # anomalies: 把 data_quality_notes 转过来（兼容 _build_sample 的 boundary）
        self.anomalies = [
            {
                "column": n.affected_cols[0] if n.affected_cols else "",
                "type": n.severity,
                "severity": n.severity,
                "description": n.note,
                "sample_rows": list(n.affected_rows),
            }
            for n in decision.data_quality_notes
        ]
        self.raw_response = ""

    def to_dict(self) -> dict:
        """兼容旧 PrescanResult.to_dict() — meta.prescan 字段（V1 兼容）。"""
        return {
            "confidence": self.confidence,
            "header_rows": self.header_rows,
            "data_start_row": self.data_start_row,
            "reasoning": self.reasoning,
            "column_mapping": self.column_mapping,
            "special_rows": self.special_rows,
            "regions": self.regions,
            "anomalies": self.anomalies,
        }


async def ensure_parquet_cache(
    excel_path: str,
    sheet: str | None,
    staging_dir: str,
) -> tuple[str, list[str] | None]:
    """确保 Excel 文件有对应的 Parquet 缓存（V2 编排）。

    V2 流程：
      1. snapshot 缓存检查
      2. probe + make_scanner → EvidencePool
      3. adjudicate(evidence) → AIDecision（含失败链）
      4. CleaningStrategy.from_decision(decision) → CleaningStrategy
      5. 线程池中调 _convert_excel_to_parquet（传 adapter + strategy）
      6. 渲染 XML 写入 meta.xml_view

    Returns:
        (parquet_cache_path, sheet_names)

    Raises:
        FileAnalyzeError: AI 三次裁决全部失败
        ValueError: 空文件
    """
    # v2.2: 缓存 key 改用文件内容指纹（zip CRC sum / csv 首 1MB md5）
    # 优势：1) 文件重命名不重算 2) 同 size 改内容也能检测到
    fingerprint = _compute_file_fingerprint(excel_path)
    sheet_label = sheet or "sheet0"
    safe_sheet = re.sub(r'[^\w\-]', '_', str(sheet_label))
    cache_name = (
        f"_cache_{_CACHE_SCHEMA_VERSION}_{fingerprint}_"
        f"{safe_sheet}_{Path(excel_path).stem}.parquet"
    )

    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    cache_path = staging / cache_name
    snapshot_path = cache_path.with_suffix(".snapshot")

    # 保留 mtime/size 供 user_message 显示和兼容旧转换函数
    stat = os.stat(excel_path)
    src_mtime, src_size = stat.st_mtime, stat.st_size

    # V2.2 #20: lazy staging 清理（每目录最多 1h 一次）
    _maybe_cleanup_staging(staging_dir)

    if _snapshot_matches_fp(cache_path, snapshot_path, fingerprint):
        return str(cache_path), None

    # V2.2 #12: 软熔断 — 同 fingerprint 文件 5 分钟内不重跑（避免 AI 反复失败烧钱）
    failure = _check_failure_cache(fingerprint)
    if failure is not None:
        category, user_msg = failure
        from services.agent.file_ai_judge import FileAnalyzeError
        raise FileAnalyzeError(
            error_category=category,
            error_summary=f"软熔断：{Path(excel_path).name} 短期内已失败",
            retryable=False,
            suggested_action="ask_user",
            user_message=(
                f"{user_msg}\n\n"
                f"（同文件 {int(_FAILURE_CACHE_TTL/60)} 分钟内已尝试失败，暂停重试以避免重复消耗）"
            ),
            file_path=excel_path,
            file_name=Path(excel_path).name,
            file_size_mb=round(src_size / 1024 / 1024, 2),
            total_rows=0,
            path_type="?",
        )

    # 新锁协议（修 #1+#2+#5）：refcount 保护 + 进程间 fcntl 文件锁
    lock_key = f"{excel_path}:{sheet_label}"
    lock_file_path = str(cache_path) + ".lock"

    async def _do_convert():
        """临界区内核心逻辑（双锁内执行）。"""
        # 双重检查
        if _snapshot_matches_fp(cache_path, snapshot_path, fingerprint):
            return str(cache_path), None

        from services.agent.file_scanners import make_scanner
        from services.agent.file_ai_judge import adjudicate, FileAnalyzeError
        from services.agent.file_cleaning_strategy import CleaningStrategy

        loop = asyncio.get_running_loop()

        # Smell 3 观测：记录 file_analyze 各阶段耗时
        _fn = Path(excel_path).name
        _stage_t0 = time.monotonic()

        # 代码扫描在线程池（IO/CPU 密集）
        try:
            scanner = await loop.run_in_executor(None, make_scanner, excel_path, None)
            evidence = await loop.run_in_executor(None, scanner.scan)
        except FileAnalyzeError:
            raise
        except Exception as e:
            from services.agent.file_ai_judge import (
                FileAnalyzeError as _FAE, AnalyzeAttemptLog,
            )
            raise _FAE(
                error_category="file_corrupted",
                error_summary=f"文件扫描失败：{e}",
                retryable=False,
                suggested_action="ask_user",
                user_message=(
                    f"文件「{Path(excel_path).name}」可能已损坏或格式异常。\n"
                    "建议用 Excel 打开后另存为新 xlsx 文件再上传。"
                ),
                file_path=excel_path,
                file_name=Path(excel_path).name,
                file_size_mb=round(src_size / 1024 / 1024, 2),
                attempts=[],
            ) from e

        _scan_elapsed = time.monotonic() - _stage_t0
        logger.info(
            f"file_analyze stage | scan_done | src={_fn} "
            f"| path={evidence.path_type} | rows={evidence.total_rows} "
            f"| cols={evidence.total_cols} | elapsed={_scan_elapsed:.1f}s"
        )

        # V2.2 #16: 尝试用 schema 指纹复用历史 AIDecision（同模板月度报表）
        _stage_t = time.monotonic()
        schema_fp = _compute_schema_fingerprint(evidence)
        cached_decision_dict = _try_reuse_decision(staging_dir, schema_fp)
        if cached_decision_dict:
            # 重建 AIDecision 对象（dict → dataclass）
            try:
                from services.agent.file_ai_decision import (
                    AIDecision, ColumnSemantic, MergedCellAction,
                    MixedTypeAction, EmptyRowDecision, RegionDecision,
                    SheetDecision, DataQualityNote, validate_decision,
                )
                def _rebuild():
                    d = AIDecision(
                        header_row=cached_decision_dict.get("header_row", 1),
                        data_start_row=cached_decision_dict.get("data_start_row", 2),
                        header_type=cached_decision_dict.get("header_type", "single"),
                        header_note=cached_decision_dict.get("header_note", ""),
                        column_semantics=[
                            ColumnSemantic(**c) for c in cached_decision_dict.get("column_semantics", [])
                        ],
                        summary_rows=cached_decision_dict.get("summary_rows", []),
                        unit_rows=cached_decision_dict.get("unit_rows", []),
                        note_rows=cached_decision_dict.get("note_rows", []),
                        merged_cell_actions=[
                            MergedCellAction(**m) for m in cached_decision_dict.get("merged_cell_actions", [])
                        ],
                        mixed_type_handling=[
                            MixedTypeAction(**m) for m in cached_decision_dict.get("mixed_type_handling", [])
                        ],
                        preserve_empty_rows=[
                            EmptyRowDecision(**e) for e in cached_decision_dict.get("preserve_empty_rows", [])
                        ],
                        regions=[
                            RegionDecision(**r) for r in cached_decision_dict.get("regions", [])
                        ],
                        sheets=[
                            SheetDecision(**s) for s in cached_decision_dict.get("sheets", [])
                        ],
                        data_quality_notes=[
                            DataQualityNote(**n) for n in cached_decision_dict.get("data_quality_notes", [])
                        ],
                        overall_summary=cached_decision_dict.get("overall_summary", ""),
                        model_used="reused_from_schema_fp",
                        attempt_count=0,
                        elapsed_ms=0,
                    )
                    if not validate_decision(d):
                        return d
                    return None
                decision = _rebuild()
            except Exception as e:
                logger.warning(f"reuse decision rebuild failed | {e}")
                decision = None
        else:
            decision = None

        # Smell 2 + 3：AI 裁决前后埋点
        _fp_elapsed = time.monotonic() - _stage_t
        _ai_reused = decision is not None  # 是否走的缓存复用
        logger.info(
            f"file_analyze stage | fingerprint_check_done | src={_fn} "
            f"| reused={_ai_reused} | elapsed={_fp_elapsed:.1f}s"
        )

        # 缓存未命中或重建失败 → 走完整 AI 裁决
        if decision is None:
            _ai_t0 = time.monotonic()
            logger.info(
                f"AI adjudicate start | src={_fn} "
                f"| evidence_cols={len(evidence.columns)} "
                f"| evidence_rows={evidence.total_rows}"
            )
            try:
                decision = await adjudicate(evidence)
            except Exception as ai_err:
                # V2.2 #12: AI 失败 → 记录软熔断（5 分钟内不再重试同 fingerprint）
                _cat = getattr(ai_err, "error_category", "internal_error")
                _msg = getattr(ai_err, "user_message", str(ai_err))
                _record_failure(fingerprint, _cat, _msg or _cat)
                logger.info(
                    f"AI adjudicate end | src={_fn} | status=failed "
                    f"| elapsed={time.monotonic()-_ai_t0:.1f}s | category={_cat}"
                )
                raise
            logger.info(
                f"AI adjudicate end | src={_fn} | status=ok "
                f"| model={getattr(decision, 'model_used', 'N/A')} "
                f"| attempts={getattr(decision, 'attempt_count', 'N/A')} "
                f"| elapsed={time.monotonic()-_ai_t0:.1f}s"
            )

        _stage_t = time.monotonic()
        strategy = CleaningStrategy.from_decision(decision)
        adapter = _AIDecisionAdapter(decision)

        # 线程池中执行 Excel → Parquet 转换
        if evidence.path_type == "D":
            # 路径 D：传 decision + strategy（按 sheets[i].role 过滤 meta/aggregated/skip）
            sheet_names = await loop.run_in_executor(
                None, _convert_all_sheets_to_parquet,
                excel_path, str(cache_path), src_mtime, src_size,
                str(snapshot_path),
                decision, strategy,
            )
        else:
            sheet_names = await loop.run_in_executor(
                None, _convert_excel_to_parquet,
                excel_path, str(cache_path), sheet, src_mtime, src_size,
                str(snapshot_path), adapter, strategy,
            )

        # Smell 3：clean_excel + Parquet 写入合并日志（这两步在 _convert_* 里串行做）
        logger.info(
            f"file_analyze stage | clean_parquet_done | src={_fn} "
            f"| elapsed={time.monotonic()-_stage_t:.1f}s"
        )
        _stage_t = time.monotonic()

        # 空文件检测
        if not cache_path.exists():
            raise ValueError(
                f"文件 {Path(excel_path).name} 内容为空，没有可读取的数据。"
                f"请检查文件是否正确或选择其他 Sheet。"
            )

        # V2: 补充 ai_decision / cleaning_strategy / xml_view 到 meta.json
        # V2.2 #16: 同时存 schema_fingerprint
        await loop.run_in_executor(
            None, _enrich_meta_v2,
            str(cache_path), excel_path, decision, strategy, staging_dir,
            schema_fp,
        )

        logger.info(
            f"file_analyze stage | meta_write_done | src={_fn} "
            f"| elapsed={time.monotonic()-_stage_t:.1f}s "
            f"| total_elapsed={time.monotonic()-_stage_t0:.1f}s"
        )

        return str(cache_path), sheet_names

    entry = await _acquire_convert_lock(lock_key)
    try:
        async with entry.lock:                          # 进程内互斥（修 #1）
            with _FileLock(lock_file_path):             # 进程间互斥（修 #5）
                return await _do_convert()
    finally:
        _release_convert_lock(lock_key)


async def ensure_parquet_cache_csv(
    csv_path: str, staging_dir: str,
) -> tuple[str, None]:
    """CSV/TSV → Parquet 直接转换（不走 make_scanner，因为 fastexcel 不支持 csv）。

    流程：
      1. 检测编码（utf-8 / gbk / gb2312 等）
      2. pandas.read_csv 加载
      3. 写 Parquet + snapshot + 简化 meta.json
      4. 不调 AI（CSV 结构简单，列名第一行清晰）

    返回 (parquet_path, None) — sheet_names 始终为 None
    """
    import pandas as pd

    # v2.2: cache_key 用内容指纹（同 ensure_parquet_cache 协议）
    fingerprint = _compute_file_fingerprint(csv_path)
    cache_name = (
        f"_cache_{_CACHE_SCHEMA_VERSION}_{fingerprint}_csv_"
        f"{Path(csv_path).stem}.parquet"
    )
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    cache_path = staging / cache_name
    snapshot_path = cache_path.with_suffix(".snapshot")

    # V2.2 #20: lazy staging 清理
    _maybe_cleanup_staging(staging_dir)

    if _snapshot_matches_fp(cache_path, snapshot_path, fingerprint):
        return str(cache_path), None

    # 新锁协议（与 ensure_parquet_cache 一致）
    lock_key = f"{csv_path}:csv"
    lock_file_path = str(cache_path) + ".lock"

    def _do_convert():
        sep = "\t" if csv_path.lower().endswith(".tsv") else ","
        # V2.2: 多级编码兜底链（UTF-8/GBK/BIG5/SJIS/EUC-KR/Latin-1）
        df = _read_csv_smart(csv_path, sep)
        if df.empty:
            raise ValueError(f"CSV 文件为空: {Path(csv_path).name}")
        tmp_path = str(cache_path.parent / f"_tmp_{uuid.uuid4().hex[:8]}.parquet")
        try:
            df.to_parquet(tmp_path, index=False, engine="pyarrow")
            os.rename(tmp_path, cache_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        # v2.2: snapshot 原子写 + 内容指纹
        _write_snapshot_atomic(snapshot_path, _compute_file_fingerprint(csv_path))
        # 生成简化 meta.json
        from services.agent.file_meta import generate_file_meta, write_file_meta
        from services.agent.excel_cleaner import CleaningReport
        report = CleaningReport(original_shape=(len(df), len(df.columns)))
        report.final_shape = report.original_shape
        report.header_row = 0
        report.data_start_row = 2
        file_meta = generate_file_meta(
            df, report,
            source_file=csv_path,
            sheet_count=1,
            formulas=[],
            formula_skip_reason="csv_no_formulas",
        )
        write_file_meta(str(cache_path), file_meta)
        return len(df)

    entry = await _acquire_convert_lock(lock_key)
    try:
        async with entry.lock:
            with _FileLock(lock_file_path):
                if _snapshot_matches_fp(cache_path, snapshot_path, fingerprint):
                    return str(cache_path), None
                loop = asyncio.get_running_loop()
                rows = await loop.run_in_executor(None, _do_convert)
                logger.info(
                    f"CSV→Parquet | src={Path(csv_path).name} | rows={rows:,}"
                )
                return str(cache_path), None
    finally:
        _release_convert_lock(lock_key)


def _enrich_meta_v2(
    cache_path: str, excel_path: str, decision, strategy, staging_dir: str,
    schema_fingerprint: str = "",
) -> None:
    """V2: 在 Parquet 生成后追加 ai_decision / cleaning_strategy / related_files /
    xml_view 到 meta.json。

    V2.2 #16: 同时存 schema_fingerprint，供后续同结构文件复用 AIDecision。
    """
    from dataclasses import asdict
    from services.agent.file_meta import read_file_meta, write_file_meta
    from services.agent.file_xml_renderer import render_xml
    from services.agent.session_files import read_session_files

    meta = read_file_meta(cache_path)
    if meta is None:
        logger.debug(f"V2 enrich skipped: meta not found for {cache_path}")
        return

    meta.ai_decision = asdict(decision)
    meta.cleaning_strategy = asdict(strategy)
    # V2.2 #16: 持久化 schema 指纹（下次同模板文件可复用）
    if schema_fingerprint:
        meta.schema_fingerprint = schema_fingerprint

    # related_files 从 session_files.json 派生
    related: list[dict] = []
    try:
        sf_data = read_session_files(staging_dir)
        relations = sf_data.get("potential_relations", []) or []
        current_stem = Path(cache_path).stem
        for rel in relations:
            files_in_rel = rel.get("files", []) or []
            if current_stem in files_in_rel:
                other = next((f for f in files_in_rel if f != current_stem), "")
                related.append({
                    "type": rel.get("relation_type", ""),
                    "confidence": rel.get("confidence", 0),
                    "other_file": other,
                    "common_columns": rel.get("common_columns", []),
                    "hint": rel.get("hint", ""),
                })
    except Exception as e:
        logger.debug(f"V2 related_files skipped: {e}")
    meta.related_files = related

    try:
        meta.xml_view = render_xml(
            meta, parquet_path=cache_path, original_path=excel_path,
            related_files=related,
        )
    except Exception as e:
        logger.warning(f"V2 XML render failed: {e}")
        meta.xml_view = ""

    write_file_meta(cache_path, meta)


def _snapshot_matches(
    cache_path: Path, snapshot_path: Path,
    src_mtime: float, src_size: int,
) -> bool:
    """旧版（v2.1 及以前）mtime+size 校验。保留供 ensure_parquet_cache_csv 等过渡代码使用。"""
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


def _snapshot_matches_fp(
    cache_path: Path, snapshot_path: Path, fingerprint: str,
) -> bool:
    """v2.2 内容指纹校验。snapshot 文件内容 = fingerprint 字符串。"""
    if not cache_path.exists() or not snapshot_path.exists():
        return False
    try:
        return snapshot_path.read_text().strip() == fingerprint
    except OSError:
        return False


def _write_snapshot_atomic(snapshot_path, content: str) -> None:
    """tmp + rename 原子写入，避免中途崩溃留下半截 snapshot。"""
    snap_str = str(snapshot_path)
    tmp_path = snap_str + ".tmp"
    Path(tmp_path).write_text(content)
    os.rename(tmp_path, snap_str)


# ── 文件级跨进程锁（fcntl）+ 进程内 refcount 协议 ──

class _LockEntry:
    """锁池条目：含 asyncio.Lock + 引用计数（防止淘汰正持有的锁）。"""
    __slots__ = ("lock", "refcount")
    def __init__(self):
        self.lock = asyncio.Lock()
        self.refcount = 0


async def _acquire_convert_lock(key: str) -> _LockEntry:
    """获取/新建锁条目，refcount+=1。

    淘汰策略：满了优先删除 refcount=0 的最旧条目；全在用则扩容（理论极限罕见）。
    """
    if key not in _convert_locks:
        if len(_convert_locks) >= _MAX_LOCKS:
            # 只淘汰未被持有的锁
            for k in list(_convert_locks):
                if _convert_locks[k].refcount == 0:
                    del _convert_locks[k]
                    break
            # 找不到可淘汰的就让池暂时超额（极罕见，不阻塞业务）
        _convert_locks[key] = _LockEntry()
    entry = _convert_locks[key]
    entry.refcount += 1
    return entry


def _release_convert_lock(key: str) -> None:
    """refcount-=1。降到 0 不立即删（等淘汰时统一处理）。"""
    entry = _convert_locks.get(key)
    if entry is not None:
        entry.refcount -= 1


class _FileLock:
    """fcntl.flock 阻塞独占锁（POSIX）— 跨进程互斥。

    用法：
        with _FileLock(lock_path):
            ...  # 临界区
    """
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._fd = None

    def __enter__(self):
        # 上层确保 lock_path 父目录存在
        self._fd = open(self.lock_path, "w")
        try:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except ImportError:
            # Windows 退化为无效锁（生产环境是 Linux ECS，不会走到这里）
            logger.warning("fcntl unavailable → file lock disabled")
        return self

    def __exit__(self, *_):
        try:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except ImportError:
            pass
        finally:
            try:
                self._fd.close()
            except OSError:
                pass

_HEADER_MAX_SCAN = 20   # 扫描前 N 行寻找表头

# 值内容模式识别（替代 isinstance，fastexcel 全返 str）
_RE_NUMERIC = re.compile(r'^-?[\d,]+\.?\d*(e[+-]?\d+)?$', re.IGNORECASE)
_RE_DATE = re.compile(
    r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}'
    r'(\s+\d{1,2}:\d{1,2}(:\d{1,2})?)?$'
)
_RE_LONG_ID = re.compile(r'^\d{10,}$')


def _classify_cell(value) -> str:
    """判断单元格内容的实际类型（不依赖 Python 类型，用值内容匹配）。"""
    if value is None:
        return "empty"
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan", "<na>"):
        return "empty"
    if _RE_LONG_ID.match(s):
        return "long_id"
    if _RE_DATE.match(s):
        return "date"
    s_clean = s.lstrip("¥$￥").rstrip("%").replace(",", "")
    if _RE_NUMERIC.match(s_clean):
        return "numeric"
    return "text"


def _is_data_row(row_values, threshold: float = 0.3) -> bool:
    """判断一行是否是数据行（含数字/日期/长ID 占比 ≥ threshold）。"""
    classes = [_classify_cell(v) for v in row_values]
    non_empty = [c for c in classes if c != "empty"]
    if not non_empty:
        return False
    data_n = sum(1 for c in non_empty if c in ("numeric", "date", "long_id"))
    return data_n / len(non_empty) >= threshold


def _looks_like_header(row_values) -> bool:
    """判断一行是否像表头（文本占比高 + 值各不相同 + 非空数够多）。"""
    classes = [_classify_cell(v) for v in row_values]
    non_empty = [c for c in classes if c != "empty"]
    if len(non_empty) < 2:
        return False
    text_ratio = sum(1 for c in non_empty if c == "text") / len(non_empty)
    if text_ratio < 0.7:
        return False
    # 排除合并单元格的标题行（所有值相同）
    vals = [str(v).strip() for v in row_values if v is not None and str(v).strip()]
    if len(set(vals)) <= 1:
        return False
    return True


def detect_header_row(rows: list[list]) -> int:
    """自动检测 Excel 表头行号（值内容模式匹配 + 下一行验证）。

    算法：找第一个"像表头 + 下一行像数据"的行。
    用值内容（正则匹配数字/日期/长ID）替代 isinstance 判断，
    兼容 fastexcel header_row=None 全返 str 的场景。
    """
    if not rows:
        return 0

    scan_rows = rows[:_HEADER_MAX_SCAN]
    for i in range(min(len(scan_rows) - 1, _HEADER_MAX_SCAN)):
        if _is_data_row(scan_rows[i]):
            continue  # 当前行是数据行，不是表头
        if not _looks_like_header(scan_rows[i]):
            continue  # 当前行不像表头（空行/标题行）
        if _is_data_row(scan_rows[i + 1]):
            return i  # 下一行是数据行 → 这行是表头

    return 0  # 兜底：默认第一行是表头


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
_CHUNK_SIZE = 100_000       # 每块行数（从 50K 提升到 100K 减少循环开销）
_CHUNK_WORKERS = 3          # 分块并行 worker 数


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


def _apply_column_mapping(df, column_mapping: dict[str, str]):
    """用 AI 决策的 column_mapping 重命名列。key 是列字母(A/B/C)，value 是业务列名。

    安全机制：重命名后如果产生重复列名，对重复的加后缀 _1/_2 去重。
    """
    if not column_mapping:
        return df
    rename_map = {}
    for col_letter, new_name in column_mapping.items():
        col_idx = 0
        for ch in col_letter.upper():
            col_idx = col_idx * 26 + (ord(ch) - ord("A") + 1)
        col_idx -= 1  # 转为 0-indexed
        if col_idx < len(df.columns):
            old_name = str(df.columns[col_idx])
            if old_name != new_name:
                rename_map[old_name] = new_name
    if not rename_map:
        return df

    df = df.rename(columns=rename_map)

    # 重命名后去重：重复列名加后缀 _1/_2
    cols = list(df.columns)
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    for c in cols:
        c_str = str(c)
        if c_str in seen:
            seen[c_str] += 1
            new_cols.append(f"{c_str}_{seen[c_str]}")
        else:
            seen[c_str] = 0
            new_cols.append(c_str)
    if new_cols != [str(c) for c in cols]:
        df.columns = new_cols
        logger.info(f"Column mapping applied (deduped): {rename_map}")
    else:
        logger.info(f"Column mapping applied: {rename_map}")
    return df


def _convert_excel_to_parquet(
    excel_path: str, cache_path: str, sheet: str | None,
    src_mtime: float, src_size: int, snapshot_path: str,
    ai_decision=None,
    strategy=None,
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

    # 表头检测：优先用 AI 预探测结果，失败退回代码检测
    if ai_decision and ai_decision.confidence in ("high", "medium"):
        # actual_start 语义：表头行位置(0-indexed)，与代码路径统一
        if ai_decision.header_rows:
            actual_start = ai_decision.header_rows[0] - 1  # 表头位置(0-indexed)
        elif ai_decision.data_start_row:
            actual_start = ai_decision.data_start_row - 2  # 推算：数据上一行是表头
        else:
            # 两个都没有 → 退回代码检测
            sheet_raw = reader.load_sheet(target_sheet, header_row=None, n_rows=_HEADER_MAX_SCAN)
            df_raw = sheet_raw.to_pandas()
            actual_start = detect_header_row(df_raw.values.tolist())
        actual_start = max(actual_start, 0)  # 防止负数
        header_depth = len(ai_decision.header_rows) if ai_decision.header_rows else 1
        logger.info(
            f"Using AI decision | src={Path(excel_path).name} "
            f"| header_rows={ai_decision.header_rows} | data_start={ai_decision.data_start_row}"
        )
    else:
        sheet_raw = reader.load_sheet(target_sheet, header_row=None, n_rows=_HEADER_MAX_SCAN)
        df_raw = sheet_raw.to_pandas()
        header_row = detect_header_row(df_raw.values.tolist())
        actual_start, header_depth = detect_header_depth(header_row, None)

    # 获取合并单元格等结构信息（传给 clean_excel 和 generate_file_meta）
    from services.agent.excel_cleaner import _detect_structure
    structure = _detect_structure(excel_path, target_sheet)
    merged_ranges = structure.merged_ranges if structure else []

    if actual_start > 0 or header_depth > 1:
        logger.info(
            f"Excel header auto-detected | src={Path(excel_path).name} "
            f"| header_row={actual_start} | depth={header_depth}"
        )

    # ── 单 Sheet 多表格检测（prescan 优先）──
    from services.agent.table_region_detector import convert_multi_region, detect_table_regions
    _skip_region_detect = (
        ai_decision
        and ai_decision.confidence in ("high", "medium")
        and len(ai_decision.regions) <= 1
    )
    if not _skip_region_detect:
        scan_raw = reader.load_sheet(target_sheet, header_row=None, n_rows=5000)
        scan_rows = scan_raw.to_pandas().values.tolist()
        regions = detect_table_regions(scan_rows)
        if len(regions) >= 2:
            _col_mapping = ai_decision.column_mapping if ai_decision else {}
            # 从 _AIDecisionAdapter 拿真正的 AIDecision（含 regions）+ strategy
            _ai_decision = getattr(ai_decision, "_d", None) if ai_decision else None
            convert_multi_region(
                excel_path, str(cache_path), regions, sheet_names,
                resolved_name, src_mtime, src_size, str(snapshot_path),
                column_mapping=_col_mapping,
                decision=_ai_decision,
                strategy=strategy,
            )
            return sheet_names

    # 估算总行数（从 fastexcel 快速获取）
    try:
        probe = reader.load_sheet(target_sheet, header_row=actual_start)
        total_rows = probe.total_height
    except Exception:
        total_rows = 0

    tmp_path = str(Path(cache_path).parent / f"_tmp_{uuid.uuid4().hex[:8]}.parquet")

    # Bug-8 修复：大文件 + 多级表头 → 降级为单级表头读首层
    # （否则 pd.read_excel(header=[0,1,...]) 全量加载会 OOM）
    _force_single_header_for_large = (
        header_depth > 1 and total_rows >= _CHUNK_THRESHOLD
    )
    if _force_single_header_for_large:
        logger.warning(
            f"Large file with multi-level header ({total_rows:,} rows, "
            f"depth={header_depth}) → fallback to single header at row {actual_start} "
            f"to avoid OOM | src={Path(excel_path).name}"
        )
        header_depth = 1

    # ── 小文件：全量读取 + 完整预处理 ──
    if total_rows < _CHUNK_THRESHOLD or header_depth > 1:
        if header_depth > 1:
            header_param = list(range(actual_start, actual_start + header_depth))
            try:
                df = pd.read_excel(excel_path, sheet_name=target_sheet, header=header_param)
            except Exception as e:
                # 多级表头读取失败（重复列名/格式异常等），降级为单层
                logger.warning(
                    f"Multi-header read failed ({type(e).__name__}: {e}), "
                    f"fallback to single header | src={Path(excel_path).name}"
                )
                sheet_data = reader.load_sheet(target_sheet, header_row=actual_start)
                df = sheet_data.to_pandas()
                header_depth = 1
        else:
            sheet_data = reader.load_sheet(target_sheet, header_row=actual_start)
            df = sheet_data.to_pandas()

        # 提取 prescan 的 special_rows（合计行等）
        _special_rows = None
        if ai_decision and ai_decision.confidence in ("high", "medium"):
            _special_rows = ai_decision.special_rows or None

        df, cleaning_report = clean_excel(
            df, excel_path, resolved_name, actual_start,
            structure=structure, special_rows=_special_rows,
            strategy=strategy,
        )
        # 设置行号映射参数
        cleaning_report.header_row = actual_start
        cleaning_report.data_start_row = actual_start + header_depth + 1
        cleaning_report.row_offset = header_depth
        # AI column_mapping 重命名列
        if ai_decision and ai_decision.column_mapping:
            df = _apply_column_mapping(df, ai_decision.column_mapping)
            cleaning_report.issues.append({
                "type": "column_renamed",
                "severity": "info",
                "location": {"cols": list(ai_decision.column_mapping.values())},
                "preserved": False,
                "action": f"AI 重命名列：{ai_decision.column_mapping}",
                "recovery_hint": "列名已按 AI 预探测结果重命名",
            })
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
            merged_ranges=merged_ranges,
            ai_decision=ai_decision if (
                ai_decision and ai_decision.confidence in ("high", "medium")
            ) else None,
        )
        if ai_decision and ai_decision.confidence in ("high", "medium"):
            # 支持新 _AIDecisionAdapter（含 to_dict()）和旧 dataclass PrescanResult
            if hasattr(ai_decision, "to_dict"):
                file_meta.prescan = ai_decision.to_dict()
            else:
                from dataclasses import asdict as _asdict
                file_meta.prescan = _asdict(ai_decision)
            # AI 检测到的数据异常 → 转成 issues
            if ai_decision.anomalies:
                for a in ai_decision.anomalies:
                    file_meta.issues.append({
                        "type": f"anomaly_{a.get('type', 'unknown')}",
                        "severity": a.get("severity", "warning"),
                        "location": {"col": a.get("column", ""),
                                     "rows": a.get("sample_rows", [])},
                        "preserved": True,
                        "action": a.get("description", ""),
                        "recovery_hint": "AI 检测到的数据异常，建议分析时注意",
                    })
        # 粒度检测（小文件：df 是完整数据，len(df) 是真实行数）
        from services.agent.file_meta import _detect_grain
        _grain = _detect_grain(df, file_meta.schema, len(df))
        if _grain:
            file_meta.grain = _grain
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
        # ── 大文件：calamine 流式读 + chunk 累加（替代 fastexcel 并行 skip_rows）──
        # 实测对比（500K 行 23 列）：
        #   fastexcel skip_rows 5 块：15s / peak 2530MB（每块重复全量解压 + 内存累积）
        #   calamine iter_rows 顺序：6.8s / peak 1095MB（真流式，不累积）
        import gc as _gc
        import pyarrow as pa
        import pyarrow.parquet as pq
        import python_calamine

        # 预扫描：三段采样 + clean_excel → 确定 target_schema
        target_schema = _prescan_schema(reader, target_sheet, actual_start, excel_path)

        # 提取 prescan 的 special_rows（合计行等）
        _special_rows = None
        if ai_decision and ai_decision.confidence in ("high", "medium"):
            _special_rows = ai_decision.special_rows or None

        # 如果 prescan 识别到合计行，schema 预留 _is_summary 列
        if _special_rows and _special_rows.get("summary"):
            target_schema = target_schema.append(pa.field("_is_summary", pa.bool_()))

        writer = pq.ParquetWriter(tmp_path, target_schema)
        row_count = 0
        merged_report = CleaningReport()
        _col_mapping = ai_decision.column_mapping if ai_decision else {}

        # calamine 流式遍历 + 100K 行 chunk 处理
        wb = python_calamine.CalamineWorkbook.from_path(excel_path)
        # target_sheet 可能是 int 索引或 str 名字
        if isinstance(target_sheet, int):
            ws = wb.get_sheet_by_index(target_sheet)
        else:
            ws = wb.get_sheet_by_name(target_sheet)

        col_names: list[str] = []
        chunk_buf: list[list[Any]] = []
        chunk_idx = 0
        rows_seen = 0
        import pandas as _pd
        for raw_row in ws.iter_rows():
            if rows_seen <= actual_start:
                if rows_seen == actual_start:
                    col_names = [str(v) for v in raw_row]
                rows_seen += 1
                continue
            chunk_buf.append(list(raw_row))
            rows_seen += 1
            if len(chunk_buf) >= _CHUNK_SIZE:
                df_chunk = _pd.DataFrame(chunk_buf)
                # calamine 空值是 ""，转 NaN 让后续清洗逻辑正常
                df_chunk = df_chunk.mask(df_chunk.eq(""), np.nan)
                if col_names and len(df_chunk.columns) == len(col_names):
                    df_chunk.columns = col_names

                df_chunk, chunk_report = clean_excel(
                    df_chunk, excel_path, resolved_name, actual_start,
                    structure=structure, special_rows=_special_rows,
                    chunk_row_offset=_CHUNK_SIZE * chunk_idx,
                    strategy=strategy,
                )
                if _col_mapping:
                    df_chunk = _apply_column_mapping(df_chunk, _col_mapping)
                table = _cast_to_schema(df_chunk, target_schema)
                writer.write_table(table)
                row_count += len(df_chunk)
                if chunk_report:
                    merged_report.merge(chunk_report)
                chunk_buf = []
                chunk_idx += 1
                del df_chunk, table
                _gc.collect()

        # 收尾 chunk
        if chunk_buf:
            df_chunk = _pd.DataFrame(chunk_buf)
            df_chunk = df_chunk.mask(df_chunk.eq(""), np.nan)
            if col_names and len(df_chunk.columns) == len(col_names):
                df_chunk.columns = col_names
            df_chunk, chunk_report = clean_excel(
                df_chunk, excel_path, resolved_name, actual_start,
                structure=structure, special_rows=_special_rows,
                chunk_row_offset=_CHUNK_SIZE * chunk_idx,
                strategy=strategy,
            )
            if _col_mapping:
                df_chunk = _apply_column_mapping(df_chunk, _col_mapping)
            table = _cast_to_schema(df_chunk, target_schema)
            writer.write_table(table)
            row_count += len(df_chunk)
            if chunk_report:
                merged_report.merge(chunk_report)
            del df_chunk, table
            _gc.collect()

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
                # 三段采样（前2000+中1000+尾2000=5000行），比只取头部更有代表性
                _mid_off = row_count // 2
                _tail_off = max(0, row_count - 2000)
                sample_df = duckdb.sql(
                    f"(SELECT * FROM '{cache_path}' LIMIT 2000)"
                    f" UNION ALL "
                    f"(SELECT * FROM '{cache_path}' LIMIT 1000 OFFSET {_mid_off})"
                    f" UNION ALL "
                    f"(SELECT * FROM '{cache_path}' LIMIT 2000 OFFSET {_tail_off})"
                ).to_df()
                formulas, formula_skip = extract_formulas(excel_path)
                file_meta = generate_file_meta(
                    sample_df, merged_report,
                    source_file=excel_path,
                    sheet_count=len(sheet_names),
                    formulas=formulas,
                    formula_skip_reason=formula_skip,
                    merged_ranges=merged_ranges,
                    ai_decision=ai_decision if (
                        ai_decision and ai_decision.confidence in ("high", "medium")
                    ) else None,
                )
                # 修正行数为实际总行数（采样 df 只有 5000 行）
                file_meta.summary["row_count"] = row_count
                if ai_decision and ai_decision.confidence in ("high", "medium"):
                    # 兼容 _AIDecisionAdapter（V2 架构，非 dataclass）和旧 PrescanResult dataclass
                    if hasattr(ai_decision, "to_dict"):
                        file_meta.prescan = ai_decision.to_dict()
                    else:
                        from dataclasses import asdict as _asdict
                        file_meta.prescan = _asdict(ai_decision)
                    # AI 检测到的数据异常 → 转成 issues
                    if ai_decision.anomalies:
                        for a in ai_decision.anomalies:
                            file_meta.issues.append({
                                "type": f"anomaly_{a.get('type', 'unknown')}",
                                "severity": a.get("severity", "warning"),
                                "location": {"col": a.get("column", ""),
                                             "rows": a.get("sample_rows", [])},
                                "preserved": True,
                                "action": a.get("description", ""),
                                "recovery_hint": "AI 检测到的数据异常，建议分析时注意",
                            })
                # AI 列重命名记录
                if ai_decision and ai_decision.column_mapping:
                    file_meta.issues.append({
                        "type": "column_renamed",
                        "severity": "info",
                        "location": {"cols": list(ai_decision.column_mapping.values())},
                        "preserved": False,
                        "action": f"AI 重命名列：{ai_decision.column_mapping}",
                        "recovery_hint": "列名已按 AI 预探测结果重命名",
                    })
                # 粒度检测（大文件：用采样 df + 实际行数）
                from services.agent.file_meta import _detect_grain
                _grain = _detect_grain(sample_df, file_meta.schema, row_count)
                if _grain:
                    file_meta.grain = _grain
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

        # V1.2 calamine 重构后：chunk 数从 row_count 算（顺序处理无 workers 概念）
        _chunks = (row_count + _CHUNK_SIZE - 1) // _CHUNK_SIZE
        logger.info(
            f"Excel chunked convert | src={Path(excel_path).name} "
            f"| chunks={_chunks} | chunk_size={_CHUNK_SIZE:,} | engine=calamine"
        )

    # v2.2: snapshot 原子写 + 内容指纹（tmp+rename，避免中途崩溃留半截）
    _write_snapshot_atomic(snapshot_path, _compute_file_fingerprint(excel_path))
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
    decision: Any = None,
    strategy: Any = None,
) -> list[str]:
    """所有同结构 Sheet 合并为单个 Parquet（加 _sheet 列标识来源）。

    Args:
        decision: AIDecision，按 decision.sheets[i].role 过滤 meta/aggregated/skip
        strategy: CleaningStrategy，传给 clean_excel（合计行/混合类型/ID 列保护）

    decision=None 时保持向后兼容（所有 sheet 都进，clean_excel 走硬规则）。
    """
    import fastexcel
    import pandas as pd

    start = time.monotonic()
    reader = fastexcel.read_excel(excel_path)
    sheet_names = reader.sheet_names

    # 构建 sheet name → role 映射（AI 决策的 sheets 字段）
    sheet_role_map: dict[str, str] = {}
    if decision is not None:
        for s in getattr(decision, "sheets", []):
            sheet_role_map[s.name] = s.role

    from services.agent.excel_cleaner import (
        CleaningReport, clean_excel, write_cleaning_report,
    )
    from services.agent.file_meta import extract_formulas, generate_file_meta, write_file_meta
    from services.agent.session_files import update_session_files

    frames: list = []
    skipped_sheets: list[tuple[str, str]] = []  # (sheet_name, role)
    total_rows = 0
    merged_report = CleaningReport()
    first_data_start_row: int = 2  # 取第一个 Sheet 的行号映射，后续赋值
    for name in sheet_names:
        # 按 AI 决策跳过非数据 sheet（meta/aggregated/skip）
        role = sheet_role_map.get(name, "data")
        if role in ("meta", "aggregated", "skip"):
            skipped_sheets.append((name, role))
            logger.info(
                f"Sheet skipped per AI decision | sheet={name} | role={role} "
                f"| src={Path(excel_path).name}"
            )
            continue

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
                strategy=strategy,
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
    # 多 Sheet 元数据：让 AI 知道(1)已合并到 _sheet 列(2)哪些 sheet 被 AI 跳过
    # 通过 evidence_summary['sheets'] 透传给 view 层，避免改 FileMeta dataclass 与 generate_file_meta 签名
    merged_sheets = [
        n for n in sheet_names
        if sheet_role_map.get(n, "data") not in ("meta", "aggregated", "skip")
    ]
    file_meta.evidence_summary["sheets"] = {
        "total": len(sheet_names),
        "merged": merged_sheets,           # 已并入 Parquet（_sheet 列区分）
        "skipped": [                       # AI 判定跳过：role + 原因
            {"name": n, "role": r} for n, r in skipped_sheets
        ],
    }
    write_file_meta(cache_path, file_meta)
    update_session_files(
        str(Path(cache_path).parent), cache_path,
        columns=[str(c) for c in merged.columns if not str(c).startswith("_is_")],
        row_count=len(merged),
        source_file=excel_path,
    )

    # v2.2: snapshot 原子写 + 内容指纹（tmp+rename，避免中途崩溃留半截）
    _write_snapshot_atomic(snapshot_path, _compute_file_fingerprint(excel_path))
    logger.info(
        f"Excel→Parquet merge-all | src={Path(excel_path).name} "
        f"| sheets={len(frames)} | rows={len(merged):,} "
        f"| elapsed={time.monotonic() - start:.1f}s"
    )
    del merged
    return sheet_names
