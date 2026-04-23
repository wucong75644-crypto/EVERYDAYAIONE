"""
DuckDB 导出辅助函数 — SQL 构建、PII 脱敏、路径解析、预览。

从 erp_unified_query.py 拆出，保持引擎文件 < 500 行。
设计文档: docs/document/TECH_DuckDB导出引擎.md
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from pathlib import Path

from services.kuaimai.erp_unified_schema import (
    TIME_COLUMNS, TimeRange, ValidatedFilter, PLATFORM_CN, DOC_TYPE_CN,
    _FIELD_LABEL_CN,
)


def _to_duckdb_timestamp(iso_str: str) -> str:
    """ISO 时间字符串 → DuckDB 兼容格式 YYYY-MM-DD HH:MM:SS。

    DuckDB 不接受带时区的 ISO 格式（如 2026-04-19T00:00+08:00），
    Supabase RPC 可以。此函数仅供 DuckDB SQL 使用。
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso_str


# ── PII 脱敏 SQL 表达式 ──────────────────────────────


_PII_SQL_MAP: dict[str, str] = {
    "receiver_name": (
        "CASE WHEN receiver_name IS NOT NULL AND length(receiver_name) >= 2 "
        "THEN substr(receiver_name, 1, 1) || repeat('*', length(receiver_name) - 1) "
        "ELSE receiver_name END AS receiver_name"
    ),
    "receiver_mobile": (
        "CASE WHEN receiver_mobile IS NOT NULL AND length(receiver_mobile) >= 7 "
        "THEN substr(receiver_mobile, 1, 3) || '****' || substr(receiver_mobile, -4) "
        "ELSE receiver_mobile END AS receiver_mobile"
    ),
    "receiver_phone": (
        "CASE WHEN receiver_phone IS NOT NULL AND length(receiver_phone) >= 7 "
        "THEN substr(receiver_phone, 1, 3) || '****' || substr(receiver_phone, -4) "
        "ELSE receiver_phone END AS receiver_phone"
    ),
    "receiver_address": (
        "CASE WHEN receiver_address IS NOT NULL AND length(receiver_address) >= 6 "
        "THEN substr(receiver_address, 1, 6) || '****' "
        "ELSE receiver_address END AS receiver_address"
    ),
}


_TIMESTAMP_COLS = frozenset({
    "doc_created_at", "doc_modified_at", "pay_time",
    "consign_time", "delivery_date", "finished_at", "apply_date",
})

# platform 编码 → 中文名 SQL CASE（防止 LLM 误翻译 fxg→西瓜视频）
_PLATFORM_CASE = (
    "CASE "
    + " ".join(f"WHEN platform = '{k}' THEN '{v}'" for k, v in PLATFORM_CN.items())
    + " ELSE platform END AS platform"
)

# order_type 数字 → 中文名（逗号分隔多值，如 "2,3,14,0" → "普通/补发"）
# 快麦 type 字段是复合标记，一个订单可能同时有多个类型标记
_ORDER_TYPE_CN: dict[str, str] = {
    "0": "普通", "4": "线下", "7": "合并", "8": "拆分",
    "13": "换货", "14": "补发", "33": "分销", "99": "出库单",
}


def _build_order_type_case() -> str:
    """构建 order_type 翻译 SQL — DuckDB list_transform 拆逗号逐个翻译。

    "2,3,14,0" → "普通/补发"（未知数字跳过，已知数字翻译后用 / 连接去重）
    """
    # 用 CASE 对每个拆出的元素翻译，未知的返回 NULL（被 list_filter 过滤）
    inner_case = "CASE " + " ".join(
        f"WHEN x = '{k}' THEN '{v}'" for k, v in _ORDER_TYPE_CN.items()
    ) + " ELSE NULL END"
    return (
        f"CASE WHEN order_type IS NULL THEN NULL ELSE "
        f"array_to_string("
        f"  list_distinct("
        f"    list_filter("
        f"      list_transform(string_split(order_type, ','), x -> {inner_case}),"
        f"      x -> x IS NOT NULL"
        f"    )"
        f"  ), '/'"
        f") END AS order_type"
    )


_ORDER_TYPE_CASE = _build_order_type_case()

# doc_status / order_status 英文码 → 中文（快麦 sysStatus + 平台状态 合集）
_STATUS_CN: dict[str, str] = {
    # 快麦系统状态（sysStatus）
    "WAIT_PAY": "待付款", "WAIT_SEND": "待发货", "PART_SEND": "部分发货",
    "SEND": "已发货", "SIGN": "已签收", "FINISH": "已完成",
    "CLOSED": "已关闭", "CANCEL": "已取消",
    # 快麦统一状态 / 平台状态
    "WAIT_AUDIT": "待审核", "WAIT_SEND_GOODS": "待发货",
    "SELLER_SEND_GOODS": "已发货", "FINISHED": "已完成",
    "WAIT_BUYER_CONFIRM_GOODS": "待买家确认收货",
    "TRADE_FINISHED": "交易完成", "TRADE_CLOSED": "交易关闭",
    # 采购/收货/上架单状态（数字字符串）
    "0": "待提交", "1": "待审核", "2": "已审核", "3": "已完成",
    "4": "已作废", "5": "已关闭",
}

_STATUS_CASE = (
    "CASE "
    + " ".join(f"WHEN {{col}} = '{k}' THEN '{v}'" for k, v in _STATUS_CN.items())
    + " ELSE {col} END AS {col}"
)

# doc_type 翻译
_DOC_TYPE_CASE = (
    "CASE "
    + " ".join(f"WHEN doc_type = '{k}' THEN '{v}'" for k, v in DOC_TYPE_CN.items())
    + " ELSE doc_type END AS doc_type"
)

# 售后类型（aftersale_type 数字 → 中文）
_AFTERSALE_TYPE_CN: dict[str, str] = {
    "1": "退款", "2": "退货", "3": "补发", "4": "换货", "5": "发货前退款",
}
_AFTERSALE_TYPE_CASE = (
    "CASE "
    + " ".join(
        f"WHEN CAST(aftersale_type AS VARCHAR) = '{k}' THEN '{v}'"
        for k, v in _AFTERSALE_TYPE_CN.items()
    )
    + " ELSE CAST(aftersale_type AS VARCHAR) END AS aftersale_type"
)

# 退款状态（refund_status 数字 → 中文）
_REFUND_STATUS_CN: dict[str, str] = {
    "0": "无退款", "1": "退款中", "2": "退款成功", "3": "退款关闭",
}
_REFUND_STATUS_CASE = (
    "CASE "
    + " ".join(
        f"WHEN CAST(refund_status AS VARCHAR) = '{k}' THEN '{v}'"
        for k, v in _REFUND_STATUS_CN.items()
    )
    + " ELSE CAST(refund_status AS VARCHAR) END AS refund_status"
)

# 货物状态（good_status 数字 → 中文）
_GOOD_STATUS_CN: dict[str, str] = {
    "1": "买家未发", "2": "买家已发", "3": "卖家已收", "4": "无需退货",
}
_GOOD_STATUS_CASE = (
    "CASE "
    + " ".join(
        f"WHEN CAST(good_status AS VARCHAR) = '{k}' THEN '{v}'"
        for k, v in _GOOD_STATUS_CN.items()
    )
    + " ELSE CAST(good_status AS VARCHAR) END AS good_status"
)

# 布尔字段 0/1 → 是/否（批量生成，避免逐个手写）
_BOOL_FIELDS = (
    "is_cancel", "is_refund", "is_exception", "is_halt",
    "is_urgent", "is_scalping", "is_presell",
)
_BOOL_CASE_MAP: dict[str, str] = {
    f: (
        f"CASE WHEN {f} = 1 THEN '是' WHEN {f} = 0 THEN '否' "
        f"ELSE CAST({f} AS VARCHAR) END AS {f}"
    )
    for f in _BOOL_FIELDS
}

# 需要 SQL CASE 翻译的特殊字段
_SPECIAL_CASE_MAP: dict[str, str] = {
    "platform": _PLATFORM_CASE,
    "order_type": _ORDER_TYPE_CASE,
    "doc_type": _DOC_TYPE_CASE,
    "doc_status": _STATUS_CASE.replace("{col}", "doc_status"),
    "order_status": _STATUS_CASE.replace("{col}", "order_status"),
    "unified_status": _STATUS_CASE.replace("{col}", "unified_status"),
    "online_status": _STATUS_CASE.replace("{col}", "online_status"),
    "handler_status": _STATUS_CASE.replace("{col}", "handler_status"),
    "aftersale_type": _AFTERSALE_TYPE_CASE,
    "refund_status": _REFUND_STATUS_CASE,
    "good_status": _GOOD_STATUS_CASE,
    **_BOOL_CASE_MAP,
}


def build_pii_select(safe_fields: list[str], *, cn_header: bool = False) -> str:
    """构建 SELECT 列表：PII 脱敏 + platform 中文化 + timestamp 去时区。

    Args:
        safe_fields: 白名单字段列表
        cn_header: True 时列别名用中文（导出 Excel 场景），
                   False 时保持英文（内部 staging 场景）
    """
    cols: list[str] = []
    for f in safe_fields:
        # 中文列别名（仅 cn_header=True 时生效）
        alias = _FIELD_LABEL_CN.get(f, f) if cn_header else f

        if f in _PII_SQL_MAP:
            # PII 脱敏字段已有 AS，替换别名
            pii_expr = _PII_SQL_MAP[f]
            if cn_header and alias != f:
                # "SUBSTR(x,1,3)||'****' AS receiver_name" → "... AS 收件人"
                pii_expr = pii_expr.rsplit(" AS ", 1)[0] + f' AS "{alias}"'
            cols.append(pii_expr)
        elif f in _SPECIAL_CASE_MAP:
            case_expr = _SPECIAL_CASE_MAP[f]
            if cn_header and alias != f:
                case_expr = case_expr.rsplit(" AS ", 1)[0] + f' AS "{alias}"'
            cols.append(case_expr)
        elif f in _TIMESTAMP_COLS:
            cols.append(f'CAST({f} AS TIMESTAMP) AS "{alias}"')
        else:
            if cn_header and alias != f:
                cols.append(f'{f} AS "{alias}"')
            else:
                cols.append(f)
    return ", ".join(cols)


# ── SQL 构建 ─────────────────────────────────────────


def _sql_escape(val: object) -> str:
    """转义 SQL 字符串中的单引号，防止语法错误。"""
    return str(val).replace("'", "''")


def build_export_where(
    doc_type: str,
    filters: list[ValidatedFilter],
    tr: TimeRange,
    org_id: str | None,
) -> str:
    """构建 WHERE 子句（纯 SQL 字符串，值经过单引号转义——仅用于 DuckDB COPY）。"""
    clauses: list[str] = [
        f"doc_type = '{_sql_escape(doc_type)}'",
        f"{tr.time_col} >= '{_sql_escape(_to_duckdb_timestamp(tr.start_iso))}'",
        f"{tr.time_col} < '{_sql_escape(_to_duckdb_timestamp(tr.end_iso))}'",
    ]

    if org_id:
        clauses.append(f"org_id = '{_sql_escape(org_id)}'")
    else:
        clauses.append("org_id IS NULL")

    non_time = [f for f in filters if f.field not in TIME_COLUMNS]
    for f in non_time:
        v = _sql_escape(f.value)
        if f.op == "eq":
            clauses.append(f"{f.field} = '{v}'")
        elif f.op == "ne":
            clauses.append(f"{f.field} != '{v}'")
        elif f.op == "gt":
            clauses.append(f"{f.field} > '{v}'")
        elif f.op == "gte":
            clauses.append(f"{f.field} >= '{v}'")
        elif f.op == "lt":
            clauses.append(f"{f.field} < '{v}'")
        elif f.op == "lte":
            clauses.append(f"{f.field} <= '{v}'")
        elif f.op == "like":
            clauses.append(f"{f.field} ILIKE '{v}'")
        elif f.op == "in" and isinstance(f.value, list) and f.value:
            in_vals = ", ".join(f"'{_sql_escape(x)}'" for x in f.value)
            clauses.append(f"{f.field} IN ({in_vals})")
        elif f.op == "is_null":
            if f.value is True or f.value == "true" or f.value == 1:
                clauses.append(f"{f.field} IS NULL")
            else:
                clauses.append(f"{f.field} IS NOT NULL")
        elif f.op == "between" and isinstance(f.value, list) and len(f.value) == 2:
            clauses.append(
                f"{f.field} BETWEEN '{_sql_escape(f.value[0])}' AND '{_sql_escape(f.value[1])}'"
            )

    return " AND ".join(clauses)


# ── 路径解析 ─────────────────────────────────────────


def resolve_export_path(
    doc_type: str,
    user_id: str | None,
    org_id: str | None,
    conversation_id: str | None,
) -> tuple[Path, str, Path, str]:
    """
    构建导出 Parquet 的 staging 路径。

    返回: (staging_dir, rel_path, staging_path, filename)
    路径格式与 _write_parquet 完全一致，确保沙盒 STAGING_DIR 不变。
    """
    from core.config import get_settings
    from core.workspace import resolve_staging_dir, resolve_staging_rel_path

    settings = get_settings()
    conv_id = conversation_id or "default"
    staging_dir = Path(resolve_staging_dir(
        settings.file_workspace_root,
        user_id=user_id or "", org_id=org_id,
        conversation_id=conv_id,
    ))
    staging_dir.mkdir(parents=True, exist_ok=True)

    ts = int(_time.time())
    filename = f"local_{doc_type}_{ts}.parquet"
    staging_path = staging_dir / filename
    rel_path = resolve_staging_rel_path(conversation_id=conv_id, filename=filename)

    return staging_dir, rel_path, staging_path, filename

