"""
ERP 本地查询共享工具

健康检查、时间计算、文档查询等复用逻辑。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger



def check_sync_health(db, sync_types: list[str]) -> str:
    """检查同步健康状态，返回警告文本（无异常返回空字符串）

    设计文档 §6.0：error_count>=3 或 last_run_at>5分钟 时附加警告。
    """
    warnings: list[str] = []
    try:
        result = (
            db.table("erp_sync_state")
            .select("sync_type,last_run_at,error_count,is_initial_done")
            .in_("sync_type", sync_types)
            .execute()
        )
        now = datetime.now(timezone.utc)
        for row in result.data or []:
            st = row["sync_type"]
            if not row.get("is_initial_done"):
                warnings.append(
                    f"ℹ {st} 首次数据同步进行中，部分历史数据尚未就绪"
                )
                continue
            if (row.get("error_count") or 0) >= 3:
                warnings.append(
                    f"⚠ {st} 数据可能未及时更新"
                    f"（连续失败{row['error_count']}次，"
                    f"最后成功：{row.get('last_run_at', '未知')}）"
                )
            elif row.get("last_run_at"):
                last = datetime.fromisoformat(
                    str(row["last_run_at"]).replace("Z", "+00:00")
                )
                # 统一为 naive datetime 比较（避免 aware/naive 混用）
                if last.tzinfo is not None:
                    last = last.replace(tzinfo=None)
                now_naive = now.replace(tzinfo=None) if now.tzinfo else now
                if (now_naive - last).total_seconds() > 300:
                    warnings.append(
                        f"⚠ {st} 数据可能未及时更新"
                        f"（最后成功：{row['last_run_at']}）"
                    )
    except Exception as e:
        logger.debug(f"Health check failed | error={e}")
    return "\n".join(warnings)


def cutoff_iso(days: int) -> str:
    """计算截止日期 ISO 字符串"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def query_doc_items(
    db, doc_type: str, code: str, days: int,
    extra_filters: dict | None = None,
) -> list[dict]:
    """查询 erp_document_items（days>90 自动 UNION 冷表）"""
    cutoff = cutoff_iso(days)

    def _do_query(table: str) -> list[dict]:
        q = (
            db.table(table)
            .select("*")
            .eq("doc_type", doc_type)
            .or_(f"outer_id.eq.{code},sku_outer_id.eq.{code}")
            .gte("doc_created_at", cutoff)
            .order("doc_created_at", desc=True)
            .limit(500)
        )
        if extra_filters:
            for k, v in extra_filters.items():
                q = q.eq(k, v)
        return q.execute().data or []

    try:
        rows = _do_query("erp_document_items")
        if days > 90:
            archive_rows = _do_query("erp_document_items_archive")
            seen = {(r["doc_id"], r["item_index"]) for r in rows}
            for r in archive_rows:
                if (r["doc_id"], r["item_index"]) not in seen:
                    rows.append(r)
            rows.sort(key=lambda r: r.get("doc_created_at", ""), reverse=True)
        return rows
    except Exception as e:
        logger.error(
            f"Local query failed | type={doc_type} | code={code} | error={e}"
        )
        return []
