"""ActorTerminalDelivery 单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.conversation_delivery import ActorTerminalDelivery


class _Query:
    def __init__(self, row):
        self._row = row

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        return SimpleNamespace(data=self._row)


class _DB:
    def __init__(self, task, message=None):
        self.task = task
        self.message = message

    def table(self, name):
        return _Query(self.task if name == "tasks" else self.message)


class _WebSocket:
    def __init__(self):
        self.messages = []

    async def send_to_task_or_user(self, task_id, user_id, message, org_id=None):
        self.messages.append((task_id, user_id, org_id, message))


def _task(status, delivery_context=None):
    return {
        "id": "task-1",
        "external_task_id": "external-1",
        "client_task_id": "client-1",
        "conversation_id": "conv-1",
        "assistant_message_id": "message-1",
        "user_id": "user-1",
        "org_id": "org-1",
        "status": status,
        "request_params": {"_task_slot_id": "slot-1"},
        "error_message": "provider down",
        "delivery_context": delivery_context or {},
    }


def _message():
    return {
        "id": "message-1",
        "conversation_id": "conv-1",
        "content": [{"type": "text", "text": "完成"}],
        "role": "assistant",
        "created_at": "2026-07-17T00:00:00Z",
        "status": "completed",
        "credits_cost": 2,
    }


@pytest.mark.asyncio
async def test_completed_delivery_releases_slot_then_pushes_message(monkeypatch):
    released = []

    async def fake_release(task):
        released.append(task["id"])

    monkeypatch.setattr(
        "services.conversation_delivery.release_task_slot",
        fake_release,
    )
    websocket = _WebSocket()
    delivery = ActorTerminalDelivery(
        _DB(_task("completed"), _message()),
        websocket,
    )

    await delivery.notify(_task("running"), {"outcome": "committed"})

    assert released == ["task-1"]
    assert websocket.messages[0][0] == "client-1"
    assert websocket.messages[0][3]["type"] == "message_done"
    assert websocket.messages[0][3]["payload"]["credits_consumed"] == 2


@pytest.mark.asyncio
async def test_failed_delivery_pushes_message_error(monkeypatch):
    async def fake_release(_task):
        return None

    monkeypatch.setattr(
        "services.conversation_delivery.release_task_slot",
        fake_release,
    )
    websocket = _WebSocket()
    delivery = ActorTerminalDelivery(_DB(_task("failed")), websocket)

    await delivery.notify(_task("running"), {"outcome": "failed"})

    assert websocket.messages[0][3]["type"] == "message_error"
    assert websocket.messages[0][3]["payload"]["error"]["message"] == "provider down"


@pytest.mark.asyncio
async def test_ownership_loss_has_no_delivery_or_slot_release(monkeypatch):
    released = []

    async def fake_release(task):
        released.append(task)

    monkeypatch.setattr(
        "services.conversation_delivery.release_task_slot",
        fake_release,
    )
    websocket = _WebSocket()
    delivery = ActorTerminalDelivery(_DB(_task("completed")), websocket)

    await delivery.notify(_task("running"), {"outcome": "ownership_lost"})

    assert released == []
    assert websocket.messages == []


@pytest.mark.asyncio
async def test_wecom_terminal_releases_slot_and_notifies_web(monkeypatch):
    released = []

    async def fake_release(task):
        released.append(task["id"])

    monkeypatch.setattr(
        "services.conversation_delivery.release_task_slot",
        fake_release,
    )
    task = _task("completed", {"actor": True, "channel": "wecom"})
    websocket = _WebSocket()

    await ActorTerminalDelivery(_DB(task, _message()), websocket).notify(
        _task("running"), {"outcome": "committed"},
    )

    assert released == ["task-1"]
    assert websocket.messages[0][3]["type"] == "message_done"


@pytest.mark.asyncio
async def test_completed_delivery_dispatches_post_commit_hooks(monkeypatch):
    async def fake_release(_task):
        return None

    monkeypatch.setattr(
        "services.conversation_delivery.release_task_slot",
        fake_release,
    )
    task = _task("completed")
    task["model_id"] = "fallback-model"
    task["request_params"] = {
        "content": "用户问题",
        "_retry_from_model": "failed-model",
    }
    task["result"] = {
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    handler = SimpleNamespace(
        org_id=None,
        _dispatch_post_tasks=MagicMock(),
        _extract_retry_knowledge=AsyncMock(),
    )
    delivery = ActorTerminalDelivery(
        _DB(task, _message()),
        _WebSocket(),
        post_handler_factory=lambda: handler,
    )

    await delivery.notify(_task("running"), {"outcome": "committed"})
    await asyncio.sleep(0)

    handler._dispatch_post_tasks.assert_called_once()
    call = handler._dispatch_post_tasks.call_args.kwargs
    assert call["text_content"] == "用户问题"
    assert call["accumulated_text"] == "完成"
    assert call["final_usage"]["prompt_tokens"] == 3
    handler._extract_retry_knowledge.assert_awaited_once_with(
        task_type="chat",
        model_id="fallback-model",
        retry_from_model="failed-model",
    )


@pytest.mark.asyncio
async def test_failed_delivery_dispatches_failure_hooks(monkeypatch):
    async def fake_release(_task):
        return None

    monkeypatch.setattr(
        "services.conversation_delivery.release_task_slot",
        fake_release,
    )
    task = _task("failed")
    task["model_id"] = "model-1"
    handler = SimpleNamespace(
        org_id=None,
        _record_knowledge_metric=AsyncMock(),
        _extract_failure_knowledge=AsyncMock(),
    )
    delivery = ActorTerminalDelivery(
        _DB(task),
        _WebSocket(),
        post_handler_factory=lambda: handler,
    )

    await delivery.notify(_task("running"), {"outcome": "failed"})
    await asyncio.sleep(0)

    handler._record_knowledge_metric.assert_awaited_once()
    handler._extract_failure_knowledge.assert_awaited_once_with(
        task_type="chat",
        model_id="model-1",
        error_message="provider down",
    )
