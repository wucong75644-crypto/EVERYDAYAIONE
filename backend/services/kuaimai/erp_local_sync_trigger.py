"""Compatibility entry: wake the durable nightly batch, never start a daytime full sync."""
from __future__ import annotations

from loguru import logger

_VALID_TYPES = {'product','stock','supplier','platform_map','order','purchase','receipt','shelf','aftersale','purchase_return'}


async def trigger_erp_sync(db, sync_type: str, org_id: str | None = None) -> str:
    if sync_type not in _VALID_TYPES:
        return f"✗ 无效类型: {sync_type}，可选: {', '.join(sorted(_VALID_TYPES))}"
    try:
        import time
        from core.config import get_settings
        from core.redis import RedisClient
        from services.kuaimai.erp_sync_scheduler import _build_task_id
        settings = get_settings()
        if not settings.erp_sync_enabled:
            return 'ℹ ERP 同步已关闭'
        await RedisClient.enqueue_task(settings.erp_sync_queue_key,
                                       _build_task_id(org_id,'nightly'),time.time())
        return (f'ℹ 已请求检查夜间批次：正常同步为北京时间每日 {settings.erp_sync_nightly_hour:02d}:00；'
                '未完成批次按断点和退避继续补查。此响应不代表同步已完成，不会启动白天全量同步。')
    except Exception as exc:
        logger.warning(f'Nightly wake-up unavailable | error={exc}')
        return '⚠ 未能发送唤醒请求；后台调度器恢复后会检查持久批次进度。'
