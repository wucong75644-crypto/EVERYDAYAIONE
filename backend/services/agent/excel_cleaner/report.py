"""Excel 清洗数据结构 + 报告 IO。

- ExcelStructure: 结构检测产出（合并/隐藏行列/autofilter）
- CleaningReport: 清洗动作汇总（cleanup actions + issues 列表）
- _dedup_issues: 跨 chunk 合并去重
- read/write_cleaning_report: .meta.json IO
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _dedup_issues(items: list[dict]) -> list[dict]:
    """按 (type, action, location) 去重 issues — 保留首次出现的顺序。

    chunked 处理大文件时，每个 chunk 的 clean_excel 都会产生同样的 issue
    （如"全空列已保留" / "整数修复"），merge 时直接拼接会导致 4x-5x 重复。
    """
    seen: set = set()
    out: list[dict] = []
    for item in items:
        try:
            key = (
                item.get("type"),
                item.get("action"),
                json.dumps(item.get("location"), sort_keys=True, default=str),
            )
        except Exception:
            # 不可哈希 → 不去重，保留
            out.append(item)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


@dataclass
class ExcelStructure:
    """Layer 1 检测结果。"""

    merged_ranges: list[tuple[int, int, int, int]] = field(default_factory=list)
    # (min_row, max_row, min_col, max_col) — 1-indexed, 与 Excel 一致
    hidden_rows: set[int] = field(default_factory=set)   # 1-indexed
    hidden_cols: set[int] = field(default_factory=set)    # 1-indexed
    has_auto_filter: bool = False


@dataclass
class CleaningReport:
    """清洗报告，写入 .meta.json 供 executor 探索模式注入 LLM 上下文。"""

    merged_cols_filled: int = 0
    summary_rows_marked: int = 0
    hidden_rows_marked: int = 0
    hidden_cols_names: list[str] = field(default_factory=list)
    empty_cols_removed: int = 0
    empty_rows_removed: int = 0
    int_cols_fixed: int = 0
    has_auto_filter: bool = False
    warnings: list[str] = field(default_factory=list)  # 旧字段保留兼容
    issues: list[dict] = field(default_factory=list)    # 统一结构化标注
    original_shape: tuple[int, int] = (0, 0)
    final_shape: tuple[int, int] = (0, 0)
    # 行号映射参数（file_meta 生成完整 .meta.json 时使用）
    header_row: int = 0
    data_start_row: int = 2
    row_offset: int = 1

    def merge(self, other: CleaningReport) -> None:
        """将另一个报告累加到自身（多 Sheet / 分块合并场景）。"""
        for attr in ("merged_cols_filled", "summary_rows_marked",
                      "hidden_rows_marked", "empty_cols_removed",
                      "empty_rows_removed", "int_cols_fixed"):
            setattr(self, attr, getattr(self, attr) + getattr(other, attr))
        self.hidden_cols_names = list(set(self.hidden_cols_names + other.hidden_cols_names))
        self.has_auto_filter = self.has_auto_filter or other.has_auto_filter
        self.warnings = list(set(self.warnings + other.warnings))
        # issues 按 (type, action, location) 去重 — 解决 chunked 处理时
        # 多 chunk 产生同样 issue 导致 4x/5x 重复输出的 bug
        self.issues = _dedup_issues(self.issues + other.issues)
        self.original_shape = (self.original_shape[0] + other.original_shape[0],
                               max(self.original_shape[1], other.original_shape[1]))
        self.final_shape = (self.final_shape[0] + other.final_shape[0],
                            max(self.final_shape[1], other.final_shape[1]))
        # 行号映射：保留首块的值（首块决定了 header 位置）
        if self.header_row == 0 and other.header_row > 0:
            self.header_row = other.header_row
            self.data_start_row = other.data_start_row
            self.row_offset = other.row_offset

    def has_changes(self) -> bool:
        return any([
            self.merged_cols_filled, self.summary_rows_marked,
            self.hidden_rows_marked, self.hidden_cols_names,
            self.empty_cols_removed, self.empty_rows_removed,
            self.int_cols_fixed, self.has_auto_filter, self.warnings,
        ])

    def to_llm_text(self) -> str:
        """生成注入 LLM 上下文的简洁报告。"""
        parts: list[str] = []
        if self.merged_cols_filled:
            parts.append(f"合并单元格精确填充（{self.merged_cols_filled}个）")
        if self.summary_rows_marked:
            parts.append(f"标记合计行（{self.summary_rows_marked}行）")
        if self.hidden_rows_marked:
            parts.append(f"标记隐藏行（{self.hidden_rows_marked}行）")
        if self.empty_cols_removed:
            parts.append(f"删除空列（{self.empty_cols_removed}列）")
        if self.empty_rows_removed:
            parts.append(f"删除空行（{self.empty_rows_removed}行）")
        if self.int_cols_fixed:
            parts.append(f"整数修复（{self.int_cols_fixed}列）")
        if not parts and not self.has_auto_filter:
            return ""

        lines: list[str] = []
        if parts:
            lines.append(f"[数据清洗] {'| '.join(parts)}")
        lines.append(
            f"清洗前: {self.original_shape[0]}行×{self.original_shape[1]}列 "
            f"→ 清洗后: {self.final_shape[0]}行×{self.final_shape[1]}列"
        )
        if self.hidden_cols_names:
            lines.append(f"⚠ 隐藏列: {self.hidden_cols_names}（数据保留，建议按需排除）")
        if self.hidden_rows_marked:
            lines.append("⚠ 建议查询时加: WHERE _is_hidden = false")
        if self.has_auto_filter:
            lines.append("注意: 数据包含自动筛选，已读取全部行（非筛选结果）")
        for w in self.warnings:
            lines.append(f"⚠ {w}")
        return "\n".join(lines)


def write_cleaning_report(cache_path: str, report: CleaningReport) -> None:
    """将清洗报告写入 .meta.json（与 Parquet 缓存同目录）。"""
    if not report.has_changes():
        return
    meta_path = cache_path.replace(".parquet", ".meta.json")
    data = asdict(report)
    # tuple → list for JSON serialization
    data["original_shape"] = list(data["original_shape"])
    data["final_shape"] = list(data["final_shape"])
    Path(meta_path).write_text(json.dumps(data, ensure_ascii=False))


def read_cleaning_report(cache_path: str) -> CleaningReport | None:
    """读取 .meta.json，不存在时返回 None。"""
    meta_path = cache_path.replace(".parquet", ".meta.json")
    p = Path(meta_path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        data["original_shape"] = tuple(data["original_shape"])
        data["final_shape"] = tuple(data["final_shape"])
        return CleaningReport(**data)
    except Exception:
        return None
