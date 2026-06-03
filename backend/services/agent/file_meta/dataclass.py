"""FileMeta 数据类 + 共享常量 + 列号工具。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# ── 常量 ──
_SAMPLE_ROWS = 5           # 兼容保留（其他模块可能引用）
_SAMPLE_HEAD = 4           # 开头取几行
_SAMPLE_MIDDLE = 2         # 中段取几行（解决 lost-in-the-middle）
_SAMPLE_TAIL = 4           # 末尾取几行
_SAMPLE_BOUNDARY_MAX = 2   # 边界补充上限（来自 prescan anomalies）
_CATEGORY_THRESHOLD = 20   # unique ≤ 此值视为分类列
_MAX_ISSUES = 50           # issues 最多记录条数


def _col_index_to_letter(idx: int) -> str:
    """0-indexed 列索引 → Excel 列字母（0=A, 1=B, ..., 25=Z, 26=AA）。"""
    result = ""
    n = idx
    while True:
        result = chr(ord("A") + n % 26) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


@dataclass
class FileMeta:
    """完整的 .meta.json 结构。"""

    version: str = "1.0"
    status: str = "pass"                  # pass | warning | fail
    source_file: str = ""
    processed_at: str = ""
    last_accessed_at: str = ""
    processed_by: str = "L1"              # L1 | L2
    summary: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    sample: dict[str, list[dict]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    formulas: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    merged_cells: list[dict[str, Any]] = field(default_factory=list)
    raw_preserved: bool = True    # 原始结构是否被保留（未自动 ffill）
    grain: dict[str, Any] = field(default_factory=dict)    # 粒度检测结果
    prescan: dict[str, Any] = field(default_factory=dict)  # AI 坐标预探测结论
    cleaning: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
