"""独立 Worker 数据库客户端合同。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import database


def _settings(worker_url: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        worker_database_url=worker_url,
        db_pool_min=1,
        db_pool_max=2,
    )


def test_worker_db_requires_dedicated_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_worker_db_client", None)
    monkeypatch.setattr(database, "get_settings", lambda: _settings(None))

    with pytest.raises(RuntimeError, match="WORKER_DATABASE_URL_REQUIRED"):
        database.get_worker_db()


def test_worker_db_uses_only_worker_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(database, "_worker_db_client", None)
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: _settings("postgresql://worker/db"),
    )

    with patch("core.local_db.LocalDBClient", factory):
        result = database.get_worker_db()

    assert result is client
    factory.assert_called_once_with(
        "postgresql://worker/db",
        min_size=1,
        max_size=2,
    )


@pytest.mark.asyncio
async def test_async_worker_db_opens_and_closes_dedicated_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.open = AsyncMock()
    client.close = AsyncMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(database, "_async_worker_db_client", None)
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: _settings("postgresql://worker/db"),
    )

    with patch("core.local_db.AsyncLocalDBClient", factory):
        result = await database.get_async_worker_db()
        await database.close_async_worker_db()

    assert result is client
    client.open.assert_awaited_once()
    client.close.assert_awaited_once()
    assert database._async_worker_db_client is None


def test_close_worker_db_resets_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr(database, "_worker_db_client", client)

    database.close_worker_db()

    client.close.assert_called_once()
    assert database._worker_db_client is None


def test_close_runtime_db_resets_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr(database, "_local_db_client", client)

    database.close_db()

    client.close.assert_called_once()
    assert database._local_db_client is None
