"""清洗策略的私有提取辅助。

5 个 _strategy_xxx 函数 — 从 CleaningStrategy 安全提取各动作所需配置。
strategy=None 时返回空集合/字典（向后兼容）。
"""
from __future__ import annotations

from typing import Any


def _strategy_summary_rows(strategy: Any) -> list[int]:
    """从 strategy 安全提取 summary_rows，无策略时返回 []。"""
    if strategy is None:
        return []
    return list(getattr(strategy, "summary_rows", []) or [])


def _strategy_id_columns(strategy: Any) -> set[str]:
    """从 strategy 安全提取 id_columns。"""
    if strategy is None:
        return set()
    return set(getattr(strategy, "id_columns", []) or [])


def _strategy_mixed_handling(strategy: Any) -> dict[str, Any]:
    """从 strategy 提取混合类型策略（col_letter → MixedTypeAction）。"""
    if strategy is None:
        return {}
    return {h.col_letter: h for h in (getattr(strategy, "mixed_type_handling", []) or [])}


def _strategy_preserve_rows(strategy: Any) -> set[int]:
    """从 strategy 提取需保留的空行号（Excel 1-indexed）。"""
    if strategy is None:
        return set()
    return {p.row for p in (getattr(strategy, "preserve_empty_rows", []) or [])}


def _strategy_merge_actions(strategy: Any) -> dict[str, Any]:
    """从 strategy 提取合并单元格动作映射（range_str → MergedCellAction）。"""
    if strategy is None:
        return {}
    return {a.range_str: a for a in (getattr(strategy, "merged_cell_actions", []) or [])}
