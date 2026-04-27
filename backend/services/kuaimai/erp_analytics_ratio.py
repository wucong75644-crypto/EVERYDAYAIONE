"""占比/排名/ABC 分类分析。

从 erp_unified_query.py 拆出，与 trend/cross/alert/distribution 保持一致的独立模块模式。
设计文档: docs/document/TECH_ERP查询架构重构.md §5.5
"""

from __future__ import annotations

from typing import Any

from services.kuaimai.erp_unified_schema import (
    DOC_TYPE_CN, _FIELD_LABEL_CN,
)
from services.agent.tool_output import ColumnMeta, OutputFormat, OutputStatus, ToolOutput


def compute_ratio(
    raw_data: list[dict],
    raw_columns: list[ColumnMeta] | None,
    doc_type: str,
    metrics: list[str] | None,
    time_label: str = "",
) -> ToolOutput:
    """在已有的分组聚合数据上计算占比 + 累计占比 + ABC 分类。

    Args:
        raw_data: _summary() 返回的分组聚合行（必须有 group_key + 数值列）
        raw_columns: _summary() 返回的列元信息
        doc_type: 单据类型（用于摘要文案）
        metrics: 用户关注的指标（决定用哪个数值列计算占比）
        time_label: 时间范围文本（用于 metadata）

    Returns:
        ToolOutput，data 中每行追加 ratio / cumulative_ratio / abc_class
    """
    if not raw_data:
        return ToolOutput(
            summary="无数据可做占比分析",
            source="erp", status=OutputStatus.EMPTY,
            metadata={"query_type": "ratio", "doc_type": doc_type},
        )

    # 确定计算占比的指标列
    metric_col = "total_amount"
    if metrics:
        col_map = {
            "count": "doc_count", "amount": "total_amount",
            "qty": "total_qty", "cost": "total_cost",
        }
        metric_col = col_map.get(metrics[0], metrics[0])
        if metric_col not in raw_data[0]:
            metric_col = "total_amount"

    # 计算占比
    total = sum(float(row.get(metric_col, 0) or 0) for row in raw_data)
    sorted_data = sorted(
        raw_data,
        key=lambda x: float(x.get(metric_col, 0) or 0),
        reverse=True,
    )
    cumulative = 0.0
    for row in sorted_data:
        val = float(row.get(metric_col, 0) or 0)
        row["ratio"] = round(val / total * 100, 1) if total else 0
        cumulative += val
        row["cumulative_ratio"] = round(cumulative / total * 100, 1) if total else 0
        if row["cumulative_ratio"] <= 80:
            row["abc_class"] = "A"
        elif row["cumulative_ratio"] <= 95:
            row["abc_class"] = "B"
        else:
            row["abc_class"] = "C"

    # 构建摘要
    a_count = sum(1 for r in sorted_data if r["abc_class"] == "A")
    b_count = sum(1 for r in sorted_data if r["abc_class"] == "B")
    c_count = sum(1 for r in sorted_data if r["abc_class"] == "C")
    type_name = DOC_TYPE_CN.get(doc_type, doc_type)
    summary = (
        f"{type_name}占比分析（共 {len(sorted_data)} 项）\n"
        f"A类（累计≤80%）：{a_count} 项 | "
        f"B类（80%-95%）：{b_count} 项 | "
        f"C类（>95%）：{c_count} 项\n"
        f"总计{_FIELD_LABEL_CN.get(metric_col, metric_col)}：{total:,.2f}"
    )

    ratio_cols = list(raw_columns or [])
    ratio_cols.extend([
        ColumnMeta("ratio", "numeric", "占比(%)"),
        ColumnMeta("cumulative_ratio", "numeric", "累计占比(%)"),
        ColumnMeta("abc_class", "text", "ABC分类"),
    ])

    return ToolOutput(
        summary=summary,
        format=OutputFormat.TABLE,
        source="erp",
        data=sorted_data,
        columns=ratio_cols,
        metadata={
            "query_type": "ratio",
            "doc_type": doc_type,
            "metric_col": metric_col,
            "total": total,
            "time_range": time_label,
        },
    )
