"""
统一查询引擎 — Filter 校验 / 时间范围提取 / ORM 查询辅助函数。

从 erp_unified_query.py 拆分出来，降低主文件行数。
所有函数操作的是 erp_unified_schema 定义的类型（ValidatedFilter / TimeRange）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from services.kuaimai.erp_local_helpers import CN_TZ
from services.kuaimai.erp_unified_schema import (
    COLUMN_WHITELIST,
    OP_COMPAT,
    TIME_COLUMNS,
    VALID_TIME_COLS,
    TimeRange,
    ValidatedFilter,
    get_column_whitelist,
    DOC_TYPE_DEFAULT_TIME_COL,
    DOC_TYPE_TIME_REQUIRED,
    _DOCUMENT_ITEM_DOC_TYPES,
)
from utils.time_context import DateRange, now_cn

if False:  # TYPE_CHECKING
    from utils.time_context import RequestContext


def validate_filters(
    filters: list[dict],
    doc_type: str | None = None,
) -> tuple[list[ValidatedFilter], str | None]:
    """校验 filters 合法性，返回 (validated_list, error_msg)。

    doc_type 可选：传入时用对应表的列白名单校验，None 时走全局白名单（向后兼容）。
    """
    whitelist = get_column_whitelist(doc_type)
    result: list[ValidatedFilter] = []
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            continue

        field = f.get("field", "")
        op = f.get("op", "")
        value = f.get("value")

        meta = whitelist.get(field)
        if not meta:
            available = ", ".join(sorted(whitelist.keys()))
            return [], (
                f"filters[{i}]: 字段 '{field}' 不在白名单中。可用字段: {available}"
            )

        compat = OP_COMPAT.get(meta.col_type, set())
        if op not in compat:
            return [], (
                f"filters[{i}]: 字段 '{field}'(类型={meta.col_type}) "
                f"不支持 op='{op}'。可用: {', '.join(sorted(compat))}"
            )

        if op == "between" and (not isinstance(value, list) or len(value) != 2):
            return [], f"filters[{i}]: op='between' 的 value 必须是 [min, max] 数组"

        if op == "in" and isinstance(value, list) and len(value) == 0:
            continue

        value = coerce_value(value, meta.col_type)
        result.append(ValidatedFilter(field=field, op=op, value=value, col_type=meta.col_type))

    return result, None


def coerce_value(value: Any, col_type: str) -> Any:
    """尝试将 value 转为目标类型"""
    if value is None:
        return value
    if col_type == "integer" and isinstance(value, str):
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
    elif col_type == "numeric" and isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
    elif col_type == "timestamp" and isinstance(value, str):
        if "+" not in value and "Z" not in value and value.count(":") >= 1:
            value = value + "+08:00"
    return value


def extract_time_range(
    filters: list[ValidatedFilter],
    time_type: str | None,
    request_ctx: Optional[RequestContext],
    mode: str,
    doc_type: str | None = None,
) -> TimeRange | None:
    """从 filters 中提取时间范围。

    doc_type 可选：新表时间列逻辑不同（如 stock 无时间维度），
    不要求 time_range 时返回 None。
    """
    now = request_ctx.now if request_ctx else now_cn()

    # 新表：用 DOC_TYPE_DEFAULT_TIME_COL 确定默认时间列
    default_time_col = DOC_TYPE_DEFAULT_TIME_COL.get(doc_type or "")
    if default_time_col:
        time_col = time_type if time_type in VALID_TIME_COLS else default_time_col
    else:
        time_col = time_type if time_type in VALID_TIME_COLS else "doc_created_at"

    start_val: str | None = None
    end_val: str | None = None
    detected_col: str | None = None

    for f in filters:
        if f.field in TIME_COLUMNS:
            if detected_col is None:
                detected_col = f.field
            if f.field == detected_col:
                if f.op in ("gte", "gt"):
                    start_val = str(f.value)
                elif f.op in ("lt", "lte"):
                    end_val = str(f.value)

    if detected_col and detected_col in VALID_TIME_COLS:
        time_col = detected_col

    # 半开区间：结束时间用次日 00:00:00（lt），覆盖完整一天
    _next_day_start = (
        (now + timedelta(days=1))
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )

    if start_val is None and end_val is None:
        # 新表不强制时间范围时，跳过时间过滤
        time_required = DOC_TYPE_TIME_REQUIRED.get(doc_type or "", True)
        if not time_required and doc_type and doc_type not in _DOCUMENT_ITEM_DOC_TYPES:
            return None
        if mode == "detail":
            s_dt = now - timedelta(days=30)
        else:
            s_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        e_dt = _next_day_start
        start_val, end_val = s_dt.isoformat(), e_dt.isoformat()
    elif start_val and not end_val:
        end_val = _next_day_start.isoformat()
    elif end_val and not start_val:
        start_val = (now - timedelta(days=30)).isoformat()

    try:
        s_dt = datetime.fromisoformat(start_val.replace("Z", "+00:00"))
        e_dt = datetime.fromisoformat(end_val.replace("Z", "+00:00"))
        if s_dt.tzinfo is None:
            s_dt = s_dt.replace(tzinfo=CN_TZ)
        if e_dt.tzinfo is None:
            e_dt = e_dt.replace(tzinfo=CN_TZ)
    except (ValueError, AttributeError):
        s_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        e_dt = _next_day_start

    # 兜底：start == end 时扩展 1 分钟，防止 DateRange.custom 抛 ValueError
    if s_dt >= e_dt:
        e_dt = s_dt + timedelta(minutes=1)

    # 统一用 strftime 格式化，保证 PG 和 DuckDB 都能解析
    start_val = s_dt.strftime("%Y-%m-%d %H:%M:%S%z")
    end_val = e_dt.strftime("%Y-%m-%d %H:%M:%S%z")

    date_range = DateRange.custom(s_dt, e_dt, reference=now)
    label = f"{s_dt.strftime('%m-%d %H:%M')} ~ {e_dt.strftime('%m-%d %H:%M')}"

    return TimeRange(
        start_iso=start_val, end_iso=end_val,
        time_col=time_col, date_range=date_range, label=label,
    )


def split_named_params(
    non_time: list[ValidatedFilter],
) -> tuple[str | None, str | None, str | None, str | None, list[dict]]:
    """分离 RPC 命名参数（shop/platform/supplier/warehouse）和 DSL filters"""
    p_shop = p_platform = p_supplier = p_warehouse = None
    dsl: list[dict] = []

    for f in non_time:
        # doc_type 由 RPC p_doc_type 参数处理，不重复进入 DSL
        if f.field == "doc_type":
            continue
        elif f.field == "shop_name" and f.op in ("eq", "like"):
            p_shop = str(f.value).replace("%", "")
        elif f.field == "platform" and f.op == "eq":
            p_platform = str(f.value)
        elif f.field == "supplier_name" and f.op in ("eq", "like"):
            p_supplier = str(f.value).replace("%", "")
        elif f.field == "warehouse_name" and f.op in ("eq", "like"):
            p_warehouse = str(f.value).replace("%", "")
        else:
            dsl.append({"field": f.field, "op": f.op, "value": f.value})

    return p_shop, p_platform, p_supplier, p_warehouse, dsl


def query_table(
    db: Any, table: str, doc_type: str, filters: list[ValidatedFilter],
    tr: TimeRange, select_cols: str, sort_by: str, sort_dir: str,
    limit: int, org_id: str | None,
) -> list[dict]:
    """构建并执行单表 ORM 查询"""
    q = (
        db.table(table).select(select_cols)
        .eq("doc_type", doc_type)
        .gte(tr.time_col, tr.start_iso)
        .lt(tr.time_col, tr.end_iso)
    )
    if org_id:
        q = q.eq("org_id", org_id)
    else:
        q = q.is_("org_id", "null")
    q = apply_orm_filters(q, filters)
    q = q.order(sort_by, desc=(sort_dir == "desc")).limit(limit)
    return q.execute().data or []


def apply_orm_filters(q: Any, filters: list[ValidatedFilter]) -> Any:
    """ValidatedFilter 列表 → Supabase ORM 链式调用"""
    for f in filters:
        val = f.value
        if f.op == "eq":
            q = q.eq(f.field, val)
        elif f.op == "ne":
            q = q.neq(f.field, val)
        elif f.op == "gt":
            q = q.gt(f.field, val)
        elif f.op == "gte":
            q = q.gte(f.field, val)
        elif f.op == "lt":
            q = q.lt(f.field, val)
        elif f.op == "lte":
            q = q.lte(f.field, val)
        elif f.op == "like":
            q = q.ilike(f.field, str(val))
        elif f.op == "in" and isinstance(val, list) and val:
            q = q.in_(f.field, val)
        elif f.op == "not_in" and isinstance(val, list) and val:
            q = q.not_.in_(f.field, val)
        elif f.op == "is_null":
            if val is True or val == "true" or val == 1:
                q = q.is_(f.field, "null")
            else:
                q = q.not_.is_(f.field, "null")
        elif f.op == "between" and isinstance(val, list) and len(val) == 2:
            q = q.gte(f.field, val[0]).lte(f.field, val[1])
    return q


def need_archive(tr: TimeRange) -> bool:
    """判断是否需要查冷表（起始时间超过90天前）"""
    try:
        s_dt = datetime.fromisoformat(tr.start_iso.replace("Z", "+00:00"))
        return s_dt < now_cn() - timedelta(days=90)
    except (ValueError, AttributeError):
        return False
