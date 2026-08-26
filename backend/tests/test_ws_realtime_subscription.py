"""WebSocket 实时订阅回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes import ws


@pytest.mark.asyncio
async def test_subscribe_returns_message_id_without_closing_connection(monkeypatch):
    """订阅确认不能引用不存在的局部 task 变量。"""
    manager = SimpleNamespace(
        subscribe_task=AsyncMock(return_value=True),
        send_to_connection=AsyncMock(),
    )
    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(
        ws,
        "_find_task_by_any_id",
        AsyncMock(return_value={"id": "task-1", "org_id": None}),
    )
    monkeypatch.setattr(
        ws,
        "_get_task_delivery_state",
        AsyncMock(return_value={"message_id": "assistant-1"}),
    )
    monkeypatch.setattr(ws, "_check_and_send_completed_task", AsyncMock())

    await ws._handle_message(
        "conn-1",
        "user-1",
        {
            "type": "subscribe",
            "payload": {"task_id": "client-task-1", "last_delivery_seq": 0},
        },
    )

    subscribed = manager.send_to_connection.await_args.args[1]
    assert subscribed["type"] == "subscribed"
    assert subscribed["payload"]["message_id"] == "assistant-1"


@pytest.mark.asyncio
async def test_subscribe_rejects_task_from_another_org_before_registering(monkeypatch):
    manager = SimpleNamespace(
        subscribe_task=AsyncMock(return_value=True),
        send_to_connection=AsyncMock(),
    )
    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(
        ws,
        "_find_task_by_any_id",
        AsyncMock(return_value={"id": "task-1", "org_id": "org-b"}),
    )

    await ws._handle_message(
        "conn-a",
        "user-1",
        {"type": "subscribe", "payload": {"task_id": "task-1"}},
        "org-a",
    )

    manager.subscribe_task.assert_not_awaited()
    error = manager.send_to_connection.await_args.args[1]
    assert error["payload"]["code"] == "SUBSCRIPTION_SCOPE_DENIED"


@pytest.mark.asyncio
async def test_delivery_state_carries_task_message_id(monkeypatch):
    """交付状态需要保留消息 ID，供重连回放建立前端流绑定。"""
    task = {
        "id": "task-1",
        "type": "chat",
        "assistant_message_id": "assistant-1",
        "status": "running",
        "accumulated_content": "已输出",
        "accumulated_blocks": [],
    }

    class Query:
        def __init__(self, data):
            self.data = data

        def select(self, _fields):
            return self

        def eq(self, _field, _value):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return SimpleNamespace(data=self.data)

    class Database:
        def table(self, _name):
            return Query(task)

        def rpc(self, _name, _params):
            return Query({
                "outcome": "found",
                "snapshot_content": "已输出",
                "snapshot_blocks": [],
            })

    monkeypatch.setattr(ws, "get_db", lambda: Database())

    state = await ws._get_task_delivery_state("client-task-1", "user-1", 0)

    assert state["message_id"] == "assistant-1"
