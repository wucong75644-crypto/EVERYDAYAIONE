"""file_analyze 重构 - 4 路径扫描器具体实现。

从 file_scanners.py 分出，避免单文件过大。
PathAScanner / PathBScanner / PathCScanner / PathDScanner 都继承 BaseScanner。
"""
from __future__ import annotations

import gc
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import python_calamine
from loguru import logger

from services.agent.file_meta import extract_formulas

# 并行公式提取超时（秒）。lxml 流式扫到 _MAX_FORMULAS=200 就 break，
# 正常文件几秒内返回；这里给 120s 是给 GB 级 sheet1.xml 兜底。
_PATH_B_FORMULA_TIMEOUT = 120.0

from services.agent.data_query_cache import _classify_cell, detect_header_row
from services.agent.file_evidence import (
    ColumnEvidence,
    EvidencePool,
    RegionEvidence,
    SheetEvidence,
    SuspiciousRow,
)
from services.agent.file_scanners import (
    BaseScanner,
    CHUNK_THRESHOLD,
    HEADER_MAX_SCAN,
    KEYWORD_SCAN_CHUNK,
    MAX_SHEETS_LISTED,
    MAX_SHEETS_SAMPLED,
    SUMMARY_KEYWORDS,
    SUSPICIOUS_MIN_NULL_RATIO,
    _RE_CURRENCY_PREFIX,
    _RE_UNIT_NUMBER,
    col_letter,
    sample_segment_sizes,
    suspicious_row_limit,
)


# ════════════════════════════════════════════════════════════════════
# 路径 A：小文件全表扫描
# ════════════════════════════════════════════════════════════════════

class PathAScanner(BaseScanner):
    """小文件 (< 100k 行) 全量加载 + 全表扫描。"""

    PATH_TYPE = "A"

    def __init__(self, excel_path: str, reader: Any,
                 header_row: int = 0, total_rows: int = 0):
        super().__init__(excel_path, reader)
        self.header_row = header_row
        self.total_rows = total_rows

    def scan(self) -> EvidencePool:
        sheet_names = self.reader.sheet_names
        target = sheet_names[0]

        # 读前 5 行原始候选 + 全表读取（用 detected header）
        probe = self.reader.load_sheet(
            target, header_row=None, n_rows=max(5, HEADER_MAX_SCAN),
        ).to_pandas()
        header_candidates = probe.head(5).values.tolist()

        df = self.reader.load_sheet(target, header_row=self.header_row).to_pandas()
        actual_total = len(df)

        merged, hidden_rows, hidden_cols, has_filter = self._structure_to_lists()
        formulas, total_formulas = self._scan_formulas(target)

        data_start_excel = self.header_row + 2  # Excel 1-indexed
        n_head, n_mid, n_tail = sample_segment_sizes(actual_total)

        columns_ev = self._scan_columns(df)
        suspicious = self._scan_suspicious_rows(
            df, data_start_excel,
            limit=suspicious_row_limit(actual_total),
        )
        key_samples = self._build_key_samples(
            df, data_start_excel, n_head, n_mid, n_tail,
        )

        return EvidencePool(
            file_path=self.excel_path,
            file_name=self.file_name,
            file_size_bytes=self.file_size,
            total_rows=actual_total,
            total_cols=len(df.columns),
            sheet_names=list(sheet_names),
            target_sheet=str(target),
            path_type="A",
            header_candidates=header_candidates,
            detected_header_row_code=self.header_row,
            merged_ranges=merged,
            hidden_rows=hidden_rows,
            hidden_cols=hidden_cols,
            has_auto_filter=has_filter,
            columns=columns_ev,
            key_samples=key_samples,
            suspicious_rows=suspicious,
            formulas=formulas[:30],
            formula_total_count=total_formulas,
        )


# ════════════════════════════════════════════════════════════════════
# 路径 B：大文件分桶 + 流式关键词扫描
# ════════════════════════════════════════════════════════════════════

PATH_B_CHUNK_SIZE = 100_000
# 25M 单元格上限：500K×23列=1.1GB / 1M×8列=2.6GB（接近 ECS 2.75GB 上限）
PATH_B_MAX_TOTAL_CELLS = 25_000_000


class _PathBChunkAccumulator:
    """跨 chunk 累加列证据 + 可疑行 + key_samples。

    替代 BaseScanner._scan_columns / _scan_suspicious_rows / _build_key_samples
    的全表向量化。在 chunk 边界上保持等价语义：
      - null_ratio 用 (累计 null) / (累计 total) 全列精确计算
      - classified_dist 在预定行号采样
      - sample_values 跨 chunk 拼接 head/mid/tail
      - is_long_id_candidate 维护全局 abs().max()
      - suspicious_rows 全行号映射到 Excel 1-indexed
    """

    def __init__(self, total_rows: int, n_cols: int, data_start_excel: int,
                 col_names: list[str], sample_idx_global: list[int],
                 key_sample_idx_global: set[int], suspicious_limit: int):
        self.total_rows = total_rows
        self.n_cols = n_cols
        self.data_start_excel = data_start_excel
        self.col_names = col_names
        self.sample_idx_global = sample_idx_global
        self.key_sample_idx_global = key_sample_idx_global
        self.suspicious_limit = suspicious_limit

        self.col_null_count = np.zeros(n_cols, dtype=np.int64)
        self.col_total_count = np.zeros(n_cols, dtype=np.int64)
        self.col_sample_values: list[list[Any]] = [[] for _ in range(n_cols)]
        self.col_max_abs: list[float] = [0.0] * n_cols
        self.col_max_abs_seen: list[bool] = [False] * n_cols

        self.key_samples_buf: dict[int, list[Any]] = {}
        self.suspicious: list[SuspiciousRow] = []

    def process_chunk(self, chunk_df: pd.DataFrame, chunk_start_local: int) -> None:
        # calamine 空值是 ""，转 NaN 让 isna 正确识别
        # （也修了 baseline 在 fastexcel fallback-to-string 列上 null_ratio 算错的 bug）
        # 用 mask 而非 replace，避免 pandas 2.2 的 downcasting FutureWarning
        chunk_df = chunk_df.mask(chunk_df.eq(""), np.nan)
        chunk_df.columns = self.col_names

        n = len(chunk_df)
        if n == 0 or self.n_cols == 0:
            return

        null_mask = chunk_df.isna().to_numpy()
        self.col_null_count += null_mask.sum(axis=0)
        self.col_total_count += n

        for ci in range(self.n_cols):
            non_null = chunk_df.iloc[:, ci].dropna()
            if len(non_null) > 0:
                try:
                    m = float(non_null.abs().max())
                    if m > self.col_max_abs[ci]:
                        self.col_max_abs[ci] = m
                    self.col_max_abs_seen[ci] = True
                except (TypeError, ValueError, AttributeError):
                    pass

        for global_idx in self.sample_idx_global:
            if chunk_start_local <= global_idx < chunk_start_local + n:
                local_idx = global_idx - chunk_start_local
                for ci in range(self.n_cols):
                    self.col_sample_values[ci].append(chunk_df.iat[local_idx, ci])

        for global_idx in self.key_sample_idx_global:
            if chunk_start_local <= global_idx < chunk_start_local + n:
                local_idx = global_idx - chunk_start_local
                self.key_samples_buf[global_idx] = list(chunk_df.iloc[local_idx].values)

        # 可疑行向量化
        null_ratios = null_mask.sum(axis=1) / self.n_cols
        multi_null_mask = null_ratios >= SUSPICIOUS_MIN_NULL_RATIO

        # 用列索引而非列名（防止重复列名导致返回 DataFrame）
        str_col_idx = [i for i in range(self.n_cols)
                       if chunk_df.iloc[:, i].dtype == object]
        if str_col_idx:
            str_df = chunk_df.iloc[:, str_col_idx].fillna("").astype(str)
            row_texts = str_df.agg(" ".join, axis=1)
            pattern = "|".join(re.escape(kw) for kw in SUMMARY_KEYWORDS)
            kw_match_mask = row_texts.str.contains(
                pattern, regex=True, na=False,
            ).to_numpy()
        else:
            kw_match_mask = np.zeros(n, dtype=bool)

        candidate_mask = kw_match_mask | multi_null_mask
        candidate_indices = np.flatnonzero(candidate_mask)

        room = self.suspicious_limit - len(self.suspicious)
        if room <= 0:
            return
        candidate_indices = candidate_indices[:room]

        if len(candidate_indices) > 0:
            values_arr = chunk_df.to_numpy()
            for idx in candidate_indices:
                row_arr = values_arr[idx]
                kw_hit = bool(kw_match_mask[idx])
                if kw_hit:
                    row_text = row_texts.iat[idx] if str_col_idx else ""
                    matched_kw = [kw for kw in SUMMARY_KEYWORDS if kw in row_text]
                    reason = "keyword_match"
                else:
                    matched_kw = []
                    reason = "multi_null"
                global_excel_row = (
                    chunk_start_local + int(idx) + self.data_start_excel
                )
                self.suspicious.append(SuspiciousRow(
                    row=global_excel_row,
                    reason=reason,
                    keywords=matched_kw,
                    null_ratio=round(float(null_ratios[idx]), 4),
                    raw_values=[
                        None if (isinstance(v, float) and pd.isna(v)) else v
                        for v in row_arr[:15].tolist()
                    ],
                ))

    def finalize_columns(self) -> list[ColumnEvidence]:
        cols: list[ColumnEvidence] = []
        for ci, col_name in enumerate(self.col_names):
            col_str = str(col_name)
            if col_str.startswith("_is_"):
                continue
            sample_vals = self.col_sample_values[ci]
            classified: dict[str, int] = {}
            for v in sample_vals:
                cls = _classify_cell(v)
                classified[cls] = classified.get(cls, 0) + 1

            total = int(self.col_total_count[ci])
            null_count = int(self.col_null_count[ci])
            null_ratio = round(1 - (total - null_count) / max(total, 1), 4)

            non_empty = sum(v for k, v in classified.items() if k != "empty")
            long_id_ratio = (classified.get("long_id", 0) / non_empty) if non_empty else 0
            is_long_id = long_id_ratio >= 0.5
            if not is_long_id and self.col_max_abs_seen[ci]:
                if self.col_max_abs[ci] >= 1e10:
                    is_long_id = True

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
                col_letter=col_letter(ci),
                raw_header=col_str,
                sample_values=sample_vals,
                classified_dist=classified,
                null_ratio=null_ratio,
                is_long_id_candidate=is_long_id,
                has_unit_suffix_candidates=has_unit,
                has_currency_prefix=has_currency,
            ))
        return cols

    def finalize_key_samples(self) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        for global_idx in sorted(self.key_samples_buf.keys()):
            samples.append({
                "row": global_idx + self.data_start_excel,
                "cells": self.key_samples_buf[global_idx],
            })
        return samples


def _build_path_b_sample_idx(n_data: int) -> list[int]:
    """复刻 BaseScanner._scan_columns 的采样行号算法（head 5 / mid 3 / tail 5）。"""
    idx_set: list[int] = []
    if n_data > 0:
        idx_set.extend(range(min(5, n_data)))
        if n_data > 8:
            mid = n_data // 2
            idx_set.extend(range(mid, min(mid + 3, n_data)))
        if n_data > 13:
            idx_set.extend(range(max(0, n_data - 5), n_data))
    return list(dict.fromkeys(idx_set))


def _build_path_b_key_sample_idx(n_data: int, n_head: int, n_mid: int,
                                  n_tail: int) -> set[int]:
    """复刻 BaseScanner._build_key_samples 的行号算法。"""
    idx_set: set[int] = set()
    if n_data == 0:
        return idx_set
    for idx in range(min(n_head, n_data)):
        idx_set.add(idx)
    if n_data > n_head + n_tail + n_mid and n_mid > 0:
        mid_start = n_data // 2
        for idx in range(mid_start, min(mid_start + n_mid, n_data)):
            idx_set.add(idx)
    if n_data > n_head:
        tail_start = max(n_head, n_data - n_tail)
        for idx in range(tail_start, n_data):
            idx_set.add(idx)
    return idx_set


class PathBScanner(BaseScanner):
    """大文件 (≥ 100k 行) calamine 流式 + chunk 累加扫描。

    设计：
      - python-calamine.iter_rows() 流式拿行（Rust 实现，比 openpyxl 快 6x）
      - 每 100k 行打包成 pandas DataFrame chunk → 处理 → 释放
      - chunk DataFrame 不累积，任意时刻只有 1 块在内存
      - 跨 chunk 累加器维护列证据/可疑行/key_samples 全局语义

    优势（相对 fastexcel 全表）：
      - 修 baseline 在 fallback-to-string 列上 null_ratio 算错的 bug
      - 长 ID 天然保留 str，日期天然 datetime
      - 上限从 800K 行（硬性）提到 25M 单元格（按列数自适应）
      - 500K 行峰值内存 1474 MB → 2156 MB 时省 32%

    成本：scan() 端到端 +8%（24s → 26s）。

    超过 MAX_TOTAL_CELLS 主动 raise FileAnalyzeError("file_too_large")，
    保护 2核4G 生产服务器内存（应用可用 ~2.75 GB）。
    """

    PATH_TYPE = "B"
    MAX_TOTAL_CELLS = PATH_B_MAX_TOTAL_CELLS

    def __init__(self, excel_path: str, reader: Any,
                 header_row: int = 0, total_rows: int = 0):
        super().__init__(excel_path, reader)
        self.header_row = header_row
        self.total_rows = total_rows

    def scan(self) -> EvidencePool:
        sheet_names = self.reader.sheet_names
        target = sheet_names[0]

        # ① 用 fastexcel probe 拿 (行数, 列数) — 单元格上限保护
        probe = self.reader.load_sheet(target, header_row=self.header_row)
        n_data = probe.total_height
        n_cols_probe = probe.width
        del probe
        gc.collect()

        if n_data * n_cols_probe > self.MAX_TOTAL_CELLS:
            self._raise_file_too_large(n_data, n_cols_probe)

        # ② 结构（不依赖数据读取，复用 BaseScanner 工具）
        merged, hidden_rows, hidden_cols, has_filter = self._structure_to_lists()

        # ③ 采样位置预计算
        sample_idx_global = _build_path_b_sample_idx(n_data)
        n_head, n_mid, n_tail = sample_segment_sizes(n_data)
        key_sample_idx_global = _build_path_b_key_sample_idx(
            n_data, n_head, n_mid, n_tail,
        )
        susp_limit = suspicious_row_limit(n_data)

        # ④ 公式提取并行化：在 calamine 主扫描启动前丢到独立线程。
        # lxml.iterparse 是 C 实现会释放 GIL，与主线程的 calamine 解压+行解析
        # 可以真并行；50w 行原本串行 290s（公式 30-60s），并行后近似只剩 calamine 主路径。
        formula_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pathb-formula",
        )
        formula_future = formula_executor.submit(
            extract_formulas, self.excel_path, target,
        )

        try:
            # ⑤ calamine 流式读
            wb = python_calamine.CalamineWorkbook.from_path(self.excel_path)
            ws = wb.get_sheet_by_index(0)

            data_start_excel = self.header_row + 2
            header_candidates_raw: list[list[Any]] = []
            col_names: list[str] = []
            n_cols = 0
            acc: _PathBChunkAccumulator | None = None
            chunk_buf: list[list[Any]] = []
            chunk_start_local = 0
            rows_seen = 0

            for raw_row in ws.iter_rows():
                if rows_seen < 5:
                    header_candidates_raw.append(list(raw_row))
                if rows_seen <= self.header_row:
                    if rows_seen == self.header_row:
                        col_names = [str(v) for v in raw_row]
                        n_cols = len(col_names)
                        acc = _PathBChunkAccumulator(
                            total_rows=n_data,
                            n_cols=n_cols,
                            data_start_excel=data_start_excel,
                            col_names=col_names,
                            sample_idx_global=sample_idx_global,
                            key_sample_idx_global=key_sample_idx_global,
                            suspicious_limit=susp_limit,
                        )
                    rows_seen += 1
                    continue
                chunk_buf.append(list(raw_row))
                rows_seen += 1
                if len(chunk_buf) >= PATH_B_CHUNK_SIZE:
                    df = pd.DataFrame(chunk_buf)
                    acc.process_chunk(df, chunk_start_local)
                    chunk_start_local += len(chunk_buf)
                    chunk_buf = []
                    del df
                    gc.collect()

            if chunk_buf and acc is not None:
                df = pd.DataFrame(chunk_buf)
                acc.process_chunk(df, chunk_start_local)
                chunk_start_local += len(chunk_buf)
                chunk_buf = []
                del df
                gc.collect()

            # ⑥ 主扫描结束后再回收公式结果（通常主路径耗时 > 公式提取，近零等待）
            try:
                raw_formulas, _skip = formula_future.result(
                    timeout=_PATH_B_FORMULA_TIMEOUT,
                )
                formulas, total_formulas = self._wrap_formula_raw(raw_formulas)
            except Exception as e:
                # 公式提取失败不影响主流程：返回空列表，PathB 扫描结果照常返回
                logger.warning(
                    f"PathB parallel formula extraction failed: "
                    f"{type(e).__name__}: {e}"
                )
                formulas, total_formulas = [], 0
        finally:
            # wait=False：主扫描已完成，公式线程要么已结束要么 timeout
            # 让 GC 兜底回收，不阻塞主路径
            formula_executor.shutdown(wait=False)

        # 空表 / 表头越界兜底
        if acc is None:
            return EvidencePool(
                file_path=self.excel_path,
                file_name=self.file_name,
                file_size_bytes=self.file_size,
                total_rows=n_data,
                total_cols=n_cols_probe,
                sheet_names=list(sheet_names),
                target_sheet=str(target),
                path_type="B",
                header_candidates=header_candidates_raw,
                detected_header_row_code=self.header_row,
                merged_ranges=merged,
                hidden_rows=hidden_rows,
                hidden_cols=hidden_cols,
                has_auto_filter=has_filter,
                formulas=formulas[:30],
                formula_total_count=total_formulas,
            )

        columns_ev = acc.finalize_columns()
        key_samples = acc.finalize_key_samples()
        total_cols = len([c for c in col_names if not str(c).startswith("_is_")])

        return EvidencePool(
            file_path=self.excel_path,
            file_name=self.file_name,
            file_size_bytes=self.file_size,
            total_rows=n_data,
            total_cols=total_cols,
            sheet_names=list(sheet_names),
            target_sheet=str(target),
            path_type="B",
            header_candidates=header_candidates_raw,
            detected_header_row_code=self.header_row,
            merged_ranges=merged,
            hidden_rows=hidden_rows,
            hidden_cols=hidden_cols,
            has_auto_filter=has_filter,
            columns=columns_ev,
            key_samples=key_samples,
            suspicious_rows=acc.suspicious,
            formulas=formulas[:30],
            formula_total_count=total_formulas,
        )

    def _raise_file_too_large(self, n_rows: int, n_cols: int) -> None:
        """超过 MAX_TOTAL_CELLS 时主动 raise，避免沙盒 OOM。"""
        from services.agent.file_ai_judge import FileAnalyzeError

        size_mb = round(self.file_size / 1024 / 1024, 2)
        cells = n_rows * n_cols
        raise FileAnalyzeError(
            error_category="file_too_large",
            error_summary=(
                f"文件 {self.file_name} 超过 {self.MAX_TOTAL_CELLS:,} 单元格处理上限"
                f"（{n_rows:,} 行 × {n_cols} 列 = {cells:,}）"
            ),
            retryable=False,
            suggested_action="ask_user",
            retry_delay_seconds=0,
            user_message=(
                f"文件「{self.file_name}」过大"
                f"（{size_mb}MB / {n_rows:,} 行 × {n_cols} 列），"
                f"超过 {self.MAX_TOTAL_CELLS:,} 单元格处理上限。\n"
                "建议按日期/区域拆分后分别上传。"
            ),
            file_path=self.excel_path,
            file_name=self.file_name,
            file_size_mb=size_mb,
            total_rows=n_rows,
            path_type="B",
        )


# ════════════════════════════════════════════════════════════════════
# 路径 C：多区域
# ════════════════════════════════════════════════════════════════════

class PathCScanner(BaseScanner):
    """单 sheet 内多个数据区域（空行分隔）。"""

    PATH_TYPE = "C"

    def __init__(self, excel_path: str, reader: Any,
                 regions: list, header_row: int = 0):
        super().__init__(excel_path, reader)
        self.regions = regions
        self.header_row = header_row

    def scan(self) -> EvidencePool:
        sheet_names = self.reader.sheet_names
        target = sheet_names[0]

        scan_raw = self.reader.load_sheet(
            target, header_row=None, n_rows=5000,
        ).to_pandas()

        region_evs = []
        for i, r in enumerate(self.regions):
            head_sample = scan_raw.iloc[
                r.data_start:min(r.data_start + 5, r.data_end)
            ].values.tolist()
            tail_start = max(r.data_start, r.data_end - 5)
            tail_sample = scan_raw.iloc[tail_start:r.data_end].values.tolist()
            n_cols = len(r.columns) if r.columns else 0
            end_col = col_letter(max(n_cols - 1, 0)) if n_cols else "A"
            region_evs.append(RegionEvidence(
                region_id=i + 1,
                range_str=f"A{r.header_row + 1}:{end_col}{r.data_end}",
                header_row=r.header_row,
                header_cells=list(r.columns or []),
                head_sample=head_sample,
                tail_sample=tail_sample,
                row_count=r.row_count,
                suspected_type="unknown",
            ))

        merged, hidden_rows, hidden_cols, has_filter = self._structure_to_lists()
        formulas, total_formulas = self._scan_formulas(target)

        return EvidencePool(
            file_path=self.excel_path,
            file_name=self.file_name,
            file_size_bytes=self.file_size,
            total_rows=len(scan_raw),
            total_cols=scan_raw.shape[1] if not scan_raw.empty else 0,
            sheet_names=list(sheet_names),
            target_sheet=str(target),
            path_type="C",
            header_candidates=scan_raw.head(5).values.tolist(),
            detected_header_row_code=self.header_row,
            merged_ranges=merged,
            hidden_rows=hidden_rows,
            hidden_cols=hidden_cols,
            has_auto_filter=has_filter,
            regions=region_evs,
            formulas=formulas[:30],
            formula_total_count=total_formulas,
        )


# ════════════════════════════════════════════════════════════════════
# 路径 D：多 sheet 自动检测
# ════════════════════════════════════════════════════════════════════

class PathDScanner(BaseScanner):
    """多 sheet 文件，AI 据此决定每个 sheet 角色和合并 group。"""

    PATH_TYPE = "D"

    def scan(self) -> EvidencePool:
        all_names = self.reader.sheet_names[:MAX_SHEETS_LISTED]
        sheet_evs: list[SheetEvidence] = []

        # 完整采样的 sheet
        for name in all_names[:MAX_SHEETS_SAMPLED]:
            try:
                ev = self._scan_one_sheet(name)
                sheet_evs.append(ev)
            except Exception as e:
                logger.warning(f"PathD scan failed | sheet={name} | err={e}")
                sheet_evs.append(SheetEvidence(name=str(name), rows=0, cols=0))

        # 剩余 sheet 仅占位
        if len(all_names) > MAX_SHEETS_SAMPLED:
            ref_cols = sheet_evs[0].cols if sheet_evs else 0
            ref_col_names = sheet_evs[0].column_names if sheet_evs else []
            for name in all_names[MAX_SHEETS_SAMPLED:]:
                sheet_evs.append(SheetEvidence(
                    name=str(name), rows=-1, cols=ref_cols,
                    column_names=list(ref_col_names),
                ))

        total = sum(s.rows for s in sheet_evs if s.rows > 0)
        max_cols = max((s.cols for s in sheet_evs), default=0)
        formulas, total_formulas = self._scan_formulas(None)

        return EvidencePool(
            file_path=self.excel_path,
            file_name=self.file_name,
            file_size_bytes=self.file_size,
            total_rows=total,
            total_cols=max_cols,
            sheet_names=list(all_names),
            target_sheet="*",
            path_type="D",
            sheets=sheet_evs,
            formulas=formulas[:30],
            formula_total_count=total_formulas,
        )

    def _scan_one_sheet(self, name) -> SheetEvidence:
        probe = self.reader.load_sheet(
            name, header_row=None, n_rows=HEADER_MAX_SCAN,
        ).to_pandas()
        header_row = detect_header_row(probe.values.tolist())
        header_candidates = probe.head(3).values.tolist()

        df = self.reader.load_sheet(name, header_row=header_row).to_pandas()

        head_sample = df.head(3).values.tolist() if len(df) > 0 else []
        tail_sample = df.tail(3).values.tolist() if len(df) > 3 else []

        return SheetEvidence(
            name=str(name),
            rows=len(df),
            cols=len(df.columns),
            header_candidates=header_candidates,
            head_sample=head_sample,
            tail_sample=tail_sample,
            column_names=[str(c) for c in df.columns],
        )
