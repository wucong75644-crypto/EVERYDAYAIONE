"""
EVERYDAYAI Sync Worker 独立进程入口

承载所有后台同步任务，与 API 服务（uvicorn）解耦：
- ERP 同步编排器（Scheduler + WorkerPool + AggregationConsumer + DeadLetterConsumer）
- ERP 同步健康检查
- 快麦 Web 数据同步（thinktank / viperp，每天 10:00）
- OSS 延迟清理（每天 03:00）
- 全局错误日志收集（loguru sink → DB）

单实例运行（systemd 保证），无需 leader election。
启动: python -m sync_worker_main  或  python sync_worker_main.py
"""

from __future__ import annotations

import asyncio
import os
import signal

from loguru import logger

from core.config import get_settings
from core.logging_config import setup_logging
from core.redis import RedisClient


# ============================================================
# 启动前 sanity check（时区 + tzdata）
# ============================================================

setup_logging()


def _time_arch_sanity_check() -> None:
    """启动时校验时区/tzdata 配置，失败 fail-fast。

    sync 进程比 API 进程更依赖时区正确性：scheduler 用 Asia/Shanghai 计算
    "明天 10:00" 等目标时间，时区错会让定时任务跑到错误时刻。
    """
    if os.environ.get("SKIP_TIME_SANITY_CHECK") == "1":
        logger.warning("[sync] SKIP_TIME_SANITY_CHECK=1，跳过时区校验")
        return

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError as e:
        raise RuntimeError(
            "tzdata 不可用，无法加载 Asia/Shanghai。"
            "请确保安装了 tzdata 包，或运行 pip install tzdata。"
            f"原始错误: {e}"
        )

    from datetime import datetime
    now_local = datetime.now(tz)
    process_tz = os.environ.get("TZ", "(unset)")
    logger.info(
        f"[sync] time-arch sanity ok | "
        f"now={now_local.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
        f"TZ_env={process_tz}"
    )


_time_arch_sanity_check()


_settings = get_settings()
if _settings.sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(
        dsn=_settings.sentry_dsn,
        environment=_settings.environment,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )
    logger.info(f"Sentry initialized | environment={_settings.environment}")


# ============================================================
# 主流程
# ============================================================


async def _run() -> None:
    """启动所有常驻 task → 等待 shutdown 信号 → 优雅停止"""
    logger.info(f"Starting EVERYDAYAI Sync Worker | pid={os.getpid()}")

    # Redis
    try:
        await RedisClient.get_client()
        logger.info("[sync] Redis 连接初始化成功")
    except Exception as e:
        # sync 进程的 ERP orchestrator 在 Redis 不可用时会自动降级为串行模式
        logger.warning(f"[sync] Redis 连接失败，将走 fallback 模式 | error={e}")

    # AsyncDB（连接池）
    from core.database import get_db, get_async_db, close_async_db
    db = get_db()
    async_db = await get_async_db()

    # OrgScopedDB schema 反射（让 upsert on_conflict 自动追加 org_id）
    try:
        from core.org_scoped_db import load_composite_org_id_tables
        load_composite_org_id_tables(db)
    except Exception as e:
        logger.error(
            f"[sync] OrgScopedDB schema reflection failed (non-critical) | error={e}"
        )

    # ── 启动常驻 task ───────────────────────────────────────

    tasks: list[asyncio.Task] = []

    # 1. 全局错误监控（loguru ERROR sink → DB 持久化）
    from core.error_alert_sink import error_log_consumer, error_log_cleanup_loop
    tasks.append(asyncio.create_task(
        error_log_consumer(async_db), name="error_log_consumer"
    ))
    tasks.append(asyncio.create_task(
        error_log_cleanup_loop(async_db), name="error_log_cleanup"
    ))

    # 2. ERP 同步编排器 + 健康检查（单实例，无 leader election）
    from services.kuaimai.erp_sync_orchestrator import ErpSyncOrchestrator
    from services.kuaimai.erp_sync_healthcheck import healthcheck_loop

    erp_orchestrator = ErpSyncOrchestrator(async_db)
    tasks.append(asyncio.create_task(
        erp_orchestrator.start(), name="erp_orchestrator"
    ))
    logger.info(f"[sync] ErpSyncOrchestrator started | pid={os.getpid()}")

    tasks.append(asyncio.create_task(
        healthcheck_loop(async_db), name="erp_healthcheck"
    ))
    logger.info("[sync] ErpSyncHealthcheck started")

    # 3. OSS 延迟清理（每天 03:00）
    from services.scheduler.oss_purge_task import oss_purge_loop
    tasks.append(asyncio.create_task(oss_purge_loop(), name="oss_purge"))
    logger.info("[sync] oss_purge_loop started")

    # 4. 快麦 Web 数据同步（每天 10:00，分层兜底 7/30/90 天）
    from services.kuaimai_external.scheduler import kuaimai_external_sync_loop
    tasks.append(asyncio.create_task(
        kuaimai_external_sync_loop(), name="kuaimai_external_sync"
    ))
    logger.info("[sync] kuaimai_external_sync_loop started")

    # 5. 快麦 Cookie 心跳保活（每 10 分钟探活，触发滑动过期续命）
    from services.kuaimai_external.scheduler import kuaimai_external_keepalive_loop
    tasks.append(asyncio.create_task(
        kuaimai_external_keepalive_loop(), name="kuaimai_external_keepalive"
    ))
    logger.info("[sync] kuaimai_external_keepalive_loop started")

    # ── 等待 shutdown 信号 ──────────────────────────────────

    shutdown_event = asyncio.Event()

    def _handle_signal(signame: str) -> None:
        logger.info(f"[sync] received {signame}, initiating graceful shutdown")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig.name)

    await shutdown_event.wait()

    # ── 优雅停止 ────────────────────────────────────────────

    logger.info("[sync] stopping ErpSyncOrchestrator ...")
    try:
        # orchestrator.stop() 内部会 await 所有子 task，确保连接归还
        await asyncio.wait_for(erp_orchestrator.stop(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning("[sync] ErpSyncOrchestrator.stop() timed out")
    except Exception as e:
        logger.error(f"[sync] ErpSyncOrchestrator.stop() error | {e}")

    logger.info("[sync] cancelling remaining tasks ...")
    for t in tasks:
        if not t.done():
            t.cancel()
    # 等所有 task 真正结束，给协程的 finally / __aexit__ 机会归还连接
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for t, r in zip(tasks, results):
        if isinstance(r, asyncio.CancelledError):
            continue
        if isinstance(r, BaseException):
            logger.warning(f"[sync] task {t.get_name()} exited with error | {r}")

    logger.info("[sync] closing DB pool ...")
    try:
        await close_async_db()
    except Exception as e:
        logger.warning(f"[sync] close_async_db error | {e}")

    logger.info("[sync] closing Redis ...")
    try:
        await RedisClient.close()
    except Exception as e:
        logger.warning(f"[sync] RedisClient.close error | {e}")

    logger.info("[sync] Shutdown complete")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
