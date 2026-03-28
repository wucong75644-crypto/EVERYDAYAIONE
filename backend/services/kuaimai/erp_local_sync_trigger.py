"""
ERP 手动同步触发工具

仅在「单据查不到 + 同步状态异常」时由 AI 调用。
含新鲜度检查（2分钟内同步过则跳过）+ 超时保护（120s）。

设计文档: docs/document/TECH_ERP本地优先统一查询架构.md §6 工具3
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger


_VALID_TYPES = {
    "product", "stock", "supplier", "platform_map",
    "order", "purchase", "receipt", "shelf",
    "aftersale", "purchase_return",
}


async def trigger_erp_sync(db, sync_type: str, org_id: str | None = None) -> str:
    """手动触发 ERP 同步（带超时保护 + 新鲜度检查）"""
    if sync_type not in _VALID_TYPES:
        return (
            f"✗ 无效类型: {sync_type}，"
            f"可选: {', '.join(sorted(_VALID_TYPES))}"
        )

    # 新鲜度检查：2分钟内同步过则跳过
    if _is_recently_synced(db, sync_type):
        return f"ℹ {sync_type} 2分钟内刚同步过，数据已是最新"

    start = time.monotonic()
    try:
        from services.kuaimai.erp_sync_service import ErpSyncService
        svc = ErpSyncService(db)
        await asyncio.wait_for(svc.sync(sync_type), timeout=120)
        elapsed = time.monotonic() - start
        return (
            f"✓ {sync_type} 同步完成（耗时 {elapsed:.1f}s）\n"
            f"请重新调用原查询工具获取最新数据"
        )
    except asyncio.TimeoutError:
        return (
            f"⏱ {sync_type} 同步超时（>120s），"
            f"后台 Worker 会继续同步，请稍后重试查询"
        )
    except Exception as e:
        logger.error(
            f"trigger_erp_sync failed | type={sync_type} | error={e}",
            exc_info=True,
        )
        return f"✗ {sync_type} 同步失败: {e}"


def _is_recently_synced(db, sync_type: str) -> bool:
    """检查2分钟内是否同步过"""
    try:
        state = (
            db.table("erp_sync_state")
            .select("last_run_at")
            .eq("sync_type", sync_type)
            .execute()
        )
        if state.data and state.data[0].get("last_run_at"):
            from datetime import datetime, timezone
            last_str = str(state.data[0]["last_run_at"])
            last = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - last).total_seconds() < 120
    except Exception as e:
        logger.debug(f"Sync freshness check failed | {e}")
    return False
