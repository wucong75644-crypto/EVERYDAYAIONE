"""Legacy media polling uses the Worker database scope from Web startup."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.db_scope import (
    SET_DATABASE_SCOPE_SQL,
    DatabaseAccessKind,
    ScopedDatabaseClient,
)
from core.local_db import LocalDBClient
from services import web_database_runtime
from services.background_task_worker import BackgroundTaskWorker


def _raw_worker_db() -> tuple[LocalDBClient, MagicMock]:
    pool = MagicMock()
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    pool.connection.return_value = connection_context
    transaction = MagicMock()
    connection.transaction.return_value = transaction
    cursor = MagicMock()
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection.cursor.return_value = cursor_context
    cursor.description = [("worker_discover_media_tasks",)]
    cursor.fetchall.return_value = [
        {"worker_discover_media_tasks": []},
    ]
    database = object.__new__(LocalDBClient)
    database._pool = pool
    return database, cursor


async def _wait_forever(*_args) -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_web_startup_scopes_legacy_media_discovery_as_worker() -> None:
    runtime_db = MagicMock(name="runtime_db")
    worker_db, cursor = _raw_worker_db()
    async_worker_db = MagicMock(name="async_worker_db")
    settings = MagicMock(
        callback_base_url="",
        poll_interval_seconds=15,
    )

    with (
        patch.object(web_database_runtime, "get_db", return_value=runtime_db),
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
        patch.object(web_database_runtime, "verify_configuration_registry"),
        patch.object(web_database_runtime, "_load_org_schema"),
        patch.object(
            web_database_runtime,
            "_run_startup_recovery",
            new=AsyncMock(),
        ),
        patch.object(BackgroundTaskWorker, "start", new=_wait_forever),
        patch(
            "services.background_task_worker.get_settings",
            return_value=settings,
        ),
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
        patch(
            "services.knowledge_config.close_pg_pools",
            new=AsyncMock(),
        ),
        patch.object(web_database_runtime, "close_async_db", new=AsyncMock()),
        patch.object(
            web_database_runtime,
            "close_async_worker_db",
            new=AsyncMock(),
        ),
        patch.object(web_database_runtime, "close_db"),
        patch.object(web_database_runtime, "close_worker_db"),
    ):
        runtime = await web_database_runtime.start_web_database_runtime()
        discovered = runtime.worker._media_tasks.discover()
        await runtime.stop()

    media_db = runtime.worker._media_tasks._db
    assert isinstance(media_db, ScopedDatabaseClient)
    assert media_db.scope.access_kind is DatabaseAccessKind.WORKER
    assert runtime.worker.db is worker_db
    assert runtime.worker._scheduled_scanner.db is worker_db
    assert discovered == []
    assert cursor.execute.call_args_list[0].args == (
        SET_DATABASE_SCOPE_SQL,
        ("", "", "worker", "legacy-media-worker"),
    )
    assert cursor.execute.call_args_list[1].args == (
        'SELECT public."worker_discover_media_tasks"('
        "p_limit := %s::integer)",
        [100],
    )
