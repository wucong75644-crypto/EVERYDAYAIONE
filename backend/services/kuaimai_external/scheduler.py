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


# ──────────────────────── Cookie Keepalive ────────────────────────
#
# 设计原理（方案 0 心跳保活）：
#   - _censeid cookie 大概率是"滑动过期"（每次使用就续命）
#   - 用 cookie 周期性调一个轻量接口 → 维持会话活跃
#   - 选 /kmzk/meal/getFunctionList（只返回菜单配置，~1KB）
#   - 间隔 10 分钟 = 一天 144 次 × 2 source × N org，可控
#
# 不保证 100% 成功 — 如果 cookie 是"绝对过期"（24h 死活续不了），
# 这个无效，但至少能延长寿命到自然极限。
#
# 双源共享：同一 org 的 thinktank + viperp 共享同一个 _censeid，
#          只需要保活其中一个，另一个自动跟着续命。
#          但为了完整性 + 简单实现，两个都保活。

_KEEPALIVE_INTERVAL_SECONDS = 600  # 10 分钟

# 轻量探活接口（智库的菜单配置，~1KB，最快返回）
_KEEPALIVE_URL = "https://erp.superboss.cc/kmzk/meal/getFunctionList"
_KEEPALIVE_MODULE_PATH = "/think_tank/profit_shop/"
_KEEPALIVE_ORIGIN = "https://erp.superboss.cc"
_KEEPALIVE_REFERER = "https://erp.superboss.cc/index.html"


async def kuaimai_external_keepalive_loop() -> None:
    """
    Cookie 心跳保活 loop —— 每 10 分钟用所有 active 凭证调一次轻量接口。

    目的：让快麦服务器认为账号"在使用中"，触发滑动过期续命。
    """
    logger.info(
        f"kuaimai_external_keepalive loop started | "
        f"interval={_KEEPALIVE_INTERVAL_SECONDS}s"
    )
    # 启动后等 1 分钟再开始（避免跟启动初始化竞争）
    await asyncio.sleep(60)

    while True:
        try:
            await keepalive_all_active()
        except asyncio.CancelledError:
            logger.info("kuaimai_external_keepalive loop cancelled")
            return
        except Exception as e:
            logger.error(f"kuaimai_external_keepalive loop error | error={e}")
        await asyncio.sleep(_KEEPALIVE_INTERVAL_SECONDS)


async def keepalive_all_active() -> dict:
    """
    遍历所有 active 凭证，调轻量接口续命。

    返回统计 {"ok": N, "expired": M, "error": K}。
    cookie 失效会被 http_base 抛 CookieExpiredError，
    我们捕获后 mark_expired + 推告警（跟 sync 流程同样的自愈机制）。
    """
    from services.kuaimai_external import (
        credential_store,
        http_base,
        wecom_alert,
    )

    db = await get_async_db()
    stats = {"ok": 0, "expired": 0, "error": 0}

    creds = await credential_store.list_all_active_credentials(db)
    if not creds:
        return stats

    for cred in creds:
        client = http_base.KuaimaiWebClient(
            companyid=cred.kuaimai_company_id,
            cookie=cred.cookie_full or f"_censeid={cred.censeid_cookie}",
            timeout=10.0,  # 探活短超时，不能卡 loop
        )
        try:
            # getFunctionList 是 POST，body 简单
            await client.post(
                url=_KEEPALIVE_URL,
                payload={"companyId": cred.kuaimai_company_id, "version": 2},
                module_path=_KEEPALIVE_MODULE_PATH,
                origin=_KEEPALIVE_ORIGIN,
                referer=_KEEPALIVE_REFERER,
                content_type="application/json",
            )
            # 成功 → 更新 last_health_check_at
            await credential_store.record_sync_success(
                db, credential_id=cred.id,
            )
            stats["ok"] += 1
            logger.debug(
                f"keepalive ok | org={cred.org_id} source={cred.source}"
            )

        except http_base.CookieExpiredError as e:
            await credential_store.mark_expired(
                db, credential_id=cred.id, error_msg=str(e),
            )
            stats["expired"] += 1
            logger.warning(
                f"keepalive cookie expired | "
                f"org={cred.org_id} source={cred.source}"
            )
            # 推告警（best-effort，不影响其他凭证）
            try:
                await wecom_alert.send_alert(
                    cred.org_id,
                    f"⚠️ **快麦 {cred.source} Cookie 失效**\n\n"
                    f"心跳保活检测到会话已过期，请到管理后台 → "
                    f"快麦接入 → 数据源 → 更新 Cookie。",
                )
            except Exception as alert_err:
                logger.error(f"keepalive alert failed | err={alert_err}")

        except Exception as e:
            stats["error"] += 1
            logger.error(
                f"keepalive error | "
                f"org={cred.org_id} source={cred.source} err={e}"
            )
        finally:
            await client.close()

    if stats["ok"] or stats["expired"] or stats["error"]:
        logger.info(f"keepalive_all_active done | stats={stats}")
    return stats
