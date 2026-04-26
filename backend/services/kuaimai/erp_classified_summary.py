"""订单分类统计（RPC + 分类引擎）

从 erp_unified_query.py 拆出，保持引擎文件 < 500 行。
入口：classified_summary()，由 UnifiedQueryEngine._summary 调用。
"""

from __future__ import annotations

import json as _json
from typing import Any, Optional

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_unified_schema import (
    GROUP_BY_MAP,
    TIME_COLUMNS,
    TimeRange,
    ValidatedFilter,
    fmt_classified_grouped,
)
from services.kuaimai.erp_unified_filters import (
    split_named_params as _split_named_params,
)
from utils.time_context import RequestContext, format_time_header


# ── 列定义 ──

CLASSIFIED_FLAT_COLUMNS = [
    ColumnMeta("total_orders", "integer", "总订单数"),
    ColumnMeta("total_amount", "numeric", "总金额"),
    ColumnMeta("valid_orders", "integer", "有效订单数"),
    ColumnMeta("valid_amount", "numeric", "有效金额"),
]

CLASSIFIED_GROUPED_COLUMNS = [
    ColumnMeta("group_key", "text", "分组"),
    ColumnMeta("total_orders", "integer", "总订单数"),
    ColumnMeta("total_amount", "numeric", "总金额"),
    ColumnMeta("valid_orders", "integer", "有效订单数"),
    ColumnMeta("valid_amount", "numeric", "有效金额"),
]


# ── 入口 ──

async def classified_summary(
    db: Any,
    org_id: str | None,
    filters: list[ValidatedFilter],
    tr: TimeRange,
    request_ctx: Optional[RequestContext],
    group_by: list[str] | None = None,
    include_invalid: bool = False,
) -> ToolOutput | None:
    """订单分类统计：走 erp_order_stats_grouped RPC + 分类引擎。

    返回 ToolOutput 或 None（回退到普通统计）。
    """
    from services.kuaimai.order_classifier import OrderClassifier

    non_time = [f for f in filters if f.field not in TIME_COLUMNS]
    p_shop, p_platform, p_supplier, p_warehouse, dsl = _split_named_params(non_time)

    # erp_order_stats_grouped 只接受 p_filters，把命名参数也转为 DSL
    if p_shop:
        dsl.append({"field": "shop_name", "op": "like", "value": f"%{p_shop}%"})
    if p_platform:
        dsl.append({"field": "platform", "op": "eq", "value": p_platform})
    if p_supplier:
        dsl.append({"field": "supplier_name", "op": "like", "value": f"%{p_supplier}%"})
    if p_warehouse:
        dsl.append({"field": "warehouse_name", "op": "like", "value": f"%{p_warehouse}%"})

    rpc_group = (
        GROUP_BY_MAP.get(group_by[0], group_by[0]) if group_by else None
    )

    params: dict[str, Any] = {
        "p_org_id": org_id,
        "p_start": tr.start_iso,
        "p_end": tr.end_iso,
        "p_time_col": tr.time_col,
        "p_filters": _json.dumps(dsl) if dsl else None,
        "p_group_by": rpc_group,
    }

    # ── 诊断：直查 DB 对比 RPC ──
    try:
        direct = db.table("erp_document_items").select(
            "doc_id", count="exact",
        ).eq("org_id", org_id).eq(
            "doc_type", "order",
        ).gte(tr.time_col, tr.start_iso).lt(
            tr.time_col, tr.end_iso,
        ).execute()
        direct_count = direct.count if direct.count else len(direct.data)
        logger.info(f"诊断直查 | table row count={direct_count} | time_col={tr.time_col} | {tr.start_iso}~{tr.end_iso}")
    except Exception as diag_err:
        logger.warning(f"诊断直查失败 | error={diag_err}")

    try:
        result = db.rpc("erp_order_stats_grouped", params).execute()
        raw_rows = result.data
    except Exception as e:
        logger.warning(f"分类统计 RPC 失败，回退原逻辑 | error={e}")
        return None

    if not raw_rows or raw_rows == []:
        return None

    # ── 诊断日志：RPC 返回的原始聚合数据 ──
    if not isinstance(raw_rows, list) or not raw_rows or not isinstance(raw_rows[0], dict):
        return None
    rpc_total_docs = sum(int(r.get("doc_count", 0)) for r in raw_rows)
    rpc_row_count = len(raw_rows)
    logger.info(
        f"分类统计 RPC 返回 | rows={rpc_row_count} | "
        f"sum(doc_count)={rpc_total_docs} | "
        f"group_by={rpc_group} | "
        f"time={tr.start_iso}~{tr.end_iso} | "
        f"time_col={tr.time_col}"
    )

    try:
        classifier = OrderClassifier.for_org(db, org_id)
    except Exception as e:
        logger.warning(f"分类引擎加载异常，回退原逻辑 | error={e}")
        return None

    time_header = format_time_header(
        ctx=request_ctx, range_=tr.date_range, kind="统计区间",
    )

    if rpc_group is None:
        output = _build_flat(classifier, raw_rows, time_header, tr, include_invalid)
    else:
        output = _build_grouped(
            classifier, raw_rows, rpc_group, time_header, tr, include_invalid,
        )

    # ── 诊断日志：分类引擎处理后的数据（扁平化结构） ──
    if output and output.data:
        if rpc_group is None:
            d = output.data[0] if output.data else {}
            classified_total = d.get("total_orders", "?")
            classified_valid = d.get("valid_orders", "?")
        else:
            classified_total = sum(
                d.get("total_orders", 0) for d in output.data
            )
            classified_valid = sum(
                d.get("valid_orders", 0) for d in output.data
            )
        logger.info(
            f"分类引擎输出 | rpc_sum={rpc_total_docs} | "
            f"classified_total={classified_total} | "
            f"classified_valid={classified_valid} | "
            f"match={'✅' if rpc_total_docs == classified_total else '❌ MISMATCH'}"
        )

    return output


# ── 构建 ToolOutput ──

def _build_flat(
    classifier: Any, raw_rows: list[dict],
    time_header: str, tr: TimeRange, include_invalid: bool,
) -> ToolOutput | None:
    """无分组：整体分类 → ToolOutput（扁平化数据）"""
    try:
        cr = classifier.classify(raw_rows)
    except Exception as e:
        logger.warning(f"分类引擎异常，回退原逻辑 | error={e}")
        return None
    body = cr.to_display_text(show_recommendation=not include_invalid)
    summary_text = f"{time_header}\n\n{body}" if time_header else body
    flat_row = {
        "total_orders": cr.total.get("doc_count", 0),
        "total_amount": cr.total.get("total_amount", 0),
        "valid_orders": cr.valid.get("doc_count", 0),
        "valid_amount": cr.valid.get("total_amount", 0),
    }
    for cat in cr.categories_list:
        name = cat.get("name", "未知")
        flat_row[f"{name}单数"] = cat.get("doc_count", 0)
    return ToolOutput(
        summary=summary_text,
        format=OutputFormat.TABLE,
        source="erp",
        columns=CLASSIFIED_FLAT_COLUMNS,
        data=[flat_row],
        metadata={
            "doc_type": "order",
            "time_range": tr.label,
            "time_column": tr.time_col,
        },
    )


def _build_grouped(
    classifier: Any, raw_rows: list[dict],
    rpc_group: str, time_header: str, tr: TimeRange,
    include_invalid: bool,
) -> ToolOutput | None:
    """有分组：每组独立分类 → ToolOutput（扁平化数据）"""
    try:
        grouped = classifier.classify_grouped(raw_rows)
    except Exception as e:
        logger.warning(f"分组分类引擎异常，回退原逻辑 | error={e}")
        return None
    body = fmt_classified_grouped(
        grouped, rpc_group, tr.label,
        show_recommendation=not include_invalid,
    )
    summary_text = f"{time_header}\n\n{body}" if time_header else body
    data_list = [
        {
            "group_key": key,
            "total_orders": cr.total.get("doc_count", 0),
            "total_amount": cr.total.get("total_amount", 0),
            "valid_orders": cr.valid.get("doc_count", 0),
            "valid_amount": cr.valid.get("total_amount", 0),
        }
        for key, cr in grouped.items()
    ]
    return ToolOutput(
        summary=summary_text,
        format=OutputFormat.TABLE,
        source="erp",
        columns=CLASSIFIED_GROUPED_COLUMNS,
        data=data_list,
        metadata={
            "doc_type": "order",
            "group_by": rpc_group,
            "time_range": tr.label,
            "time_column": tr.time_col,
        },
    )
