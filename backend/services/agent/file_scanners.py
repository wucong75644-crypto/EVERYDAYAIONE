"""file_analyze 重构 - 4 路径独立代码扫描器。

每条路径产出统一格式的 EvidencePool，作为 AI 一次裁决的输入。

路径分流（make_scanner 工厂）：
  - len(sheet_names) >= 2     → 路径 D（多 sheet）
  - 单 sheet + region_count≥2 → 路径 C（多区域）
  - 单 sheet + ≥100k 行       → 路径 B（大文件分块）
  - 单 sheet + <100k 行       → 路径 A（小文件全表）

设计文档：docs/document/TECH_file_analyze_重构.md §6
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from services.agent.data_query_cache import _classify_cell, detect_header_row
from services.agent.excel_cleaner import _detect_structure
from services.agent.file_evidence import (
    ColumnEvidence,
    EvidencePool,
    FormulaEvidence,
    RegionEvidence,
    SheetEvidence,
    SuspiciousRow,
)
from services.agent.file_meta import extract_formulas
from services.agent.table_region_detector import (
    detect_table_regions,
    has_multiple_regions_streaming,
)

# ── 常量 ──

CHUNK_THRESHOLD = 100_000      # 路径 B 分块阈值
HEADER_MAX_SCAN = 20           # 探测表头扫描行数
KEYWORD_SCAN_CHUNK = 20_000    # 路径 B 关键词扫描 head/tail 各 20k 行
MAX_SHEETS_SAMPLED = 20        # 路径 D 完整采样的 sheet 上限
MAX_SHEETS_LISTED = 200        # 路径 D 仅列名的 sheet 上限

# V2.2 #13: ColumnEvidence 列数上限（防超宽表 evidence 爆炸 + LLM token 超限）
MAX_COLUMN_EVIDENCE = 200      # 超过此列数只采前 N 列产 ColumnEvidence

# 路径 A 关键样本默认上限
SAMPLE_HEAD_DEFAULT = 5
SAMPLE_MID_DEFAULT = 3
SAMPLE_TAIL_DEFAULT = 5

# 可疑行上限规则
SUSPICIOUS_MIN_NULL_RATIO = 0.5  # 整行 ≥ 50% 列为空视为可疑
SUMMARY_KEYWORDS = ("合计", "总计", "小计", "Total", "Subtotal", "Grand Total")

# 列字母 ↔ 索引
_RE_CURRENCY_PREFIX = re.compile(r"^[¥$￥]")
_RE_UNIT_NUMBER = re.compile(r"^-?\d+\.?\d*\s*[A-Za-z一-鿿]+$")

# V2.2 #15: 扩展 ID 列识别覆盖 UUID/ObjectId/ASIN 等非纯数字 ID
_RE_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RE_OBJECTID = re.compile(r"^[0-9a-fA-F]{24}$")  # MongoDB ObjectId
_RE_ASIN = re.compile(r"^B[0-9A-Z]{9}$")          # Amazon ASIN (B + 9 字母数字)
_RE_HEX_ID = re.compile(r"^[0-9a-fA-F]{16,64}$")  # 通用 hex ID (16-64 位)


def _is_known_id_format(s: str) -> bool:
    """识别常见非纯数字 ID 格式（UUID / ObjectId / ASIN / 通用 hex）。"""
    if not s or len(s) < 8:
        return False
    return bool(
        _RE_UUID.match(s)
        or _RE_OBJECTID.match(s)
        or _RE_ASIN.match(s)
        or _RE_HEX_ID.match(s)
    )


# 复用 file_meta.dataclass 的统一实现（与 excel_cleaner.structure._col_index_to_letter_local 同源）
from services.agent.file_meta.dataclass import (  # noqa: E402
    _col_index_to_letter as col_letter,
)


# ── 基类 ──

class BaseScanner(ABC):
    """4 路径扫描器共享基类。

    子类需实现 scan() 返回完整 EvidencePool。
    """

    PATH_TYPE: str = ""

    def __init__(self, excel_path: str, reader: Any):
        self.excel_path = excel_path
        self.reader = reader
        self.file_name = Path(excel_path).name
        self.file_size = Path(excel_path).stat().st_size

    @abstractmethod
    def scan(self) -> EvidencePool:
        """执行扫描，返回完整 evidence_pool。"""
        ...

    # ── 共享工具 ──

    def _scan_columns(self, df) -> list[ColumnEvidence]:
        """按列扫描，构建 ColumnEvidence 列表。

        sample_values 取 head 5 + mid 3 + tail 5（自适应总行数）。
        """
        import numpy as np
        # 修 fastexcel fallback-to-string 列把空 cell 读成 "" 而非 NaN 的 bug
        # 不修会导致 null_ratio 在这类列上严重低估（如真实文件街道列 0% vs 实际 83%）
        df = df.mask(df.eq(""), np.nan)
        cols: list[ColumnEvidence] = []
        n = len(df)
        # 取样行索引
        idx_set: list[int] = []
        if n > 0:
            idx_set.extend(range(min(5, n)))
            if n > 8:
                mid = n // 2
                idx_set.extend(range(mid, min(mid + 3, n)))
            if n > 13:
                idx_set.extend(range(max(0, n - 5), n))
        idx_set = list(dict.fromkeys(idx_set))  # 去重保序

        for i, col_name in enumerate(df.columns):
            # V2.2 #13: 列数上限（超宽表 evidence 爆炸 / LLM token 超限）
            if len(cols) >= MAX_COLUMN_EVIDENCE:
                logger.warning(
                    f"ColumnEvidence truncated at {MAX_COLUMN_EVIDENCE} cols "
                    f"(file has {len(df.columns)} cols, rest will be invisible to AI)"
                )
                break
            col_str = str(col_name)
            if col_str.startswith("_is_"):
                continue
            series = df.iloc[:, i]
            non_null = series.dropna()

            # 分类分布（限于采样行，节省时间）
            sample_vals: list[Any] = []
            classified: dict[str, int] = {}
            for ridx in idx_set:
                val = df.iat[ridx, i]
                sample_vals.append(val)
                cls = _classify_cell(val)
                classified[cls] = classified.get(cls, 0) + 1

            null_ratio = round(1 - len(non_null) / max(n, 1), 4)

            # ID 候选：宽松筛选给 AI 看（最终由 AI 裁决 is_id_column）
            #   规则 1：long_id（≥10 位数字串）占比 ≥ 50%
            #   规则 2：数值列且采样里有 abs ≥ 1e10 的大数（pandas float64 误读 ID）
            #   规则 3 (V2.2 #15)：UUID / ObjectId / ASIN / hex ID 等非纯数字 ID 占比 ≥ 50%
            non_empty = sum(v for k, v in classified.items() if k != "empty")
            long_id_ratio = (classified.get("long_id", 0) / non_empty) if non_empty else 0
            is_long_id = long_id_ratio >= 0.5
            if not is_long_id and len(non_null) > 0:
                try:
                    if non_null.abs().max() >= 1e10:
                        is_long_id = True
                except (TypeError, ValueError, AttributeError):
                    pass
            # 规则 3：扩展 ID 格式（仅在 text 占主导且 sample 充足时检查）
            if not is_long_id and sample_vals:
                id_format_hits = sum(
                    1 for v in sample_vals
                    if v is not None and _is_known_id_format(str(v).strip())
                )
                if id_format_hits / max(len(sample_vals), 1) >= 0.5:
                    is_long_id = True

            # 货币前缀 / 单位后缀
            has_currency = False
            has_unit = False
            for val in sample_vals[:10]:
                if val is None:
                    continue
                s = str(val).strip()
                if not s:
                    continue
                if _RE_CURRENCY_PREFIX.match(s):
                    has_currency = True
                if _RE_UNIT_NUMBER.match(s):
                    has_unit = True

            cols.append(ColumnEvidence(
                col_letter=col_letter(i),
                raw_header=col_str,
                sample_values=sample_vals,
                classified_dist=classified,
                null_ratio=null_ratio,
                is_long_id_candidate=is_long_id,
                has_unit_suffix_candidates=has_unit,
                has_currency_prefix=has_currency,
            ))
        return cols

    def _scan_suspicious_rows(
        self, df, data_start_excel_row: int, limit: int = 500,
    ) -> list[SuspiciousRow]:
        """扫描可疑行：含关键词 / 多列缺失。向量化优化（避免逐行 iloc）。

        data_start_excel_row: 数据第一行的 Excel 1-indexed 行号
        limit: 上限（按 file 总行数自适应，调用方传入）

        性能：10 万行扫描 <500ms（旧版逐行 iloc 50s+）
        """
        import numpy as np
        import pandas as pd

        suspicious: list[SuspiciousRow] = []
        n_cols = len(df.columns)
        n_rows = len(df)
        if n_cols == 0 or n_rows == 0:
            return suspicious

        # 修 fastexcel fallback-to-string 列把空 cell 读成 "" 而非 NaN 的 bug
        df = df.mask(df.eq(""), np.nan)

        # ① 向量化：每行 null 比例
        null_mask_2d = df.isna().to_numpy()       # (n_rows, n_cols) bool
        null_ratios = null_mask_2d.sum(axis=1) / n_cols
        multi_null_mask = null_ratios >= SUSPICIOUS_MIN_NULL_RATIO

        # ② 向量化：关键词扫描
        # 只在含 object/str 类型的列做（数值列不可能含"合计"）
        str_cols = [c for c in df.columns if df[c].dtype == object]
        if str_cols:
            # 各 str 列转字符串后用 " ".join 拼接每行
            str_df = df[str_cols].fillna("").astype(str)
            row_texts = str_df.agg(" ".join, axis=1)
            # 一次性 str.contains 所有关键词
            import re
            pattern = "|".join(re.escape(kw) for kw in SUMMARY_KEYWORDS)
            kw_match_mask = row_texts.str.contains(pattern, regex=True, na=False).to_numpy()
        else:
            kw_match_mask = np.zeros(n_rows, dtype=bool)

        # ③ 候选行 = 关键词命中 ∨ 多列空
        candidate_mask = kw_match_mask | multi_null_mask
        candidate_indices = np.flatnonzero(candidate_mask)[:limit]
        if len(candidate_indices) == 0:
            return suspicious

        # ④ 仅对候选行（很少）逐行构造 SuspiciousRow
        values_arr = df.to_numpy()
        for idx in candidate_indices:
            row_arr = values_arr[idx]
            kw_hit = bool(kw_match_mask[idx])
            null_ratio = float(null_ratios[idx])

            if kw_hit:
                # 在 str 列文本里查匹配的关键词
                row_text = row_texts.iat[idx] if str_cols else ""
                matched_kw = [kw for kw in SUMMARY_KEYWORDS if kw in row_text]
                reason = "keyword_match"
            else:
                matched_kw = []
                reason = "multi_null"

            suspicious.append(SuspiciousRow(
                row=int(idx) + data_start_excel_row,
                reason=reason,
                keywords=matched_kw,
                null_ratio=round(null_ratio, 4),
                raw_values=[
                    None if (isinstance(v, float) and pd.isna(v)) else v
                    for v in row_arr[:15].tolist()
                ],
            ))
        return suspicious

    def _build_key_samples(
        self, df, data_start_excel_row: int,
        n_head: int, n_mid: int, n_tail: int,
    ) -> list[dict[str, Any]]:
        """构建关键样本（head + mid + tail）。每条 = {"row": excel_row, "cells": [...]}"""
        samples: list[dict[str, Any]] = []
        n = len(df)
        if n == 0:
            return samples

        # head
        for idx in range(min(n_head, n)):
            samples.append({
                "row": idx + data_start_excel_row,
                "cells": list(df.iloc[idx].values),
            })

        # mid（仅文件够长且不重叠）
        if n > n_head + n_tail + n_mid and n_mid > 0:
            mid_start = n // 2
            for idx in range(mid_start, min(mid_start + n_mid, n)):
                samples.append({
                    "row": idx + data_start_excel_row,
                    "cells": list(df.iloc[idx].values),
                })

        # tail
        if n > n_head:
            tail_start = max(n_head, n - n_tail)
            for idx in range(tail_start, n):
                samples.append({
                    "row": idx + data_start_excel_row,
                    "cells": list(df.iloc[idx].values),
                })
        return samples

    def _scan_formulas(self, sheet_name: str | int | None) -> tuple[list[FormulaEvidence], int]:
        """复用 extract_formulas 抓公式。"""
        raw, _skip = extract_formulas(self.excel_path, sheet_name)
        formulas = [
            FormulaEvidence(
                cell=item.get("cell", ""),
                expression=item.get("formula", ""),
                value=item.get("value"),
                col_name="",
            )
            for item in raw
        ]
        return formulas, len(raw)

    def _structure_to_lists(self) -> tuple[list, list[int], list[int], bool]:
        """复用 _detect_structure 拿合并/隐藏/autofilter。失败时返回空。"""
        struct = _detect_structure(self.excel_path, self.reader.sheet_names[0])
        if struct is None:
            return [], [], [], False
        return (
            list(struct.merged_ranges),
            sorted(struct.hidden_rows),
            sorted(struct.hidden_cols),
            struct.has_auto_filter,
        )


# ── 工厂（自动分流）──

def make_scanner(excel_path: str, reader: Any | None = None) -> BaseScanner:
    """根据 probe 结果自动分流到 4 条扫描路径。

    V1.1：file_analyze 工具不暴露 sheet 参数，所以 sheet 永远为 None。
    路径 D 触发条件改为代码 probe 文件 sheet 数 ≥ 2 时自动走。
    """
    import fastexcel
    if reader is None:
        reader = fastexcel.read_excel(excel_path)
    sheet_names = reader.sheet_names

    file_name = Path(excel_path).name

    # ── 路径 D：多 sheet 自动走 ──
    if len(sheet_names) >= 2:
        # 行数防御：多 sheet 文件凭文件大小粗判（避免逐 sheet probe 浪费 IO）
        # 150MB 经验值 — ECS 2.75GB 可用内存安全余量
        _PATH_D_MAX_FILE_SIZE = 150 * 1024 * 1024
        file_size = Path(excel_path).stat().st_size
        if file_size > _PATH_D_MAX_FILE_SIZE:
            from services.agent.file_ai_judge import FileAnalyzeError
            size_mb = round(file_size / 1024 / 1024, 2)
            max_mb = _PATH_D_MAX_FILE_SIZE // 1024 // 1024
            raise FileAnalyzeError(
                error_category="file_too_large",
                error_summary=(
                    f"多 sheet 文件 {Path(excel_path).name} 大小 {size_mb}MB "
                    f"超过 {max_mb}MB 上限"
                ),
                retryable=False,
                suggested_action="ask_user",
                user_message=(
                    f"文件「{Path(excel_path).name}」过大（{size_mb}MB，"
                    f"含 {len(sheet_names)} 个 sheet），建议拆分后单独处理。"
                ),
                file_path=excel_path,
                file_name=Path(excel_path).name,
                file_size_mb=size_mb,
                total_rows=0,
                path_type="D",
            )
        from services.agent.file_scanners_paths import PathDScanner
        logger.info(
            f"make_scanner | path=D | file={file_name} | "
            f"sheets={len(sheet_names)} | reason=multi_sheet"
        )
        return PathDScanner(excel_path, reader)

    # ── 单 sheet：probe 进一步分流 ──
    target = sheet_names[0]
    try:
        probe = reader.load_sheet(target, header_row=None, n_rows=HEADER_MAX_SCAN).to_pandas()
        header_row = detect_header_row(probe.values.tolist())
    except Exception:
        header_row = 0

    try:
        probe_all = reader.load_sheet(target, header_row=header_row)
        total_rows = probe_all.total_height
    except Exception:
        total_rows = 0

    # 大文件：先用流式检测多区域，避免数据混入（V2.2 #7 + #17）
    if total_rows >= CHUNK_THRESHOLD:
        try:
            if has_multiple_regions_streaming(excel_path, target):
                from services.agent.file_ai_judge import FileAnalyzeError
                logger.info(
                    f"make_scanner | path=blocked | file={file_name} | "
                    f"rows={total_rows:,} | reason=large_multi_region"
                )
                raise FileAnalyzeError(
                    error_category="file_too_complex",
                    error_summary=(
                        f"大文件 {file_name} 含多个数据区域"
                    ),
                    retryable=False,
                    suggested_action="ask_user",
                    user_message=(
                        f"文件「{file_name}」≥10万行且检测到多个数据块"
                        f"（含 ≥3 行连续空行的分隔段）。"
                        f"为保证数据正确性，建议按区域拆分上传。"
                    ),
                    file_path=excel_path,
                    file_name=file_name,
                    total_rows=total_rows,
                    path_type="B",
                )
        except FileAnalyzeError:
            raise
        except Exception as e:
            # 流式检测失败不阻塞主流程
            logger.warning(f"streaming region detect failed | {file_name} | {e}")

        from services.agent.file_scanners_paths import PathBScanner
        logger.info(
            f"make_scanner | path=B | file={file_name} | "
            f"rows={total_rows:,} | header_row={header_row} | reason=large_file"
        )
        return PathBScanner(excel_path, reader, header_row=header_row, total_rows=total_rows)

    # 中小文件才做多区域检测
    try:
        scan_raw = reader.load_sheet(target, header_row=None, n_rows=5000).to_pandas()
        regions = detect_table_regions(scan_raw.values.tolist())
    except Exception:
        regions = []

    if len(regions) >= 2:
        from services.agent.file_scanners_paths import PathCScanner
        logger.info(
            f"make_scanner | path=C | file={file_name} | "
            f"rows={total_rows:,} | regions={len(regions)} | reason=multi_region"
        )
        return PathCScanner(excel_path, reader, regions=regions, header_row=header_row)

    from services.agent.file_scanners_paths import PathAScanner
    logger.info(
        f"make_scanner | path=A | file={file_name} | "
        f"rows={total_rows:,} | header_row={header_row} | reason=small_single_region"
    )
    return PathAScanner(excel_path, reader, header_row=header_row, total_rows=total_rows)


def suspicious_row_limit(total_rows: int) -> int:
    """自适应可疑行上限：min(total_rows × 0.1%, 500)，最少 50。"""
    return max(50, min(int(total_rows * 0.001), 500))


def sample_segment_sizes(total_rows: int) -> tuple[int, int, int]:
    """关键样本行数自适应。"""
    if total_rows <= 10_000:
        return 3, 0, 3
    elif total_rows <= 100_000:
        return 4, 2, 4
    elif total_rows <= 1_000_000:
        return 5, 3, 5
    else:
        return 6, 6, 6
