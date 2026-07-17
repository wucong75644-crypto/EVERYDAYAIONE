"""WecomDeliveryWorker 租约、检查点和重试测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.wecom.delivery_sender import WecomDeliveryItem
from services.wecom.delivery_worker import WecomDeliveryClaim, WecomDeliveryWorker


class _Call:
    def __init__(self, data):
        self._data = data

    async def execute(self):
        return SimpleNamespace(data=self._data)


class _Query:
    def __init__(self, data):
        self._data = data

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._data)


class _DB:
    def __init__(self, outcomes, task=None, message=None):
        self.outcomes = {name: list(values) for name, values in outcomes.items()}
        self.task = task or {
            "id": "task", "status": "completed",
            "assistant_message_id": "message",
        }
        self.message = message or {
            "id": "message", "content": [{"type": "text", "text": "完成"}],
        }
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _Call(self.outcomes[name].pop(0))

    def table(self, name):
        return _Query(self.task if name == "tasks" else self.message)


def _claim(delivered=None):
    return {
        "outcome": "claimed", "delivery_id": "delivery", "task_id": "task",
        "lease_token": "token", "target_context": {
            "org_id": "org", "transport": "smart_robot", "chatid": "chat",
        },
        "delivered_items": delivered or [],
    }


def test_claim_rejects_missing_fencing_data():
    with pytest.raises(RuntimeError, match="CLAIM_INVALID"):
        WecomDeliveryClaim.from_result({"outcome": "claimed"})


def test_worker_rejects_invalid_timing_and_attempts():
    with pytest.raises(ValueError, match="timing"):
        WecomDeliveryWorker(object(), MagicMock(), lease_seconds=10)
    with pytest.raises(ValueError, match="attempts"):
        WecomDeliveryWorker(object(), MagicMock(), max_attempts=0)


@pytest.mark.asyncio
async def test_worker_checkpoints_each_item_then_completes():
    db = _DB({
        "claim_conversation_delivery": [_claim()],
        "renew_conversation_delivery": [
            {"outcome": "renewed"}, {"outcome": "renewed"},
        ],
        "complete_conversation_delivery": [{"outcome": "delivered"}],
    })
    sender = MagicMock()
    sender.build_items.side_effect = lambda task, message, _context: [
        WecomDeliveryItem("text:0", "text", "A"),
        WecomDeliveryItem("image:1", "image", "url"),
    ]
    sender.send = AsyncMock(return_value=True)

    assert await WecomDeliveryWorker(db, sender).run_once() is True

    assert sender.send.await_count == 2
    complete = [call for call in db.calls if call[0].startswith("complete_")][0]
    assert complete[1]["p_delivered_items"].obj == ["image:1", "text:0"]


@pytest.mark.asyncio
async def test_worker_skips_checkpointed_item_after_retry():
    db = _DB({
        "claim_conversation_delivery": [_claim(["text:0"])],
        "renew_conversation_delivery": [{"outcome": "renewed"}],
        "complete_conversation_delivery": [{"outcome": "delivered"}],
    })
    sender = MagicMock()
    sender.build_items.side_effect = lambda task, message, _context: [
        WecomDeliveryItem("text:0", "text", "A"),
        WecomDeliveryItem("image:1", "image", "url"),
    ]
    sender.send = AsyncMock(return_value=True)

    await WecomDeliveryWorker(db, sender).run_once()

    sender.send.assert_awaited_once()
    assert sender.send.call_args.args[1].key == "image:1"


@pytest.mark.asyncio
async def test_worker_completes_when_sender_skips_chart_only_message():
    db = _DB({
        "claim_conversation_delivery": [_claim(["text:0"])],
        "complete_conversation_delivery": [{"outcome": "delivered"}],
    })
    sender = MagicMock()
    sender.build_items.return_value = []
    sender.send = AsyncMock(return_value=True)

    await WecomDeliveryWorker(db, sender).run_once()

    sender.send.assert_not_awaited()
    complete = [call for call in db.calls if call[0].startswith("complete_")][0]
    assert complete[1]["p_delivered_items"].obj == ["text:0"]


@pytest.mark.asyncio
async def test_worker_schedules_retry_with_saved_checkpoints_on_send_failure():
    db = _DB({
        "claim_conversation_delivery": [_claim(["text:0"])],
        "fail_conversation_delivery": [{"outcome": "retry_scheduled"}],
    })
    sender = MagicMock()
    sender.build_items.side_effect = lambda task, message, _context: [
        WecomDeliveryItem("image:1", "image", "url"),
    ]
    sender.send = AsyncMock(return_value=False)

    await WecomDeliveryWorker(db, sender).run_once()

    failed = [call for call in db.calls if call[0].startswith("fail_")][0]
    assert failed[1]["p_delivered_items"].obj == ["text:0"]


@pytest.mark.asyncio
async def test_worker_stops_when_checkpoint_ownership_is_lost():
    db = _DB({
        "claim_conversation_delivery": [_claim()],
        "renew_conversation_delivery": [{"outcome": "ownership_lost"}],
    })
    sender = MagicMock()
    sender.build_items.side_effect = lambda task, message, _context: [
        WecomDeliveryItem("text:0", "text", "A"),
    ]
    sender.send = AsyncMock(return_value=True)

    await WecomDeliveryWorker(db, sender).run_once()

    assert not any(name.startswith("fail_") for name, _ in db.calls)
    assert not any(name.startswith("complete_") for name, _ in db.calls)


@pytest.mark.asyncio
async def test_worker_returns_false_when_outbox_is_empty():
    db = _DB({"claim_conversation_delivery": [{"outcome": "empty"}]})
    sender = MagicMock()

    assert await WecomDeliveryWorker(db, sender).run_once() is False


@pytest.mark.asyncio
async def test_worker_scan_error_isolated_from_main_loop():
    worker = WecomDeliveryWorker(object(), MagicMock())
    worker.run_once = AsyncMock(side_effect=ConnectionError("db down"))

    assert await worker._run_safely() is False


@pytest.mark.asyncio
async def test_worker_start_stops_after_current_poll_cycle():
    worker = WecomDeliveryWorker(object(), MagicMock())
    worker._run_safely = AsyncMock(return_value=False)

    async def stop_during_wait():
        await worker.stop()

    worker._wait_for_next_poll = AsyncMock(side_effect=stop_during_wait)

    await worker.start()

    worker._run_safely.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_renew_rejects_lost_fencing_token():
    db = _DB({
        "renew_conversation_delivery": [{"outcome": "ownership_lost"}],
    })
    worker = WecomDeliveryWorker(db, MagicMock())
    claim = WecomDeliveryClaim.from_result(_claim())

    with pytest.raises(Exception) as error:
        await worker._renew(claim)

    assert type(error.value).__name__ == "_DeliveryOwnershipLost"
