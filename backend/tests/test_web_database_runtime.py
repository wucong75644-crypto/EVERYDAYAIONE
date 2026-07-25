"""Web 进程后台数据库运行时身份隔离测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.db_scope import DatabaseAccessKind
from services import web_database_runtime


@pytest.mark.asyncio
async def test_warm_knowledge_uses_worker_scope() -> None:
    runtime_pool = MagicMock()
    captured_scope = None

    async def _load_seed(*, db_source):
        nonlocal captured_scope
        captured_scope = db_source
        return 3

    with (
        patch(
            "services.knowledge_config._get_pg_pool",
            new=AsyncMock(return_value=runtime_pool),
        ),
        patch("services.knowledge_config.is_kb_available", return_value=True),
        patch(
            "services.knowledge_service.load_seed_knowledge",
            new=_load_seed,
        ),
        patch.object(
            web_database_runtime.RedisClient,
            "acquire_lock",
            new=AsyncMock(return_value="lock-token"),
        ),
        patch.object(
            web_database_runtime.RedisClient,
            "release_lock",
            new=AsyncMock(),
        ),
    ):
        await web_database_runtime.warm_knowledge_base()

    assert captured_scope is not None
    assert captured_scope.access_kind == DatabaseAccessKind.WORKER


@pytest.mark.asyncio
async def test_web_runtime_separates_runtime_and_worker_clients() -> None:
    runtime_db = MagicMock(name="runtime_db")
    worker_db = MagicMock(name="worker_db")
    async_worker_db = MagicMock(name="async_worker_db")
    worker = MagicMock()
    worker.start = AsyncMock()
    worker.stop = AsyncMock()

    async def _wait_forever(*_args):
        await asyncio.Event().wait()

    with (
        patch.object(web_database_runtime, "get_db", return_value=runtime_db),
        patch.object(
            web_database_runtime,
            "verify_configuration_registry",
        ) as verify_registry,
        patch.object(
            web_database_runtime,
            "get_worker_db",
            return_value=worker_db,
        ),
        patch.object(
            web_database_runtime,
            "get_async_worker_db",
            new=AsyncMock(return_value=async_worker_db),
        ),
        patch.object(web_database_runtime, "_load_org_schema") as load_schema,
        patch.object(
            web_database_runtime,
            "_run_startup_recovery",
            new=AsyncMock(),
        ) as recovery,
        patch.object(
            web_database_runtime,
            "_expire_pending_interactions",
        ) as expire,
        patch.object(
            web_database_runtime,
            "BackgroundTaskWorker",
            return_value=worker,
        ) as worker_factory,
        patch(
            "core.error_alert_sink.error_log_consumer",
            new=_wait_forever,
        ),
        patch(
            "core.error_alert_sink.error_log_cleanup_loop",
            new=_wait_forever,
        ),
        patch(
            "core.message_idempotency_cleanup."
            "message_idempotency_cleanup_loop",
            new=_wait_forever,
        ),
        patch.object(
            web_database_runtime,
            "close_async_db",
            new=AsyncMock(),
        ),
        patch.object(
            web_database_runtime,
            "close_async_worker_db",
            new=AsyncMock(),
        ),
        patch(
            "services.knowledge_config.close_pg_pools",
            new=AsyncMock(),
        ),
        patch.object(web_database_runtime, "close_db"),
        patch.object(web_database_runtime, "close_worker_db"),
    ):
        runtime = await web_database_runtime.start_web_database_runtime()
        await runtime.stop()

    load_schema.assert_called_once_with(runtime_db)
    verify_registry.assert_called_once_with(runtime_db)
    recovery.assert_awaited_once_with(worker_db)
    expire.assert_called_once_with(worker_db)
    worker_factory.assert_called_once_with(
        worker_db,
        runtime_db=runtime_db,
    )


@pytest.mark.asyncio
async def test_startup_recovery_releases_each_lock_with_its_token() -> None:
    worker_db = MagicMock()
    release_lock = AsyncMock()
    with (
        patch.object(
            web_database_runtime.RedisClient,
            "acquire_lock",
            new=AsyncMock(side_effect=["recovery-token", "reconcile-token"]),
        ),
        patch.object(
            web_database_runtime.RedisClient,
            "release_lock",
            new=release_lock,
        ),
        patch(
            "services.task_recovery.recover_orphan_tasks",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "services.handlers.interrupt_anchor."
            "reconcile_interrupted_messages",
            new=AsyncMock(return_value={"reconciled": 0}),
        ),
    ):
        await web_database_runtime._run_startup_recovery(worker_db)

    assert release_lock.await_args_list == [
        call("orphan_task_recovery", "recovery-token"),
        call("interrupt_anchor_reconcile", "reconcile-token"),
    ]
