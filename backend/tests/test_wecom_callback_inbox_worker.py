import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.wecom.callback_inbox_worker import WecomCallbackInboxWorker


ITEM = {
    "id": "inbox-1",
    "org_id": "org-1",
    "corp_id": "corp-1",
    "lease_token": "lease-1",
    "payload": {
        "xml_content": (
            "<xml><MsgType>text</MsgType><Content>你好</Content>"
            "<FromUserName>user-1</FromUserName><MsgId>msg-1</MsgId></xml>"
        ),
    },
}


def _worker() -> WecomCallbackInboxWorker:
    worker = object.__new__(WecomCallbackInboxWorker)
    worker._runtime_db = MagicMock()
    worker._worker_db = MagicMock()
    worker._complete = MagicMock()
    worker._fail = MagicMock()
    worker._maintenance_db = MagicMock()
    worker._next_cleanup_at = time.monotonic() + 3600
    return worker


@pytest.mark.asyncio
async def test_successful_callback_completes_lease() -> None:
    worker = _worker()
    config = SimpleNamespace(
        corp_id="corp-1",
        agent_id="10001",
        agent_secret="secret",
    )
    service = MagicMock()
    service.handle_message = AsyncMock(return_value=True)
    with (
        patch(
            "services.wecom.callback_inbox_worker"
            ".resolve_wecom_callback_config",
            return_value=config,
        ),
        patch(
            "services.wecom.callback_inbox_worker.WecomMessageService",
            return_value=service,
        ),
    ):
        await worker._process(ITEM)

    worker._complete.assert_called_once_with(ITEM)
    worker._fail.assert_not_called()


@pytest.mark.asyncio
async def test_failed_message_releases_lease_for_retry() -> None:
    worker = _worker()
    config = SimpleNamespace(
        corp_id="corp-1",
        agent_id="10001",
        agent_secret="secret",
    )
    service = MagicMock()
    service.handle_message = AsyncMock(return_value=False)
    with (
        patch(
            "services.wecom.callback_inbox_worker"
            ".resolve_wecom_callback_config",
            return_value=config,
        ),
        patch(
            "services.wecom.callback_inbox_worker.WecomMessageService",
            return_value=service,
        ),
    ):
        await worker._process(ITEM)

    worker._complete.assert_not_called()
    worker._fail.assert_called_once()


@pytest.mark.asyncio
async def test_claim_failure_does_not_stop_worker() -> None:
    worker = _worker()
    worker._claim = MagicMock(
        side_effect=[RuntimeError("database unavailable"), None],
    )
    sleeps = 0

    async def controlled_sleep(_delay: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError

    with patch(
        "services.wecom.callback_inbox_worker.asyncio.sleep",
        side_effect=controlled_sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            await worker.run()

    assert worker._claim.call_count == 2


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_block_claiming() -> None:
    worker = _worker()
    worker._next_cleanup_at = 0
    worker._maintenance_db.rpc.side_effect = RuntimeError("cleanup unavailable")
    worker._claim = MagicMock(return_value=None)
    sleeps = 0

    async def controlled_sleep(_delay: float) -> None:
        nonlocal sleeps
        sleeps += 1
        raise asyncio.CancelledError

    with patch(
        "services.wecom.callback_inbox_worker.asyncio.sleep",
        side_effect=controlled_sleep,
    ):
        with pytest.raises(asyncio.CancelledError):
            await worker.run()

    worker._claim.assert_called_once()
