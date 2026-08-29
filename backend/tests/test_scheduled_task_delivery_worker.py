"""定时任务企微投递 Worker 的断线恢复契约。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.scheduler.delivery_worker import (
    ScheduledTaskDeliveryClaim,
    ScheduledTaskDeliveryWorker,
)


class _Call:
    def __init__(self, data):
        self._data = data

    async def execute(self):
        return SimpleNamespace(data=self._data)


class _DB:
    def __init__(self, outcomes):
        self.outcomes = {name: list(values) for name, values in outcomes.items()}
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _Call(self.outcomes[name].pop(0))


def _claim():
    return {
        "outcome": "claimed", "delivery_id": "delivery", "run_id": "run",
        "delivery_kind": "result", "lease_token": "lease", "org_id": "org",
        "target_context": {"type": "wecom_group", "chatid": "chat"},
        "payload": {"text": "日报", "files": []},
    }


def test_claim_rejects_missing_delivery_fencing_data():
    with pytest.raises(RuntimeError, match="CLAIM_INVALID"):
        ScheduledTaskDeliveryClaim.from_result({"outcome": "claimed"})


@pytest.mark.asyncio
async def test_worker_completes_only_after_wecom_sender_accepts_message():
    db = _DB({
        "claim_scheduled_task_delivery": [_claim()],
        "complete_scheduled_task_delivery": [{"outcome": "delivered"}],
    })
    sender = MagicMock()
    sender.send = AsyncMock(return_value=True)

    assert await ScheduledTaskDeliveryWorker(db, sender).run_once() is True

    sender.send.assert_awaited_once()
    context, item = sender.send.call_args.args
    assert context == {"org_id": "org", "transport": "smart_robot", "chatid": "chat"}
    assert item.content == "日报"
    assert db.calls[-1][0] == "complete_scheduled_task_delivery"


@pytest.mark.asyncio
async def test_worker_records_retry_when_ws_is_unavailable():
    db = _DB({
        "claim_scheduled_task_delivery": [_claim()],
        "fail_scheduled_task_delivery": [{"outcome": "retry_scheduled"}],
    })
    sender = MagicMock()
    sender.send = AsyncMock(return_value=False)

    await ScheduledTaskDeliveryWorker(db, sender).run_once()

    assert db.calls[-1][0] == "fail_scheduled_task_delivery"
    assert "WECOM_WS_UNAVAILABLE" in db.calls[-1][1]["p_error"]


@pytest.mark.asyncio
async def test_worker_keeps_running_after_empty_outbox():
    worker = ScheduledTaskDeliveryWorker(
        _DB({"claim_scheduled_task_delivery": [{"outcome": "empty"}]}),
        MagicMock(),
    )

    assert await worker.run_once() is False
