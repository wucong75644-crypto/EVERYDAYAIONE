"""
快麦外部数据自动同步调度器

每天上午 10:00（Asia/Shanghai）触发，遍历所有 active 凭证：
  - 智库 sync
  - viperp sync

分层兜底（应对延迟修正、退款、成本重算）：
  - 月 1 号：抓过去 90 天
  - 周一  ：抓过去 30 天
  - 其他  ：抓过去 7 天

按 services/scheduler/oss_purge_task.py 同样的循环 + 错误退避模式。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Literal

from loguru import logger

from core.database import get_async_db


# 目标执行时间：上午 10:00（给快麦自己的 T+1 计算留缓冲，POC 实测 ~8:20 完成）
_TARGET_HOUR = 10
_TARGET_MINUTE = 0


def _decide_backfill_days(today: date) -> int:
    """决定本次同步的回溯天数（分层兜底）。"""
    if today.day == 1:
        return 90   # 月 1 号：抓过去 90 天，捕捉季度对账修正
    if today.weekday() == 0:
        return 30   # 周一：抓过去 30 天，捕捉月度成本重算
    return 7        # 平时：抓过去 7 天，捕捉退款/售后修正


async def kuaimai_external_sync_loop() -> None:
    """每天上午 10:00 执行一次完整同步，永不退出。"""
    logger.info(
        f"kuaimai_external_sync loop started | "
        f"target={_TARGET_HOUR:02d}:{_TARGET_MINUTE:02d} Asia/Shanghai"
    )
    while True:
        try:
            await _sleep_until_target()
            today = date.today()
            backfill = _decide_backfill_days(today)
            start_date = today - timedelta(days=backfill)
            logger.info(
                f"kuaimai_external_sync 触发 | "
                f"today={today} backfill_days={backfill} "
                f"range=[{start_date} ~ {today}]"
            )
            await sync_all_active(start_date=start_date, end_date=today)
            logger.info("kuaimai_external_sync 周期完成")
        except asyncio.CancelledError:
            logger.info("kuaimai_external_sync loop cancelled")
            return
        except Exception as e:
            logger.error(f"kuaimai_external_sync loop error | error={e}")
            await asyncio.sleep(3600)  # 出错后 1 小时重试


async def _sleep_until_target() -> None:
    """睡到下一个目标时间（Asia/Shanghai 10:00）"""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)
    target = datetime.combine(
        now.date(), time(_TARGET_HOUR, _TARGET_MINUTE), tzinfo=tz
    )
    if now >= target:
        target += timedelta(days=1)
    delta = (target - now).total_seconds()
    logger.debug(
        f"kuaimai_external_sync sleeping | "
        f"next_run={target.isoformat()} seconds={delta:.0f}"
    )
    await asyncio.sleep(delta)


async def sync_all_active(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    sync_type: Literal["daily", "manual", "backfill"] = "daily",
) -> dict:
    """
    遍历所有 active 凭证 → 跑同步。

    Args:
        start_date / end_date: 时间范围（默认调用方决定）
        sync_type: 写入 sync_logs 的类型标签

    Returns:
        {"thinktank": {"ok": N, "fail": M}, "viperp": {...}}
    """
    from services.kuaimai_external import (
        credential_store,
        thinktank_sync,
        viperp_sync,
    )

    db = await get_async_db()
    stats = {
        "thinktank": {"ok": 0, "fail": 0, "rows": 0, "cookie_expired": 0},
        "viperp": {"ok": 0, "fail": 0, "rows": 0, "cookie_expired": 0},
    }

    creds = await credential_store.list_all_active_credentials(db)
    logger.info(f"sync_all_active 待同步凭证数: {len(creds)}")

    for cred in creds:
        try:
            if cred.source == "thinktank":
                r = await thinktank_sync.sync_thinktank(
                    db,
                    org_id=cred.org_id,
                    sync_type=sync_type,
                    start_date=start_date,
                    end_date=end_date,
                )
            elif cred.source == "viperp":
                r = await viperp_sync.sync_viperp(
                    db,
                    org_id=cred.org_id,
                    sync_type=sync_type,
                    start_date=start_date,
                    end_date=end_date,
                )
            else:
                continue

            bucket = stats[cred.source]
            if r.success:
                bucket["ok"] += 1
                bucket["rows"] += r.rows_synced
            else:
                bucket["fail"] += 1
                if r.cookie_expired:
                    bucket["cookie_expired"] += 1

        except Exception as e:
            logger.error(
                f"sync_all_active 异常 | "
                f"org={cred.org_id} source={cred.source} err={e}"
            )
            stats[cred.source]["fail"] += 1

    logger.info(f"sync_all_active 完成 | stats={stats}")
    return stats
