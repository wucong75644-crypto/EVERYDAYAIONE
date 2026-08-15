"""
分布分析引擎 — 按数值字段动态分桶。

调用 RPC erp_distribution_query，返回各区间计数和总量。
用户场景："订单金额分布" / "客单价区间" / "数量分布"

设计文档: docs/document/TECH_ERP查询架构重构.md §5.9
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_unified_schema import (
    DOC_TYPE_CN,
    DOC_TYPE_TABLE,
    TimeRange,
    ValidatedFilter,
)


# ============================================================
# 预定义分桶规则
# ============================================================

BUCKET_RULES: dict[str, list[float]] = {
    "amount": [0, 50, 100, 200, 500, 1000, 5000],
    "quantity": [0, 1, 5, 10, 50, 100],
    "order_amount": [0, 50, 100, 200, 500, 1000, 5000],
    "order_qty": [0, 1, 5, 10, 50, 100],
    "purchase_amount": [0, 100, 500, 1000, 5000, 10000],
    "total_stock": [0, 10, 50, 100, 500, 1000],
    "available_stock": [0, 10, 50, 100, 500, 1000],
    "cost": [0, 10, 50, 100, 500, 1000],
}

DEFAULT_BUCKETS = [0, 100, 500, 1000, 5000]

_DISTRIBUTION_COLUMNS = [
    ColumnMeta("bucket", "text", "区间"),
    ColumnMeta("count", "integer", "数量"),
    ColumnMeta("bucket_total", "numeric", "区间总量"),
]

_FIELD_CN = {
    "amount": "金额",
    "quantity": "数量",
    "order_amount": "订单金额",
    "order_qty": "订单数量",
    "purchase_amount": "采购金额",
    "total_stock": "总库存",
    "available_stock": "可用库存",
    "cost": "成本",
}


# ============================================================
# 分布分析查询
# ============================================================

async def query_distribution(
    db: Any,
    org_id: str | None,
    doc_type: str,
    filters: list[ValidatedFilter] | None = None,
    tr: TimeRange | None = None,
    metrics: list[str] | None = None,
    limit: int = 20,
) -> ToolOutput:
    """分布直方图——按数值区间分桶。

    调用 RPC erp_distribution_query，返回各区间计数。
    """
    field = metrics[0] if metrics else "amount"
    buckets = BUCKET_RULES.get(field, DEFAULT_BUCKETS)

    table = DOC_TYPE_TABLE.get(doc_type, "erp_document_items")
    if table == "erp_product_daily_stats":
        time_col = "stat_date"
    elif table == "erp_stock_status":
        time_col = "stock_modified_time"
    else:
        time_col = "doc_created_at"

    rpc_params: dict[str, Any] = {
        "p_org_id": org_id,
        "p_table": table,
        "p_field": field,
        "p_buckets": buckets,
        "p_time_col": time_col,
    }

    if table in ("erp_document_items", "erp_document_items_archive"):
        rpc_params["p_doc_type"] = doc_type

    if tr:
        rpc_params["p_start"] = tr.start_iso
        rpc_params["p_end"] = tr.end_iso

    try:
        result = db.rpc("erp_distribution_query", rpc_params).execute()
        rows = result.data or []
    except Exception as e:
        logger.error(f"Distribution query failed | doc_type={doc_type} "
                     f"field={field} | {e}")
        return ToolOutput(
            summary=f"分布查询失败: {e}",
            status=OutputStatus.ERROR,
            error_message=str(e),
            metadata={"query_type": "distribution", "field": field},
        )

    # RPC 返回 JSONB，Supabase 客户端自动解析
    if isinstance(rows, str):
        rows = json.loads(rows)
    if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], list):
        rows = rows[0]

    for r in rows:
        r.pop("sort_key", None)

    type_name = DOC_TYPE_CN.get(doc_type, doc_type)
    return ToolOutput(
        summary=format_distribution_summary(rows, field, type_name),
        status=OutputStatus.OK if rows else OutputStatus.EMPTY,
        format=OutputFormat.TABLE,
        data=rows,
        columns=_DISTRIBUTION_COLUMNS,
        metadata={
            "query_type": "distribution",
            "field": field,
            "doc_type": doc_type,
            "buckets": buckets,
        },
    )


# ============================================================
# 格式化
# ============================================================

def format_distribution_summary(
    rows: list[dict], field: str, type_name: str,
) -> str:
    """分布分析人类可读摘要。"""
    field_cn = _FIELD_CN.get(field, field)

    if not rows:
        return f"{type_name}{field_cn}分布：无数据"

    total_count = sum(r.get("count", 0) for r in rows)
    parts = [f"{type_name}{field_cn}分布（共 {total_count} 条）："]

    for r in rows:
        bucket = r.get("bucket", "")
        count = r.get("count", 0)
        pct = round(count / total_count * 100, 1) if total_count else 0
        parts.append(f"  {bucket}：{count} 条（{pct}%）")

    return "\n".join(parts)
