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
    """完整的 .meta.json 结构。

    Phase 0 兼容期：v1 字段全部保留，新增 v2 字段。
    Phase 5/6 才删除 v1 已废弃字段（prescan/confidence/processed_by/status 三值）。
    """

    # ── v1 字段（Phase 5/6 前保留）──
    version: str = "1.0"
    status: str = "pass"                  # v1: pass | warning | fail；v2 后只用 success
    source_file: str = ""
    processed_at: str = ""
    last_accessed_at: str = ""
    processed_by: str = "L1"              # v1: L1 | L2；v2 后废弃
    summary: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    sample: dict[str, list[dict]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    formulas: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    merged_cells: list[dict[str, Any]] = field(default_factory=list)
    raw_preserved: bool = True            # 原始结构是否被保留（未自动 ffill）
    # V3：删 grain 字段（电商假设的"事实表粒度"层）。表角色识别 → AI 输出 ai_decision.table_role
    prescan: dict[str, Any] = field(default_factory=dict)  # v1: PrescanResult；v2 后废弃
    cleaning: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0               # v1: 1.0/0.8/0.5；v2 后废弃

    # ── v2 新增字段（Phase 0 起开始写入）──
    ai_decision: dict[str, Any] = field(default_factory=dict)
    # asdict(AIDecision) — AI 一次裁决的完整产出

    cleaning_strategy: dict[str, Any] = field(default_factory=dict)
    # asdict(CleaningStrategy) — 派生自 ai_decision 的清洗策略

    evidence_summary: dict[str, Any] = field(default_factory=dict)
    # 关键证据摘要（不存完整 EvidencePool 以节省空间）
    # 含: suspicious_row_count / region_count / sheet_count / formula_total_count 等

    related_files: list[dict[str, Any]] = field(default_factory=list)
    # 从 session_files.json 派生 — 当前文件与其他文件的 JOIN/UNION 关联

    xml_view: str = ""
    # 持久化的 XML 渲染结果（缓存命中时直接返回，无需重建）

    schema_fingerprint: str = ""
    # V2.2 #16: EvidencePool 结构指纹（列字母+表头+类型分布），同模板月度报表可复用 AIDecision

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
