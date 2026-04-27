"""查询预检防御层（Query Preflight Guard）

导出模式的数据量门卫：用户请求的导出行数超过安全上限时拒绝执行。
只管导出量，不管底层数据量。

设计文档：docs/document/TECH_查询预检防御层.md
"""

from __future__ import annotations

from dataclasses import dataclass


# 导出安全上限（DuckDB 远程扫描实测上限）
EXPORT_ROW_LIMIT = 30_000


@dataclass(frozen=True)
class PreflightResult:
    """预检结果"""
    ok: bool
    reject_reason: str = ""
    suggestions: tuple[str, ...] = ()


def preflight_check(mode: str, limit: int) -> PreflightResult:
    """预检门卫：只管导出量。

    summary 模式：不拦（RPC 在 PG 侧聚合）。
    export 模式：limit > EXPORT_ROW_LIMIT 才拦。
    """
    if mode != "export":
        return PreflightResult(ok=True)

    if limit > EXPORT_ROW_LIMIT:
        return PreflightResult(
            ok=False,
            reject_reason=(
                f"导出行数过大（请求 {limit:,} 行，上限 {EXPORT_ROW_LIMIT:,} 行）"
            ),
            suggestions=(
                "缩小时间范围（如改为最近 7 天）",
                "添加平台/店铺/商品编码等过滤条件",
                "减少 limit（如 limit=1000）",
                "改用 summary 模式先查看统计汇总",
            ),
        )

    return PreflightResult(ok=True)
