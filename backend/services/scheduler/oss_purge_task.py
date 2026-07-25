"""
OSS 延迟清理任务

每天凌晨 3 点扫描 deleted_files 表，清理超过 30 天的 OSS 文件。
confirm_delete 删除 NAS 文件时不删 OSS，OSS 保留 30 天作为恢复窗口。
"""

import asyncio
from datetime import datetime, time, timedelta, timezone

from loguru import logger

# 每批处理条数（避免单次事务太大）
_BATCH_SIZE = 100
# 目标执行时间：每天凌晨 3:00（Asia/Shanghai）
_TARGET_HOUR = 3
_TARGET_MINUTE = 0


async def oss_purge_loop() -> None:
    """每天凌晨 3 点执行一次 OSS 清理，永不退出。"""
    logger.info("OSS purge loop started | target=03:00 Asia/Shanghai")
    while True:
        try:
            await _sleep_until_target()
            purged = await _purge_expired_files()
            logger.info(f"OSS purge completed | purged={purged}")
        except asyncio.CancelledError:
            logger.info("OSS purge loop cancelled")
            return
        except Exception as e:
            logger.error(f"OSS purge loop error | error={e}")
            await asyncio.sleep(3600)  # 出错后 1 小时重试


async def _sleep_until_target() -> None:
    """睡到下一个凌晨 3:00（Asia/Shanghai）"""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)
    target = datetime.combine(now.date(), time(_TARGET_HOUR, _TARGET_MINUTE), tzinfo=tz)
    if now >= target:
        target += timedelta(days=1)
    delta = (target - now).total_seconds()
    logger.debug(f"OSS purge sleeping | next_run={target.isoformat()} | seconds={delta:.0f}")
    await asyncio.sleep(delta)


async def _purge_expired_files() -> int:
    """扫描过期记录，逐条删除 OSS 文件并标记 purged。"""
    from core.database import get_async_db
    from services.knowledge_config import is_kb_available

    if not is_kb_available():
        return 0

    db = await get_async_db()
    total_purged = 0
    while True:
        response = await db.rpc(
            "sync_list_oss_purge_candidates",
            {"p_limit": _BATCH_SIZE},
        ).execute()
        rows = response.data or []
        if not rows:
            break

        successful = 0
        for row in rows:
            row_id = row["id"]
            oss_key = row["oss_object_key"]
            ok = await _delete_oss_object(oss_key)
            if ok:
                marked = await db.rpc(
                    "sync_mark_oss_file_purged",
                    {
                        "p_id": row_id,
                        "p_oss_object_key": oss_key,
                    },
                ).execute()
                if marked.data:
                    successful += 1
                    total_purged += 1
                    logger.debug(f"OSS purged | key={oss_key}")
            else:
                logger.warning(f"OSS purge failed, skip | key={oss_key}")
        if successful == 0:
            break

    return total_purged


async def _delete_oss_object(oss_key: str) -> bool:
    """调用 OSS 服务删除对象"""
    try:
        from services.oss_service import get_oss_service
        oss = get_oss_service()
        # oss_key 格式: workspace/org/user/下载/a.xlsx
        # delete_workspace_object 需要去掉 workspace/ 前缀
        rel = oss_key.removeprefix("workspace/")
        return await oss.delete_workspace_object(rel)
    except Exception as e:
        logger.warning(f"OSS delete error | key={oss_key} | error={e}")
        return False
