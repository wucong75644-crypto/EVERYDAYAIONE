"""Web 进程内后台数据库任务的独立 Worker 运行时。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger

from core.database import (
    close_db,
    close_async_db,
    close_async_worker_db,
    close_worker_db,
    get_async_worker_db,
    get_db,
    get_worker_db,
)
from core.redis import RedisClient
from services.background_task_worker import BackgroundTaskWorker
from services.configuration.control_service import verify_configuration_registry


async def warm_knowledge_base() -> None:
    """预热 runtime 知识池，并用独立 Worker 连接导入全局种子。"""
    try:
        from core.db_scope import DatabaseAccessKind, DatabaseScope
        from services.knowledge_config import _get_pg_pool, is_kb_available
        from services.knowledge_service import load_seed_knowledge

        pool = await _get_pg_pool()
        if not pool or not is_kb_available():
            logger.info("Knowledge base not configured or disabled")
            return
        lock_token = await RedisClient.acquire_lock(
            "seed_knowledge_load",
            timeout=60,
        )
        if not lock_token:
            logger.info(
                "Knowledge base seed loading skipped (another worker is loading)"
            )
            return
        try:
            imported = await load_seed_knowledge(
                db_source=DatabaseScope(
                    actor_user_id=None,
                    org_id=None,
                    access_kind=DatabaseAccessKind.WORKER,
                    request_id="seed-knowledge-load",
                ),
            )
            logger.info(f"Knowledge base ready | seed_imported={imported}")
        finally:
            await RedisClient.release_lock("seed_knowledge_load", lock_token)
    except Exception as exc:
        logger.warning(
            "Knowledge base init failed (non-critical) | "
            f"error_type={type(exc).__name__}"
        )


@dataclass
class WebDatabaseRuntime:
    """持有 Web 内后台 Worker 与监控任务的生命周期。"""

    worker: BackgroundTaskWorker
    worker_task: asyncio.Task[Any]
    monitor_tasks: tuple[asyncio.Task[Any], ...]

    async def stop(self) -> None:
        """停止后台任务并关闭 runtime/worker 数据库连接池。"""
        await self.worker.stop()
        self.worker_task.cancel()
        await _await_cancelled(self.worker_task)
        for task in self.monitor_tasks:
            task.cancel()
            await _await_cancelled(task)
        from services.knowledge_config import close_pg_pools

        await close_pg_pools()
        await close_async_db()
        await close_async_worker_db()
        close_db()
        close_worker_db()


async def start_web_database_runtime() -> WebDatabaseRuntime:
    """启动 Web 内所有需要 Worker 数据库身份的后台任务。"""
    runtime_db = get_db()
    worker_db = get_worker_db()
    verify_configuration_registry(runtime_db)
    logger.info("Configuration Registry contract verified")
    _load_org_schema(runtime_db)
    await _run_startup_recovery(worker_db)
    async_worker_db = await get_async_worker_db()

    worker = BackgroundTaskWorker(worker_db, runtime_db=runtime_db)
    worker_task = asyncio.create_task(worker.start())
    logger.info("BackgroundTaskWorker started")

    from core.error_alert_sink import error_log_consumer, error_log_cleanup_loop
    from core.message_idempotency_cleanup import message_idempotency_cleanup_loop

    monitor_tasks = (
        asyncio.create_task(error_log_consumer(async_worker_db)),
        asyncio.create_task(error_log_cleanup_loop(async_worker_db)),
        asyncio.create_task(message_idempotency_cleanup_loop(async_worker_db)),
    )
    return WebDatabaseRuntime(worker, worker_task, monitor_tasks)


def _load_org_schema(runtime_db: Any) -> None:
    try:
        from core.org_scoped_db import load_composite_org_id_tables

        load_composite_org_id_tables(runtime_db)
    except Exception as exc:
        logger.error(
            "OrgScopedDB schema reflection failed (non-critical) | "
            f"error_type={type(exc).__name__}"
        )


async def _run_startup_recovery(worker_db: Any) -> None:
    recovery_lock = await RedisClient.acquire_lock(
        "orphan_task_recovery",
        timeout=30,
    )
    if recovery_lock:
        try:
            from services.task_recovery import recover_orphan_tasks

            recovered = await recover_orphan_tasks(worker_db)
            if recovered > 0:
                logger.info(
                    f"Orphan task recovery completed | recovered={recovered}"
                )
        except Exception as exc:
            logger.error(
                "Orphan task recovery failed (non-critical) | "
                f"error_type={type(exc).__name__}"
            )
        finally:
            await RedisClient.release_lock(
                "orphan_task_recovery",
                recovery_lock,
            )

    reconcile_lock = await RedisClient.acquire_lock(
        "interrupt_anchor_reconcile",
        timeout=30,
    )
    if reconcile_lock:
        try:
            from services.handlers.interrupt_anchor import (
                reconcile_interrupted_messages,
            )

            result = await reconcile_interrupted_messages(worker_db)
            if result.get("reconciled", 0) > 0:
                logger.info(
                    "Interrupt anchor reconcile completed | "
                    f"scanned={result.get('scanned')} | "
                    f"reconciled={result.get('reconciled')}"
                )
        except Exception as exc:
            logger.error(
                "Interrupt anchor reconcile failed (non-critical) | "
                f"error_type={type(exc).__name__}"
            )
        finally:
            await RedisClient.release_lock(
                "interrupt_anchor_reconcile",
                reconcile_lock,
            )


async def _await_cancelled(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass
