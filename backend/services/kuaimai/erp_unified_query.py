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
from services.kuaimai.erp_unified_filters import (
    validate_filters as _validate_filters,
    extract_time_range as _extract_time_range,
    split_named_params as _split_named_params,
    need_archive as _need_archive,
)
from services.kuaimai.erp_unified_schema import (
    COLUMN_WHITELIST,
    DEFAULT_DETAIL_FIELDS,
    DOC_TYPE_CN,
    EXPORT_COLUMN_NAMES,
    EXPORT_MAX,
    GROUP_BY_MAP,
    PLATFORM_CN,
    TIME_COLUMNS,
    VALID_DOC_TYPES,
    TimeRange,
    ValidatedFilter,
    _FIELD_LABEL_CN,
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
        include_invalid: bool = False,
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

        # sort_by 白名单校验（只允许 COLUMN_WHITELIST 中的列）
        if sort_by and sort_by not in COLUMN_WHITELIST:
            sort_by = None

        # sort_dir 枚举校验
        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc"

        # fields 白名单校验（SELECT 列，范围比 filter 列更大）
        if fields:
            valid_fields = set(COLUMN_WHITELIST.keys()) | EXPORT_COLUMN_NAMES
            fields = [f for f in fields if f in valid_fields]
            if not fields:
                fields = None

        validated, err = _validate_filters(filters)
        if err:
            return ToolOutput(
                summary=err,
                source="erp",
                status=OutputStatus.ERROR,
                error_message=err,
            )

        tr = _extract_time_range(validated, time_type, request_ctx, mode)

        if mode == "summary":
            return await self._summary(
                doc_type, validated, tr, group_by, request_ctx,
                include_invalid=include_invalid,
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
        include_invalid: bool = False,
    ) -> ToolOutput:
        # 订单分类引擎分支：doc_type=order + 非全量模式 + 无分组
        if doc_type == "order" and not include_invalid and not group_by:
            classified = await self._summary_classified(filters, tr, request_ctx)
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
            "p_group_by": rpc_group, "p_limit": 20,
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
        # platform 编码 → 中文（和 summary 文本保持一致，防止 LLM 输出编码）
        result_data = data if isinstance(data, list) else [data]
        for row in result_data:
            if "platform" in row:
                row["platform"] = PLATFORM_CN.get(row["platform"], row["platform"])
            if "group_key" in row and rpc_group == "platform":
                row["group_key"] = PLATFORM_CN.get(row["group_key"], row["group_key"])
        return ToolOutput(
            summary=summary,
            format=OutputFormat.TABLE,
            source="erp",
            columns=[
                ColumnMeta("doc_count", "integer", "单数"),
                ColumnMeta("total_qty", "integer", "数量"),
                ColumnMeta("total_amount", "numeric", "金额"),
            ],
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
    ) -> ToolOutput | None:
        """订单分类统计：走 erp_order_stats_grouped RPC + 分类引擎"""
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

        params: dict[str, Any] = {
            "p_org_id": self.org_id,
            "p_start": tr.start_iso,
            "p_end": tr.end_iso,
            "p_time_col": tr.time_col,
            "p_filters": _json.dumps(dsl) if dsl else None,
        }

        try:
            result = self.db.rpc("erp_order_stats_grouped", params).execute()
            raw_rows = result.data
        except Exception as e:
            logger.warning(f"分组统计 RPC 失败，回退原逻辑 | error={e}")
            return None

        if not raw_rows or raw_rows == []:
            return None

        try:
            classifier = OrderClassifier.for_org(self.db, self.org_id)
            cr = classifier.classify(raw_rows)
        except Exception as e:
            logger.warning(f"分类引擎异常，回退原逻辑 | error={e}")
            return None

        time_header = format_time_header(
            ctx=request_ctx, range_=tr.date_range, kind="统计区间",
        )
        body = cr.to_display_text()
        summary_text = f"{time_header}\n\n{body}" if time_header else body

        return ToolOutput(
            summary=summary_text,
            format=OutputFormat.TABLE,
            source="erp",
            columns=[
                ColumnMeta("doc_count", "integer", "单数"),
                ColumnMeta("total_qty", "integer", "数量"),
                ColumnMeta("total_amount", "numeric", "金额"),
            ],
            data=[{
                "total": cr.total,
                "valid": cr.valid,
                "categories": cr.categories_list,
            }],
            metadata={
                "recommended_key": "valid",
                "doc_type": "order",
                "time_range": tr.label,
                "time_column": tr.time_col,
            },
        )

    # ── Export 模式（DuckDB 流式导出） ────────────────

    async def _export(
        self, doc_type: str, filters: list[ValidatedFilter],
        tr: TimeRange, fields: list[str] | None, limit: int,
        user_id: str | None, conversation_id: str | None,
        request_ctx: Optional[RequestContext],
    ) -> ToolOutput:
        type_name = DOC_TYPE_CN.get(doc_type, doc_type)

        # fields 为空时用默认字段（detail 合并到 export 后，用户不一定指定 fields）
        if not fields:
            fields = DEFAULT_DETAIL_FIELDS.get(doc_type, ["*"])

        safe_fields = [c for c in fields if c in EXPORT_COLUMN_NAMES]
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

        # export = DuckDB 流式导出，固定用 EXPORT_MAX（100万行）。
        # 上游 limit=20 是 summary 默认值，export 无视。
        max_rows = EXPORT_MAX

        # 构建 DuckDB SQL
        select_sql = build_pii_select(safe_fields, cn_header=True)
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
        # ORDER BY 用中文别名（子查询 AS 后外层只能用别名）
        order_col = _FIELD_LABEL_CN.get(tr.time_col, tr.time_col)
        query = f'SELECT * FROM ({inner}) sub ORDER BY "{order_col}" DESC LIMIT {max_rows}'

        # DuckDB 流式导出 → staging（内存恒定，无行数截断）
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
            return ToolOutput(
                summary=f"导出失败（已重试）: {e}",
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
        from services.agent.data_profile import build_profile_from_duckdb
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
