"""
统一查询引擎（Filter DSL → 参数化 SQL）

替代 7 个碎片工具，统一对 erp_document_items 的查询入口。
三种模式：summary（RPC聚合）/ detail（ORM明细）/ export（Parquet导出）

设计文档: docs/document/TECH_统一查询引擎FilterDSL.md
"""

from __future__ import annotations

import json as _json
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from services.kuaimai.erp_local_helpers import CN_TZ, check_sync_health
from services.kuaimai.erp_duckdb_helpers import (
    build_export_where,
    build_pii_select,
    read_parquet_preview,
    resolve_export_path,
)
from services.kuaimai.erp_unified_schema import (
    COLUMN_WHITELIST,
    DEFAULT_DETAIL_FIELDS,
    DOC_TYPE_CN,
    EXPORT_COLUMN_NAMES,
    EXPORT_MAX,
    GROUP_BY_MAP,
    OP_COMPAT,
    TIME_COLUMNS,
    VALID_DOC_TYPES,
    VALID_TIME_COLS,
    TimeRange,
    ValidatedFilter,
    fmt_detail_rows,
    fmt_summary_grouped,
    fmt_summary_total,
    generate_field_doc,
)
from utils.time_context import (
    DateRange,
    RequestContext,
    format_time_header,
    now_cn,
)


class UnifiedQueryEngine:
    """Filter DSL → 参数化 SQL，三种输出模式"""

    def __init__(self, db: Any, org_id: str | None = None):
        self.db = db
        self.org_id = org_id

    async def execute(
        self,
        doc_type: str,
        mode: str,
        filters: list[dict],
        group_by: list[str] | None = None,
        sort_by: str | None = None,
        sort_dir: str = "desc",
        fields: list[str] | None = None,
        limit: int = 20,
        time_type: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        request_ctx: Optional[RequestContext] = None,
    ) -> str:
        """统一入口"""
        if doc_type not in VALID_DOC_TYPES:
            return f"无效的 doc_type: {doc_type}，可选: {', '.join(sorted(VALID_DOC_TYPES))}"
        if mode not in ("summary", "detail", "export"):
            mode = "summary"

        validated, err = _validate_filters(filters)
        if err:
            return err

        tr = _extract_time_range(validated, time_type, request_ctx, mode)

        if mode == "summary":
            return await self._summary(doc_type, validated, tr, group_by, request_ctx)
        elif mode == "detail":
            return await self._detail(
                doc_type, validated, tr, fields, sort_by, sort_dir, limit, request_ctx,
            )
        else:
            return await self._export(
                doc_type, validated, tr, fields, limit,
                user_id, conversation_id, request_ctx,
            )

    # ── Summary 模式 ──────────────────────────────────

    async def _summary(
        self, doc_type: str, filters: list[ValidatedFilter],
        tr: TimeRange, group_by: list[str] | None,
        request_ctx: Optional[RequestContext],
    ) -> str:
        type_name = DOC_TYPE_CN.get(doc_type, doc_type)
        non_time = [f for f in filters if f.field not in TIME_COLUMNS]

        p_shop, p_platform, p_supplier, p_warehouse, dsl = _split_named_params(non_time)

        rpc_group = GROUP_BY_MAP.get(group_by[0], group_by[0]) if group_by else None

        params: dict[str, Any] = {
            "p_doc_type": doc_type,
            "p_start": tr.start_iso, "p_end": tr.end_iso,
            "p_time_col": tr.time_col,
            "p_shop": p_shop, "p_platform": p_platform,
            "p_supplier": p_supplier, "p_warehouse": p_warehouse,
            "p_group_by": rpc_group, "p_limit": 20,
            "p_org_id": self.org_id,
            "p_filters": _json.dumps(dsl) if dsl else None,
        }

        try:
            result = self.db.rpc("erp_global_stats_query", params).execute()
            data = result.data
        except Exception as e:
            logger.error(f"UnifiedQuery summary RPC failed | error={e}", exc_info=True)
            return f"统计查询失败: {e}"

        if not data or data == {} or data == []:
            health = check_sync_health(self.db, [doc_type], org_id=self.org_id)
            return f"{type_name} {tr.label} 内无记录\n{health}".strip()

        if isinstance(data, dict) and "error" in data:
            return f"查询参数错误: {data['error']}"

        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="统计区间",
        )
        if rpc_group is None:
            body = fmt_summary_total(data, type_name, tr.label, self.db, doc_type, self.org_id)
        else:
            body = fmt_summary_grouped(data, rpc_group, type_name, tr.label)

        return f"{time_header}\n\n{body}" if time_header else body

    # ── Detail 模式 ───────────────────────────────────

    async def _detail(
        self, doc_type: str, filters: list[ValidatedFilter],
        tr: TimeRange, fields: list[str] | None,
        sort_by: str | None, sort_dir: str, limit: int,
        request_ctx: Optional[RequestContext],
    ) -> str:
        limit = min(max(limit, 1), 200)
        select_fields = fields or DEFAULT_DETAIL_FIELDS.get(doc_type, ["*"])
        non_time = [f for f in filters if f.field not in TIME_COLUMNS]
        sort_col = sort_by or tr.time_col

        # 冷表 UNION 需要 doc_id + item_index 做去重，确保 select 包含它们
        need_archive = _need_archive(tr)
        query_fields = list(select_fields)
        if need_archive:
            for col in ("doc_id", "item_index"):
                if col not in query_fields:
                    query_fields.append(col)
        select_cols = ",".join(query_fields)

        try:
            rows = _query_table(
                self.db, "erp_document_items", doc_type, non_time, tr,
                select_cols, sort_col, sort_dir, limit, self.org_id,
            )
            if need_archive:
                archive = _query_table(
                    self.db, "erp_document_items_archive", doc_type, non_time, tr,
                    select_cols, sort_col, sort_dir, limit, self.org_id,
                )
                seen = {(r.get("doc_id"), r.get("item_index")) for r in rows}
                for r in archive:
                    if (r.get("doc_id"), r.get("item_index")) not in seen:
                        rows.append(r)
                rows.sort(key=lambda r: r.get(sort_col, ""), reverse=(sort_dir == "desc"))
                rows = rows[:limit]
        except Exception as e:
            logger.error(f"UnifiedQuery detail failed | error={e}", exc_info=True)
            return f"明细查询失败: {e}"

        type_name = DOC_TYPE_CN.get(doc_type, doc_type)
        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="查询窗口",
        )
        if not rows:
            health = check_sync_health(self.db, [doc_type], org_id=self.org_id)
            body = f"{type_name}无匹配记录\n{health}".strip()
            return f"{time_header}\n\n{body}" if time_header else body

        body = fmt_detail_rows(rows, select_fields, type_name, limit)
        return f"{time_header}\n\n{body}" if time_header else body

    # ── Export 模式（DuckDB 流式导出） ────────────────

    async def _export(
        self, doc_type: str, filters: list[ValidatedFilter],
        tr: TimeRange, fields: list[str] | None, limit: int,
        user_id: str | None, conversation_id: str | None,
        request_ctx: Optional[RequestContext],
    ) -> str:
        type_name = DOC_TYPE_CN.get(doc_type, doc_type)

        if not fields:
            return generate_field_doc(doc_type)

        safe_fields = [c for c in fields if c in EXPORT_COLUMN_NAMES]
        if not safe_fields:
            return "❌ 传入的 fields 无有效字段，请参考字段文档"

        # staging 路径（与旧逻辑格式完全一致）
        staging_dir, rel_path, staging_path, filename = resolve_export_path(
            doc_type, user_id, self.org_id, conversation_id,
        )

        # 安全上限
        max_rows = min(limit or EXPORT_MAX, EXPORT_MAX)

        # 构建 DuckDB SQL
        select_sql = build_pii_select(safe_fields)
        where_sql = build_export_where(doc_type, filters, tr, self.org_id)
        need_archive = _need_archive(tr)

        if need_archive:
            inner = (
                f"SELECT {select_sql} FROM pg.public.erp_document_items "
                f"WHERE {where_sql} "
                f"UNION ALL "
                f"SELECT {select_sql} FROM pg.public.erp_document_items_archive "
                f"WHERE {where_sql}"
            )
        else:
            inner = (
                f"SELECT {select_sql} FROM pg.public.erp_document_items "
                f"WHERE {where_sql}"
            )
        query = f"SELECT * FROM ({inner}) sub ORDER BY {tr.time_col} DESC LIMIT {max_rows}"

        # DuckDB 流式导出 → staging（内存恒定，无行数截断）
        # 失败时引擎内部自动重连 + 重试，不降级到旧逻辑
        import asyncio as _asyncio
        start = _time.monotonic()
        from core.duckdb_engine import get_duckdb_engine
        engine = get_duckdb_engine()
        try:
            result = await _asyncio.to_thread(
                engine.export_to_parquet, query, staging_path,
            )
        except Exception as e:
            logger.error(f"DuckDB export failed after retries | error={e}", exc_info=True)
            return f"导出失败（已重试）: {e}"
        row_count = result["row_count"]
        size_kb = result["size_kb"]
        elapsed = _time.monotonic() - start

        # 返回信息（格式与旧逻辑一致，Agent 无感知）
        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="导出窗口",
        )
        if row_count == 0:
            # 清理空文件
            staging_path.unlink(missing_ok=True)
            health = check_sync_health(self.db, [doc_type], org_id=self.org_id)
            body = f"{type_name}无数据\n{health}".strip()
            return f"{time_header}\n\n{body}" if time_header else body

        preview = read_parquet_preview(staging_path, n=3)
        body = (
            f"[数据已暂存] {rel_path}\n"
            f"共 {row_count:,} 条记录（Parquet，{size_kb:.0f}KB），"
            f"耗时 {elapsed:.3f}秒。\n"
            f"如需处理请调 code_execute，"
            f"用 df = pd.read_parquet(STAGING_DIR + '/{filename}') 读取。\n\n"
            f"前3条预览：\n{preview}"
        )
        if row_count >= max_rows:
            body += (
                f"\n\n⚠️ 已达导出上限 {max_rows:,} 行，实际数据可能更多。"
                f"请缩小时间范围重新导出。"
            )
        return f"{time_header}\n\n{body}" if time_header else body



# ── 模块级工具函数（可被 local_compare_stats 等复用） ──


def _validate_filters(
    filters: list[dict],
) -> tuple[list[ValidatedFilter], str | None]:
    """校验 filters 合法性"""
    result: list[ValidatedFilter] = []
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            continue

        field = f.get("field", "")
        op = f.get("op", "")
        value = f.get("value")

        meta = COLUMN_WHITELIST.get(field)
        if not meta:
            available = ", ".join(sorted(COLUMN_WHITELIST.keys()))
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

        value = _coerce_value(value, meta.col_type)
        result.append(ValidatedFilter(field=field, op=op, value=value, col_type=meta.col_type))

    return result, None


def _coerce_value(value: Any, col_type: str) -> Any:
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


def _extract_time_range(
    filters: list[ValidatedFilter],
    time_type: str | None,
    request_ctx: Optional[RequestContext],
    mode: str,
) -> TimeRange:
    """从 filters 中提取时间范围"""
    now = request_ctx.now if request_ctx else now_cn()
    time_col = time_type if time_type in VALID_TIME_COLS else "doc_created_at"

    start_val: str | None = None
    end_val: str | None = None
    detected_col: str | None = None

    for f in filters:
        if f.field in TIME_COLUMNS:
            if detected_col is None:
                detected_col = f.field
            # 只提取同一列的 start/end，避免混用不同时间列
            if f.field == detected_col:
                if f.op in ("gte", "gt"):
                    start_val = str(f.value)
                elif f.op in ("lt", "lte"):
                    end_val = str(f.value)

    if detected_col and detected_col in VALID_TIME_COLS:
        time_col = detected_col

    if start_val is None and end_val is None:
        if mode == "detail":
            s_dt = now - timedelta(days=30)
        else:
            s_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        e_dt = now.replace(hour=23, minute=59, second=59, microsecond=0)
        start_val, end_val = s_dt.isoformat(), e_dt.isoformat()
    elif start_val and not end_val:
        end_val = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
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
        e_dt = now.replace(hour=23, minute=59, second=59, microsecond=0)
        start_val, end_val = s_dt.isoformat(), e_dt.isoformat()

    date_range = DateRange.custom(s_dt, e_dt, reference=now)
    label = f"{s_dt.strftime('%m-%d %H:%M')} ~ {e_dt.strftime('%m-%d %H:%M')}"

    return TimeRange(
        start_iso=start_val, end_iso=end_val,
        time_col=time_col, date_range=date_range, label=label,
    )


def _split_named_params(
    non_time: list[ValidatedFilter],
) -> tuple[str | None, str | None, str | None, str | None, list[dict]]:
    """分离 RPC 命名参数（shop/platform/supplier/warehouse）和 DSL filters"""
    p_shop = p_platform = p_supplier = p_warehouse = None
    dsl: list[dict] = []

    for f in non_time:
        if f.field == "shop_name" and f.op in ("eq", "like"):
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


def _query_table(
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
    q = _apply_orm_filters(q, filters)
    q = q.order(sort_by, desc=(sort_dir == "desc")).limit(limit)
    return q.execute().data or []


def _apply_orm_filters(q: Any, filters: list[ValidatedFilter]) -> Any:
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
        elif f.op == "is_null":
            if val is True or val == "true" or val == 1:
                q = q.is_(f.field, "null")
            else:
                q = q.not_.is_(f.field, "null")
        elif f.op == "between" and isinstance(val, list) and len(val) == 2:
            q = q.gte(f.field, val[0]).lte(f.field, val[1])
    return q


def _need_archive(tr: TimeRange) -> bool:
    """判断是否需要查冷表"""
    try:
        s_dt = datetime.fromisoformat(tr.start_iso.replace("Z", "+00:00"))
        return s_dt < now_cn() - timedelta(days=90)
    except (ValueError, AttributeError):
        return False
