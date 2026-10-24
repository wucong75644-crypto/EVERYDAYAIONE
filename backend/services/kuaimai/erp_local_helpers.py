"""
ERP 本地查询共享工具

健康检查、时间计算、文档查询等复用逻辑。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

# ERP 数据存储使用中国时间（快麦 API 返回北京时间），查询必须用同一时区。
# 改用 ZoneInfo（IANA 标准），向后兼容旧 import 路径。
# 设计文档：docs/document/TECH_ERP时间准确性架构.md §4.4
from utils.time_context import CN_TZ  # noqa: F401  (re-export for backward compat)



def check_sync_health(db, sync_types: list[str], org_id: str | None = None) -> str:
    """检查同步健康状态，返回警告文本（无异常返回空字符串）

    设计文档 §6.0：error_count>=3 或超过一个夜间周期 时附加警告。
    """
    warnings: list[str] = []
    try:
        q = (
            db.table("erp_sync_state")
            .select("sync_type,last_run_at,error_count,is_initial_done")
            .in_("sync_type", sync_types)
        )
        result = q.execute()
        now = datetime.now(CN_TZ)
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
                    f"最近尝试：{row.get('last_run_at', '未知')}）"
                )
            elif row.get("last_run_at"):
                last = datetime.fromisoformat(
                    str(row["last_run_at"]).replace("Z", "+00:00")
                )
                # 统一为 naive datetime 比较（避免 aware/naive 混用）
                if last.tzinfo is not None:
                    last = last.astimezone(CN_TZ).replace(tzinfo=None)
                now_naive = now.replace(tzinfo=None) if now.tzinfo else now
                if (now_naive - last).total_seconds() > 36 * 3600:
                    warnings.append(
                        f"⚠ {st} 数据可能未及时更新"
                        f"（最近尝试：{row['last_run_at']}）"
                    )
        batches = db.table("erp_sync_batches").select(
            "business_date,status,source_as_of,last_error").order("business_date",desc=True).limit(1)
        batches = batches.eq("org_id",org_id) if org_id else batches.is_("org_id","null")
        latest = batches.execute()
        for batch in latest.data or []:
            if not batch.get('business_date'):
                continue
            if batch.get('status') == 'complete':
                warnings.append(f"ℹ 本地单据快照：业务日 {batch['business_date']}，变更拉取上界 {batch.get('source_as_of')}")
            else:
                warnings.append(f"⚠ {batch['business_date']} 夜间批次尚未完成，单据仍可能为上次已发布数据")
    except Exception as e:
        logger.warning(f"Health check failed | error={e}")
        return "⚠ 同步状态检查失败，数据可能不完整"
    return "\n".join(warnings)


def cutoff_iso(days: int) -> str:
    """计算截止日期 ISO 字符串（中国时间，与 doc_created_at 对齐）"""
    return (datetime.now(CN_TZ) - timedelta(days=days)).isoformat()



# query_doc_items() 已移除 — erp_document_items 查询统一由
# erp_unified_query.UnifiedQueryEngine 处理。
