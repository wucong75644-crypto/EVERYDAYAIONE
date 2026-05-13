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
    from services.knowledge_config import get_pg_connection, is_kb_available

    if not is_kb_available():
        return 0

    conn_ctx = await get_pg_connection()
    if conn_ctx is None:
        return 0

    total_purged = 0
    async with conn_ctx as conn:
        while True:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, oss_object_key, relative_path
                    FROM deleted_files
                    WHERE purge_after < now() AND NOT purged
                    ORDER BY purge_after
                    LIMIT %s
                    """,
                    (_BATCH_SIZE,),
                )
                rows = await cur.fetchall()

            if not rows:
                break

            for row_id, oss_key, rel_path in rows:
                ok = await _delete_oss_object(oss_key)
                if ok:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE deleted_files SET purged = TRUE WHERE id = %s",
                            (row_id,),
                        )
                    total_purged += 1
                    logger.debug(f"OSS purged | key={oss_key}")
                else:
                    logger.warning(f"OSS purge failed, skip | key={oss_key}")

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
