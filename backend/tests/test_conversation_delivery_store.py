"""Conversation Delivery Store 适配器测试。"""

from types import SimpleNamespace

import pytest

from services.conversation_delivery_store import DatabaseConversationDeliveryStore


class _RPC:
    def __init__(self, data):
        self._data = data

    async def execute(self):
        return SimpleNamespace(data=self._data)


class _DB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _RPC(self.responses.pop(0))


@pytest.mark.asyncio
async def test_begin_normalizes_session_data_and_append_preserves_fencing_params():
    db = _DB([
        {
            "outcome": "started",
            "session_id": "session-1",
            "stream_id": "stream-1",
            "execution_attempt": 2,
            "next_seq": 0,
            "snapshot_seq": 0,
            "snapshot_content": "旧内容",
            "snapshot_blocks": [{"type": "text", "text": "旧内容"}],
        },
        {
            "outcome": "appended",
            "delivery_seq": 1,
            "stream_id": "stream-1",
        },
    ])
    store = DatabaseConversationDeliveryStore(db)

    session = await store.begin(
        task_id="task-1",
        execution_token="token-1",
        execution_attempt=2,
        message_id="message-1",
    )
    result = await store.append(
        task_id="task-1",
        execution_token="token-1",
        event_type="message_chunk",
        payload={"chunk": "新增"},
        event_id="event-1",
    )

    assert session.stream_id == "stream-1"
    assert session.snapshot_content == "旧内容"
    assert result["delivery_seq"] == 1
    assert db.calls[1][1]["p_execution_token"] == "token-1"
    assert db.calls[1][1]["p_event_id"] == "event-1"


@pytest.mark.asyncio
async def test_delivery_store_rejects_ownership_loss():
    db = _DB([{"outcome": "ownership_lost"}])
    store = DatabaseConversationDeliveryStore(db)

    with pytest.raises(RuntimeError, match="OWNERSHIP_LOST"):
        await store.begin(
            task_id="task-1",
            execution_token="old-token",
            execution_attempt=1,
            message_id="message-1",
        )
