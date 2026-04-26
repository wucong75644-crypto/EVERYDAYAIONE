"""
统一查询引擎（Filter DSL → 参数化 SQL）

替代 7 个碎片工具，统一对 erp_document_items 的查询入口。
三种模式：summary（RPC聚合）/ detail（ORM明细）/ export（Parquet导出）

所有模式返回 ToolOutput（Phase 0 改造）。

设计文档: docs/document/TECH_统一查询引擎FilterDSL.md
重构文档: docs/document/TECH_多Agent单一职责重构.md §4.3
"""

from __future__ import annotations

import json as _json
import time as _time
import uuid as _uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from services.agent.tool_output import (
    ColumnMeta,
    _FORMAT_MIME,
    FileRef,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)
from services.kuaimai.erp_local_helpers import CN_TZ, check_sync_health
from services.kuaimai.erp_duckdb_helpers import (
    build_export_where,
    build_pii_select,
    resolve_export_path,
)
from services.kuaimai.erp_query_preflight import preflight_check
from services.kuaimai.erp_unified_filters import (
    validate_filters as _validate_filters,
    extract_time_range as _extract_time_range,
    split_named_params as _split_named_params,
    need_archive as _need_archive,
)
from services.kuaimai.erp_unified_schema import (
    COLUMN_WHITELIST,
    DOC_TYPE_CN,
    DOC_TYPE_TABLE,
    EXPORT_COLUMN_NAMES,
    EXPORT_MAX,
    GROUP_BY_MAP,
    RPC_ORDER_STATS_FILTER_FIELDS,
    PLATFORM_CN,
    TIME_COLUMNS,
    VALID_DOC_TYPES,
    _DOCUMENT_ITEM_DOC_TYPES,
    TimeRange,
    ValidatedFilter,
    _FIELD_LABEL_CN,
    get_column_whitelist,
    fmt_summary_grouped,
    fmt_summary_total,
    format_filter_hint,
    generate_field_doc,
)
from utils.time_context import (
    DateRange,
    RequestContext,
    format_time_header,
    now_cn,
)


# ── Summary 列定义（ColumnMeta label = 中文，序列化出口自动翻译 key） ──

_SUMMARY_BASE_COLUMNS = [
    ColumnMeta("doc_count", "integer", "单数"),
    ColumnMeta("total_qty", "integer", "数量"),
    ColumnMeta("total_amount", "numeric", "金额"),
]

_GROUP_LABEL = {
    "platform": "平台", "shop": "店铺", "product": "商品",
    "supplier": "供应商", "warehouse": "仓库", "status": "状态",
}



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
        extra_fields: list[str] | None = None,
        limit: int = 20,
        time_type: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        request_ctx: Optional[RequestContext] = None,
        include_invalid: bool = False,
        push_thinking: Any = None,
        **_kwargs,  # 吸收 LLM 透传的未知参数，防止 TypeError
    ) -> ToolOutput:
        """统一入口——所有参数在此校验，下游不再需要防御"""
        if doc_type not in VALID_DOC_TYPES:
            return ToolOutput(
                summary=f"无效的 doc_type: {doc_type}，可选: {', '.join(sorted(VALID_DOC_TYPES))}",
                source="erp",
                status=OutputStatus.ERROR,
                error_message=f"invalid doc_type: {doc_type}",
            )
        if mode == "detail":
            mode = "export"  # detail 已合并到 export（与 plan_builder 对齐）
        if mode not in ("summary", "export"):
            mode = "summary"

        # group_by 白名单校验（LLM 可能传 "store" 等非标准值）
        if group_by:
            valid_groups = [g for g in group_by if g in GROUP_BY_MAP]
            group_by = valid_groups or None

        # sort_by 白名单校验（允许当前表的列）
        col_wl = get_column_whitelist(doc_type)
        if sort_by and sort_by not in col_wl:
            sort_by = None

        # sort_dir 枚举校验
        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc"

        # limit 归一化（≤0 无合法语义，回退默认值）
        if not isinstance(limit, int) or limit <= 0:
            limit = 20

        # extra_fields 白名单校验（追加到默认列的额外列）
        if extra_fields:
            valid_fields = set(col_wl.keys()) | EXPORT_COLUMN_NAMES
            extra_fields = [f for f in extra_fields if f in valid_fields]
            if not extra_fields:
                extra_fields = None

        # 新表走按 doc_type 分组的白名单校验
        table = DOC_TYPE_TABLE.get(doc_type, "erp_document_items")
        is_new_table = table != "erp_document_items"

        validated, err = _validate_filters(
            filters, doc_type=doc_type if is_new_table else None,
        )
        if err:
            return ToolOutput(
                summary=err,
                source="erp",
                status=OutputStatus.ERROR,
                error_message=err,
            )

        tr = _extract_time_range(
            validated, time_type, request_ctx, mode,
            doc_type=doc_type if is_new_table else None,
        )

        # ── 新表路由：ORM 直查 ──
        if is_new_table:
            if mode == "summary":
                result = await self._summary_orm(
                    table, doc_type, validated, tr,
                    sort_by=sort_by, sort_dir=sort_dir, limit=limit,
                    request_ctx=request_ctx,
                )
            else:
                result = await self._export_orm(
                    table, doc_type, validated, tr,
                    sort_by=sort_by, sort_dir=sort_dir, limit=limit,
                    extra_fields=extra_fields,
                    user_id=user_id, conversation_id=conversation_id,
                    request_ctx=request_ctx,
                    push_thinking=push_thinking,
                )
        else:
            # ── 现有表：预检防御层 ──
            preflight = preflight_check(
                db=self.db, doc_type=doc_type,
                time_col=tr.time_col, start_iso=tr.start_iso, end_iso=tr.end_iso,
                org_id=self.org_id, mode=mode,
            )

            if not preflight.ok:
                result = ToolOutput(
                    summary=preflight.reject_reason,
                    source="erp",
                    status=OutputStatus.REJECTED,
                    metadata={
                        "estimated_rows": preflight.estimated_rows,
                        "suggestions": list(preflight.suggestions),
                    },
                )
            elif mode == "summary":
                result = await self._summary(
                    doc_type, validated, tr, group_by, request_ctx,
                    include_invalid=include_invalid,
                    sort_by=sort_by, sort_dir=sort_dir, limit=limit,
                )
            else:
                result = await self._export(
                    doc_type, validated, tr, extra_fields, limit,
                    user_id, conversation_id, request_ctx,
                    include_invalid=include_invalid,
                    push_thinking=push_thinking,
                    sort_by=sort_by, sort_dir=sort_dir,
                )

        # 统一出口：注入已应用的过滤条件摘要，让下游 LLM 知道数据已过滤
        if result.summary:
            hint = format_filter_hint(validated)
            if hint:
                result.summary = f"{hint}\n{result.summary}"

        return result

    # ── Summary 模式 ──────────────────────────────────

    async def _summary(
        self, doc_type: str, filters: list[ValidatedFilter],
        tr: TimeRange, group_by: list[str] | None,
        request_ctx: Optional[RequestContext],
        include_invalid: bool = False,
        sort_by: str | None = None,
        sort_dir: str = "desc",
        limit: int = 20,
    ) -> ToolOutput:
        # 订单统计走分类引擎，但前提是所有 filter 字段都被 RPC 支持
        if doc_type == "order":
            # 检测 filter 字段是否在 erp_order_stats_grouped 白名单内
            # 不支持的字段（如 receiver_name/express_company）跳过分类路径，
            # 回退到 erp_global_stats_query（SELECT * 支持全列）
            non_time_fields = {
                f.field for f in filters if f.field not in TIME_COLUMNS
            }
            unsupported = non_time_fields - RPC_ORDER_STATS_FILTER_FIELDS
            if unsupported:
                logger.info(
                    f"跳过分类引擎: filter 包含 RPC 不支持的字段 {unsupported}"
                )
            else:
                classified = await self._summary_classified(
                    filters, tr, request_ctx,
                    group_by=group_by,
                    include_invalid=include_invalid,
                )
                if classified is not None:
                    return classified

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
            "p_group_by": rpc_group, "p_limit": limit,
            "p_org_id": self.org_id,
            "p_filters": _json.dumps(dsl) if dsl else None,
        }

        try:
            result = self.db.rpc("erp_global_stats_query", params).execute()
            data = result.data
        except Exception as e:
            logger.error(f"UnifiedQuery summary RPC failed | error={e}", exc_info=True)
            return ToolOutput(
                summary=f"统计查询失败: {e}",
                source="erp",
                status=OutputStatus.ERROR,
                error_message=str(e),
            )

        if not data or data == {} or data == []:
            health = check_sync_health(self.db, [doc_type], org_id=self.org_id)
            return ToolOutput(
                summary=f"{type_name} {tr.label} 内无记录\n{health}".strip(),
                source="erp",
                status=OutputStatus.EMPTY,
                metadata={"doc_type": doc_type, "time_range": tr.label},
            )

        if isinstance(data, dict) and "error" in data:
            return ToolOutput(
                summary=f"查询参数错误: {data['error']}",
                source="erp",
                status=OutputStatus.ERROR,
                error_message=str(data["error"]),
            )

        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="统计区间",
        )
        if rpc_group is None:
            body = fmt_summary_total(data, type_name, tr.label, self.db, doc_type, self.org_id)
        else:
            body = fmt_summary_grouped(data, rpc_group, type_name, tr.label)

        summary = f"{time_header}\n\n{body}" if time_header else body

        # summary 数据可以是 dict（总计）或 list（分组）
        # platform 编码 → 中文 + 清理冗余字段（group_key 已含分组值）
        result_data = data if isinstance(data, list) else [data]
        for row in result_data:
            if "group_key" in row and rpc_group == "platform":
                row["group_key"] = PLATFORM_CN.get(row["group_key"], row["group_key"])
            elif "platform" in row:
                row["platform"] = PLATFORM_CN.get(row["platform"], row["platform"])
            # 分组时 platform 和 group_key 重复，删除冗余字段避免导出列名混乱
            if "group_key" in row and "platform" in row:
                del row["platform"]
        summary_cols = list(_SUMMARY_BASE_COLUMNS)
        if rpc_group:
            summary_cols.insert(0, ColumnMeta("group_key", "text", _GROUP_LABEL.get(rpc_group, "分组")))
        return ToolOutput(
            summary=summary,
            format=OutputFormat.TABLE,
            source="erp",
            columns=summary_cols,
            data=result_data,
            metadata={
                "doc_type": doc_type,
                "time_range": tr.label,
                "time_column": tr.time_col,
            },
        )

    async def _summary_classified(
        self, filters: list[ValidatedFilter],
        tr: TimeRange,
        request_ctx: Optional[RequestContext],
        group_by: list[str] | None = None,
        include_invalid: bool = False,
    ) -> ToolOutput | None:
        """订单分类统计（委托 erp_classified_summary 模块）。"""
        from services.kuaimai.erp_classified_summary import classified_summary
        return await classified_summary(
            db=self.db, org_id=self.org_id,
            filters=filters, tr=tr, request_ctx=request_ctx,
            group_by=group_by, include_invalid=include_invalid,
        )

    # ── 新表 ORM 查询（委托 erp_orm_query 模块）────────

    async def _summary_orm(self, table, doc_type, filters, tr, **kw) -> ToolOutput:
        from services.kuaimai.erp_orm_query import summary_orm
        return await summary_orm(self.db, self.org_id, table, doc_type, filters, tr, **kw)

    async def _export_orm(self, table, doc_type, filters, tr, **kw) -> ToolOutput:
        from services.kuaimai.erp_orm_query import export_orm
        return await export_orm(self.db, self.org_id, table, doc_type, filters, tr, **kw)

    # ── Export 模式（DuckDB 流式导出） ────────────────

    async def _export(
        self, doc_type: str, filters: list[ValidatedFilter],
        tr: TimeRange, extra_fields: list[str] | None, limit: int,
        user_id: str | None, conversation_id: str | None,
        request_ctx: Optional[RequestContext],
        include_invalid: bool = False,
        push_thinking: Any = None,
        sort_by: str | None = None,
        sort_dir: str = "desc",
    ) -> ToolOutput:
        type_name = DOC_TYPE_CN.get(doc_type, doc_type)
        # include_invalid 在 export 模式预留（与 summary 语义一致：
        # 总数包含刷单，只做标记分类不排除。用户显式传 is_scalping
        # 过滤时才会排除刷单行）

        from services.kuaimai.erp_unified_schema import merge_export_fields
        safe_fields = merge_export_fields(doc_type, extra_fields)
        # 排序列必须在 SELECT 中，否则 ORDER BY 报列不存在
        if tr.time_col and tr.time_col not in safe_fields and tr.time_col in EXPORT_COLUMN_NAMES:
            safe_fields.append(tr.time_col)
        if not safe_fields:
            return ToolOutput(
                summary="传入的 fields 无有效字段，请参考字段文档",
                source="erp",
                status=OutputStatus.ERROR,
                error_message="no valid export fields",
            )

        # staging 路径（与旧逻辑格式完全一致）
        staging_dir, rel_path, staging_path, filename = resolve_export_path(
            doc_type, user_id, self.org_id, conversation_id,
        )

        # export 行数：用户指定 limit 与安全上限取小值（入口已保证 limit > 0）
        max_rows = min(limit, EXPORT_MAX)

        # 构建 DuckDB SQL
        select_sql = build_pii_select(safe_fields, cn_header=True)

        # 订单导出：追加 order_class 分类标签列（从规则表动态生成）
        if doc_type == "order":
            try:
                from services.kuaimai.order_classifier import OrderClassifier
                classifier = OrderClassifier.for_org(self.db, self.org_id)
                case_sql = classifier.to_case_sql()
                select_sql += f', {case_sql} AS "订单分类"'
            except Exception as e:
                logger.warning(f"导出分类标签生成失败，跳过 | error={e}")

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
        # ORDER BY：优先用 sort_by，降级到 time_col（中文别名）
        if sort_by and sort_by in COLUMN_WHITELIST:
            order_col = _FIELD_LABEL_CN.get(sort_by, sort_by)
            order_dir = sort_dir.upper()
        else:
            order_col = _FIELD_LABEL_CN.get(tr.time_col, tr.time_col)
            order_dir = "DESC"
        query = f'SELECT * FROM ({inner}) sub ORDER BY "{order_col}" {order_dir} LIMIT {max_rows}'

        # DuckDB 流式导出 → staging（子进程隔离，崩溃不影响 chat worker）
        start = _time.monotonic()
        try:
            from services.kuaimai.erp_export_subprocess import subprocess_export
            result = await subprocess_export(
                query, str(staging_path), push_thinking=push_thinking,
            )
        except Exception as e:
            logger.error(f"DuckDB export subprocess failed | error={e}", exc_info=True)
            return ToolOutput(
                summary=f"导出失败: {e}",
                source="erp",
                status=OutputStatus.ERROR,
                error_message=str(e),
            )
        row_count = result["row_count"]
        size_kb = result["size_kb"]
        elapsed = _time.monotonic() - start

        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="导出窗口",
        )
        if row_count == 0:
            staging_path.unlink(missing_ok=True)
            health = check_sync_health(self.db, [doc_type], org_id=self.org_id)
            body = f"{type_name}无数据\n{health}".strip()
            summary = f"{time_header}\n\n{body}" if time_header else body
            return ToolOutput(
                summary=summary,
                source="erp",
                status=OutputStatus.EMPTY,
                metadata={"doc_type": doc_type, "time_range": tr.label},
            )

        # v6: DuckDB 直接从 parquet 文件算统计（不加载到 Python 内存）
        import asyncio as _asyncio
        from core.duckdb_engine import get_duckdb_engine
        from services.agent.data_profile import build_profile_from_duckdb
        engine = get_duckdb_engine()
        _profile_raw = await _asyncio.to_thread(
            engine.profile_parquet, staging_path,
        )
        profile_text, _export_stats = build_profile_from_duckdb(
            _profile_raw, filename=filename,
            file_size_kb=size_kb, elapsed=elapsed,
        )
        body = profile_text
        if row_count >= max_rows:
            body += (
                f"\n\n⚠️ 已达导出上限 {max_rows:,} 行，实际数据可能更多。"
                f"请缩小时间范围重新导出。"
            )
        summary = f"{time_header}\n\n{body}" if time_header else body

        # 构建列元信息（export 用中文列名，与 parquet 列头一致）
        from services.kuaimai.erp_unified_schema import build_column_metas_cn
        export_columns = build_column_metas_cn(safe_fields)
        # 订单导出追加分类标签列元信息
        if doc_type == "order":
            export_columns.append(ColumnMeta("订单分类", "text", "订单分类"))

        file_ref = FileRef(
            path=str(staging_path),
            filename=filename,
            format="parquet",
            row_count=row_count,
            size_bytes=int(size_kb * 1024),
            columns=export_columns,
            preview=profile_text,
            created_at=_time.time(),
            id=_uuid.uuid4().hex,
            mime_type=_FORMAT_MIME.get("parquet", ""),
            created_by="erp_export",
        )

        return ToolOutput(
            summary=summary,
            format=OutputFormat.FILE_REF,
            source="erp",
            file_ref=file_ref,
            metadata={
                "doc_type": doc_type,
                "time_range": tr.label,
                "time_column": tr.time_col,
                "stats": _export_stats,
            },
        )

