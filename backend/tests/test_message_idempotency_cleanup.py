"""消息生成幂等记录 TTL 清理循环测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.message_idempotency_cleanup import message_idempotency_cleanup_loop


@pytest.mark.asyncio
async def test_cleanup_calls_rpc_then_stops_on_cancellation() -> None:
    db = MagicMock()
    db.rpc.return_value.execute = AsyncMock(
        return_value=SimpleNamespace(data=3)
    )

    with patch(
        "core.message_idempotency_cleanup.asyncio.sleep",
        new=AsyncMock(side_effect=asyncio.CancelledError),
    ):
        await message_idempotency_cleanup_loop(db)

    db.rpc.assert_called_once_with(
        "cleanup_expired_message_generation_requests",
        {},
    )


@pytest.mark.asyncio
async def test_cleanup_retries_after_database_failure() -> None:
    db = MagicMock()
    db.rpc.return_value.execute = AsyncMock(side_effect=RuntimeError("db down"))
    sleep = AsyncMock(side_effect=asyncio.CancelledError)

    with patch("core.message_idempotency_cleanup.asyncio.sleep", new=sleep):
        await message_idempotency_cleanup_loop(db)

    sleep.assert_awaited_once_with(3600)
