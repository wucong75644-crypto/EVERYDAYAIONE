"""WebSocket 工具确认的任务归属与 Actor 持久化边界测试。"""

from __future__ import annotations

from types import SimpleNamespace
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes import ws
from services.conversation_commands import CommandType, ConversationCommand
from services.handlers.chat_tool_mixin import ChatToolMixin


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
    def __init__(self, row):
        self.row = row

    def table(self, _name):
        return _Query(self.row)


def _task(*, actor: bool, conversation_id: str = "conversation-1"):
    return {
        "id": "task-1",
        "type": "chat",
        "status": "running",
        "conversation_id": conversation_id,
        "turn_id": "turn-1",
        "delivery_context": {"actor": actor},
    }


@pytest.mark.asyncio
async def test_actor_confirmation_is_persisted_with_scope():
    db = _DB(_task(actor=True))
    store = AsyncMock()
    store.append.return_value = {
        "outcome": "enqueued",
        "already_enqueued": False,
    }

    with patch.object(ws, "get_async_db", new=AsyncMock(return_value=db)), \
         patch.object(ws, "DatabaseConversationCommandStore", return_value=store):
        persisted, code = await ws._persist_actor_tool_confirmation(
            user_id="user-1",
            task_id="task-1",
            conversation_id="conversation-1",
            tool_call_id="tool-1",
            approved=True,
        )

    assert persisted is True
    assert code == ""
    store.append.assert_awaited_once()
    kwargs = store.append.await_args.kwargs
    assert kwargs["command_type"].value == "approval_result"
    assert kwargs["dedupe_key"] == "approval:tool-1"
    assert kwargs["payload"]["approved"] is True


@pytest.mark.asyncio
async def test_non_actor_confirmation_keeps_legacy_path():
    db = _DB(_task(actor=False))

    with patch.object(ws, "get_async_db", new=AsyncMock(return_value=db)):
        persisted, code = await ws._persist_actor_tool_confirmation(
            user_id="user-1",
            task_id="task-1",
            conversation_id="conversation-1",
            tool_call_id="tool-1",
            approved=False,
        )

    assert persisted is False
    assert code == "CONFIRM_LEGACY"


@pytest.mark.asyncio
async def test_actor_confirmation_rejects_wrong_conversation():
    db = _DB(_task(actor=True, conversation_id="conversation-1"))

    with patch.object(ws, "get_async_db", new=AsyncMock(return_value=db)):
        persisted, code = await ws._persist_actor_tool_confirmation(
            user_id="user-1",
            task_id="task-1",
            conversation_id="conversation-evil",
            tool_call_id="tool-1",
            approved=True,
        )

    assert persisted is False
    assert code == "CONFIRM_SCOPE_INVALID"


@pytest.mark.asyncio
async def test_durable_approval_returns_to_runtime_inbox_before_safe_point():
    command = ConversationCommand(
        command_id="event-1",
        event_id="event-1",
        command_type=CommandType.APPROVAL_RESULT,
        conversation_id="conversation-1",
        task_id="task-1",
        payload={"tool_call_id": "tool-1", "approved": True, "user_id": "user-1"},
    )
    store = MagicMock()
    store.load_pending = AsyncMock(return_value=[command])
    store.acknowledge = AsyncMock()
    runtime = MagicMock()
    mixin = MagicMock()
    mixin._actor_cancellation_event = asyncio.Event()
    mixin._tool_actor_user_id = "user-1"

    approved = await ChatToolMixin._poll_durable_tool_confirmation(
        mixin,
        store=store,
        token="token-1",
        tool_call_id="tool-1",
        task_id="task-1",
        timeout=1.0,
        runtime=runtime,
    )

    assert approved is True
    runtime.push.assert_called_once_with(command)
    store.acknowledge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", [None, "fb7da576-4aef-4dc4-a5ff-a53728d3f460"])
async def test_actor_steer_is_durable_and_wakes_conversation_worker(request_id):
    task = _task(actor=True)
    control_tasks = SimpleNamespace(
        running=task,
        paused=None,
        to_router_state=lambda: {},
    )
    store = AsyncMock()
    store.append.return_value = {"outcome": "enqueued", "event_id": "saved-steer", "payload": {"message": "请改为简短回答"}}
    wakeup = MagicMock()
    wakeup.publish = AsyncMock()

    with patch.object(ws, "get_db", return_value=MagicMock()), \
         patch.object(ws, "get_async_db", new=AsyncMock(return_value=MagicMock())), \
         patch.object(ws, "load_control_tasks", return_value=control_tasks), \
         patch.object(
             ws.ConversationControlRouter,
             "route",
             new_callable=AsyncMock,
             return_value=SimpleNamespace(action=ws.ControlAction.NONE),
         ), \
         patch.object(ws, "DatabaseConversationCommandStore", return_value=store), \
         patch.object(ws.ws_manager, "send_to_connection", new_callable=AsyncMock) as send, \
         patch(
             "services.conversation_worker.RedisConversationWakeup",
             return_value=wakeup,
         ):
        await ws._handle_message(
            "conn-1",
            "user-1",
            {
                "type": "user_steer",
                "payload": {
                    "task_id": "task-1",
                    "conversation_id": "conversation-1",
                    "message": "请改为简短回答",
                    **({"request_id": request_id} if request_id else {}),
                },
            },
            org_id="org-1",
        )

    store.append.assert_awaited_once()
    kwargs = store.append.await_args.kwargs
    assert kwargs["command_type"] is CommandType.STEER
    assert kwargs["payload"] == {"message": "请改为简短回答"}
    wakeup.publish.assert_awaited_once_with("conversation-1", "org-1")
    if request_id:
        assert kwargs["dedupe_key"] == f"steer:{request_id}"
        receipt = send.await_args.args[1]
        assert receipt["type"] == "steer_result"
        assert receipt["payload"] == {"request_id": request_id, "outcome": "enqueued", "event_id": "saved-steer"}
    else:
        send.assert_not_awaited()


@pytest.mark.asyncio
async def test_steer_retry_reads_original_receipt_after_task_has_ended():
    request_id = "fb7da576-4aef-4dc4-a5ff-a53728d3f460"
    with patch.object(ws, "get_db", return_value=MagicMock()), \
         patch.object(ws, "_find_steer_receipt", return_value="saved-steer") as find, \
         patch.object(ws, "load_control_tasks") as active, \
         patch.object(ws.ws_manager, "send_to_connection", new_callable=AsyncMock) as send:
        await ws._handle_message("conn-1", "user-1", {
            "type": "user_steer", "payload": {
                "task_id": "task-1", "conversation_id": "conversation-1",
                "request_id": request_id, "message": "改为白底", "retry": True,
            },
        }, org_id="org-1")
    active.assert_not_called()
    assert find.call_args.kwargs == {
        "task_id": "task-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "org_id": "org-1", "request_id": request_id, "message": "改为白底", "attachments": (),
    }
    assert send.await_args.args[1]["payload"]["event_id"] == "saved-steer"


@pytest.mark.asyncio
async def test_steer_completion_race_returns_closed_receipt_instead_of_disconnect():
    task = _task(actor=True)
    control_tasks = SimpleNamespace(running=task, paused=None, to_router_state=lambda: {})
    store = AsyncMock()
    store.append.side_effect = RuntimeError('ACTOR_STEER_SCOPE_MISMATCH')
    with patch.object(ws, 'get_db', return_value=MagicMock()), \
         patch.object(ws, 'get_async_db', new=AsyncMock(return_value=MagicMock())), \
         patch.object(ws, '_find_steer_receipt', return_value=None), \
         patch.object(ws, '_steer_window_closed', return_value=True), \
         patch.object(ws, 'load_control_tasks', return_value=control_tasks), \
         patch.object(ws.ConversationControlRouter, 'route', new=AsyncMock(return_value=SimpleNamespace(action=ws.ControlAction.NONE))), \
         patch.object(ws, 'DatabaseConversationCommandStore', return_value=store), \
         patch.object(ws.ws_manager, 'send_to_connection', new_callable=AsyncMock) as send:
        await ws._handle_message('conn-1', 'user-1', {'type': 'user_steer', 'payload': {
            'task_id': 'task-1', 'conversation_id': 'conversation-1', 'message': '补充：第5条改为海边灯塔。',
            'request_id': 'fb7da576-4aef-4dc4-a5ff-a53728d3f460'}}, org_id='org-1')
    assert send.await_args.args[1]['payload']['outcome'] == 'window_closed'
    store.append.assert_awaited_once()


@pytest.mark.asyncio
async def test_steer_receipt_is_durable_before_best_effort_worker_wakeup():
    task = _task(actor=True)
    store = AsyncMock()
    store.append.return_value = {'outcome': 'enqueued', 'event_id': 'saved', 'payload': {'message': '追加要求'}}
    wakeup = MagicMock()
    async def unavailable(*args):
        assert send.await_count == 1
        assert send.await_args.args[1]['payload']['outcome'] == 'enqueued'
        raise ConnectionError('wakeup unavailable')
    wakeup.publish = AsyncMock(side_effect=unavailable)
    with patch.object(ws, 'get_db', return_value=MagicMock()), \
         patch.object(ws, 'get_async_db', new=AsyncMock(return_value=MagicMock())), \
         patch.object(ws, '_find_steer_receipt', return_value=None), \
         patch.object(ws, 'load_control_tasks', return_value=SimpleNamespace(running=task, paused=None, to_router_state=lambda: {})), \
         patch.object(ws.ConversationControlRouter, 'route', new=AsyncMock(return_value=SimpleNamespace(action=ws.ControlAction.NONE))), \
         patch.object(ws, 'DatabaseConversationCommandStore', return_value=store), \
         patch.object(ws.ws_manager, 'send_to_connection', new_callable=AsyncMock) as send, \
         patch('services.conversation_worker.RedisConversationWakeup', return_value=wakeup):
        await ws._handle_message('conn-1', 'user-1', {'type': 'user_steer', 'payload': {
            'task_id': 'task-1', 'conversation_id': 'conversation-1', 'message': '追加要求',
            'request_id': 'fb7da576-4aef-4dc4-a5ff-a53728d3f460'}}, org_id='org-1')
    store.append.assert_awaited_once()
    assert send.await_count == 1


@pytest.mark.parametrize(('changes', 'closed'), [({}, True), ({'user_id': 'other'}, False),
    ({'org_id': 'other'}, False), ({'conversation_id': 'other'}, False), ({'status': 'running'}, False)])
def test_closed_window_receipt_requires_owned_scope_and_no_existing_event(changes, closed):
    from tests.conftest import MockSupabaseClient
    db = MockSupabaseClient()
    task = _task(actor=True)
    task.update(id='00000000-0000-4000-8000-000000000001', status='completed', user_id='user-1',
                org_id='org-1', conversation_id='conversation-1', type='chat')
    task.update(changes)
    db.set_table_data('tasks', [task])
    args = dict(task_id='00000000-0000-4000-8000-000000000001', conversation_id='conversation-1',
                user_id='user-1', org_id='org-1', request_id='request')
    assert ws._steer_window_closed(db, **args) is closed
    if closed:
        db.set_table_data('conversation_control_events', [{'id': 'event', 'task_id': task['id'],
            'event_type': 'steer', 'dedupe_key': 'steer:request'}])
        assert not ws._steer_window_closed(db, **args)
