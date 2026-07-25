"""
ERP 手动同步触发工具

仅在「单据查不到 + 同步状态异常」时由 AI 调用。
含新鲜度检查（2分钟内同步过则跳过），实际写入由 Sync Worker 完成。

设计文档: docs/document/TECH_ERP本地优先统一查询架构.md §6 工具3
"""

from __future__ import annotations

from loguru import logger


_VALID_TYPES = {
    "product", "stock", "supplier", "platform_map",
    "order", "purchase", "receipt", "shelf",
    "aftersale", "purchase_return",
}


async def trigger_erp_sync(db, sync_type: str, org_id: str | None = None) -> str:
    """将手动 ERP 同步请求送入 Sync Worker 队列。"""
    if sync_type not in _VALID_TYPES:
        return (
            f"✗ 无效类型: {sync_type}，"
            f"可选: {', '.join(sorted(_VALID_TYPES))}"
        )

    # 新鲜度检查：2分钟内同步过则跳过
    if _is_recently_synced(db, sync_type):
        return f"ℹ {sync_type} 2分钟内刚同步过，数据已是最新"

    try:
        from core.config import get_settings
        from core.redis import RedisClient
        from services.kuaimai.erp_sync_scheduler import (
            PRIORITY_WEIGHTS,
            _build_task_id,
        )
        import time

        task_id = _build_task_id(org_id, sync_type)
        score = time.time() - PRIORITY_WEIGHTS.get(sync_type, 0)
        queued = await RedisClient.enqueue_task(
            get_settings().erp_sync_queue_key,
            task_id,
            score,
        )
        state = "已进入队列" if queued else "已在队列或执行中"
        return f"✓ {sync_type} 同步任务{state}，请稍后重试原查询"
    except Exception as e:
        logger.error(
            f"trigger_erp_sync failed | type={sync_type} | error={e}",
            exc_info=True,
        )
        return f"✗ {sync_type} 同步任务入队失败: {e}"


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
