"""F08-01: real WS → scoped waiter → Policy/Dispatcher; business IO is trapped."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.routes import ws
from tests.test_tool_production_integration import ChatHarness, setup, existing_files
from tests.tool_runtime_support import MockHandlerExecutor


class TaskDB:
    """Only the identity DB is fake; apply real query filters."""
    def __init__(self, row):
        self.row = row

    def table(self, name):
        assert name == "tasks"
        return TaskQuery(self.row)


class TaskQuery:
    def __init__(self, row):
        self.row, self.filters = row, {}

    def select(self, _): return self
    def maybe_single(self): return self
    def eq(self, key, value):
        self.filters[key] = value
        return self
    async def execute(self):
        matches = self.row and all(self.row.get(k) == v for k, v in self.filters.items())
        return SimpleNamespace(data=self.row if matches else None)


def task_row(actor=False):
    return dict(id="00000000-0000-0000-0000-000000000001", client_task_id="task1",
                user_id="u1", conversation_id="c1", status="running", type="chat",
                delivery_context={"actor": actor}, turn_id="turn1")


@pytest.mark.parametrize("scenario", [
    "string_false", "foreign_actor", "true", "false", "legacy_true",
    "wrong_task", "wrong_conversation", "missing_approved", "null", "number",
    "persist_failure", "unknown_task", "foreign_actor_fullscope", "task_wrong_owner", "task_wrong_conversation", "task_stopped",
])
async def test_confirmation_response_cannot_bypass_execution_boundary(setup, monkeypatch, scenario):
    manager, root = setup
    existing_files(root, "fixture.txt")
    monkeypatch.setattr(ws, "ws_manager", manager)
    manager.send_to_connection = AsyncMock()
    row = task_row()
    if scenario == "unknown_task": row = None
    if scenario == "task_wrong_owner": row["user_id"] = "other-user"
    if scenario == "task_wrong_conversation": row["conversation_id"] = "other-conversation"
    if scenario == "task_stopped": row["status"] = "completed"
    db = AsyncMock(return_value=TaskDB(row))
    if scenario == "persist_failure": db.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(ws, "get_async_db", db)
    persist = AsyncMock(wraps=ws._persist_actor_tool_confirmation)
    monkeypatch.setattr(ws, "_persist_actor_tool_confirmation", persist)
    original_wait = manager.wait_for_confirm
    async def bounded_wait(call_id, **kw):
        kw["timeout"] = .05
        return await original_wait(call_id, **kw)
    monkeypatch.setattr(manager, "wait_for_confirm", bounded_wait)
    harness = ChatHarness()
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    executor.tool_confirmer = lambda call, ctx, decision: harness._confirm_tool_call(call, ctx, decision, "m1")
    cache = Mock()
    lifecycle = Mock(replay=AsyncMock(return_value=None), begin=AsyncMock(return_value=None), complete=AsyncMock())

    async def send(*items):
        request = items[-1]
        if request["type"] != "tool_confirm_request":
            return
        payload = {"tool_call_id": request["payload"]["tool_call_id"], "task_id": "task1", "conversation_id": "c1", "approved": True}
        actor = "u1"
        if scenario == "string_false": payload["approved"] = "false"
        if scenario == "false": payload["approved"] = False
        if scenario == "null": payload["approved"] = None
        if scenario == "number": payload["approved"] = 1
        if scenario == "missing_approved": payload.pop("approved")
        if scenario in {"foreign_actor", "legacy_true"}:
            payload.pop("task_id")
            payload.pop("conversation_id")
        if scenario in {"foreign_actor", "foreign_actor_fullscope"}: actor = "other-user"
        if scenario == "wrong_task": payload["task_id"] = "other-task"
        if scenario == "wrong_conversation": payload["conversation_id"] = "other-conversation"
        await ws._handle_message("test-connection", actor, {"type": "tool_confirm_response", "payload": payload}, org_id="o1")

    manager.send_to_task_or_user = AsyncMock(side_effect=send)
    result = await executor.tool_runtime.execute("file_delete", {"files": ["fixture.txt"]}, call_id="danger-call", cache=cache, lifecycle=lifecycle)
    approved = scenario in {"true", "legacy_true"}
    assert executor.handler.await_count == int(approved)
    assert lifecycle.begin.await_count == int(approved)
    if not approved:
        cache.get.assert_not_called()
        assert not result.execution.handler_started
    if scenario == "legacy_true":
        assert persist.await_args.kwargs["task_id"] == "task1"
        assert persist.await_args.kwargs["conversation_id"] == "c1"
    assert (root / "org/o1/u1/fixture.txt").read_text() == "test"
    assert not manager._pending_confirms


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, [], {}])
async def test_untrusted_values_do_not_consume_waiter(bad):
    from services.websocket_manager import WebSocketManager
    manager = WebSocketManager()
    scope = dict(actor_user_id="actor", task_id="task", conversation_id="conversation")
    waiter = asyncio.create_task(manager.wait_for_confirm("approval", timeout=1, **scope))
    await asyncio.sleep(0)
    assert manager.resolve_confirm("approval", bad, **scope) is False
    assert manager.resolve_confirm("approval", True, **scope) is True
    assert await waiter is True


@pytest.mark.parametrize("first", [True, False])
async def test_first_response_wins_and_conflicts_cannot_flip_it(first):
    from services.websocket_manager import WebSocketManager
    manager = WebSocketManager()
    scope = dict(actor_user_id="actor", task_id="task", conversation_id="conversation")
    waiter = asyncio.create_task(manager.wait_for_confirm("approval", timeout=1, **scope))
    await asyncio.sleep(0)
    assert manager.resolve_confirm("approval", first, **scope)
    assert manager.resolve_confirm("approval", first, **scope)
    assert not manager.resolve_confirm("approval", not first, **scope)
    assert await waiter is first
    assert not manager.resolve_confirm("approval", True, **scope)


@pytest.mark.parametrize("bad_scope", [
    {}, {"actor_user_id": "actor"}, {"task_id": "task", "conversation_id": "conversation"},
    {"actor_user_id": "owner", "task_id": "task", "conversation_id": "conversation"},
    {"actor_user_id": "actor", "task_id": "other", "conversation_id": "conversation"},
])
async def test_missing_or_foreign_binding_is_not_optional(bad_scope):
    from services.websocket_manager import WebSocketManager
    manager = WebSocketManager()
    scope = dict(actor_user_id="actor", task_id="task", conversation_id="conversation")
    waiter = asyncio.create_task(manager.wait_for_confirm("approval", timeout=1, **scope))
    await asyncio.sleep(0)
    assert not manager.resolve_confirm("approval", True, **bad_scope)
    assert manager.resolve_confirm("approval", False, **scope)
    assert await waiter is False


@pytest.mark.parametrize("case", ["cancelled", "expired", "duplicate_registration"])
async def test_confirmation_lifetime(case):
    from services.websocket_manager import WebSocketManager
    manager = WebSocketManager()
    scope = dict(actor_user_id="actor", task_id="task", conversation_id="conversation")
    waiter = asyncio.create_task(manager.wait_for_confirm("approval", timeout=1, **scope))
    await asyncio.sleep(0)
    if case == "duplicate_registration":
        with pytest.raises(ValueError, match="already_pending"):
            await manager.wait_for_confirm("approval", **scope)
        assert manager.resolve_confirm("approval", False, **scope)
        assert await waiter is False
    else:
        if case == "expired":
            manager._pending_confirms["approval"].deadline = 0
            assert not manager.resolve_confirm("approval", True, **scope)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError): await waiter
    assert not manager._pending_confirms
    assert not manager.resolve_confirm("approval", True, **scope)


@pytest.mark.parametrize("actor", [True, False])
@pytest.mark.parametrize("case", ["approved", "rejected", "wrong_actor", "missing_scope", "stopped", "wrong_conversation", "db_error"])
async def test_remote_ws_requires_durable_actor_scope(monkeypatch, actor, case):
    from services.websocket_manager import WebSocketManager
    manager = WebSocketManager()  # no local waiter: separate Actor process
    manager.send_to_connection = AsyncMock()
    monkeypatch.setattr(ws, "ws_manager", manager)
    row = task_row(actor=actor)
    if case == "stopped": row["status"] = "completed"
    if case == "wrong_conversation": row["conversation_id"] = "other"
    get_db = AsyncMock(return_value=TaskDB(row))
    if case == "db_error": get_db.side_effect = RuntimeError("db down")
    monkeypatch.setattr(ws, "get_async_db", get_db)
    store = SimpleNamespace(append=AsyncMock(return_value={"already_enqueued": False}))
    monkeypatch.setattr(ws, "DatabaseConversationCommandStore", lambda _: store)
    payload = dict(tool_call_id="opaque-approval", task_id="task1", conversation_id="c1", approved=case != "rejected")
    if case == "missing_scope": payload.pop("conversation_id")
    user = "other-user" if case == "wrong_actor" else "u1"
    await ws._handle_message("connection", user, dict(type="tool_confirm_response", payload=payload), org_id="o1")
    expected = actor and case in {"approved", "rejected"}
    assert store.append.await_count == int(expected)
    if expected:
        from services.conversation_commands import CommandType, ConversationCommand
        command = ConversationCommand(command_id="event", event_id="event", command_type=CommandType.APPROVAL_RESULT,
                                      conversation_id="c1", task_id="task1", payload=store.append.await_args.kwargs["payload"])
        inbox = SimpleNamespace(load_pending=AsyncMock(return_value=[command]), acknowledge=AsyncMock())
        harness = ChatHarness()
        harness._tool_actor_user_id = "u1"
        harness._actor_cancellation_event = asyncio.Event()
        decision = await harness._poll_durable_tool_confirmation(store=inbox, token="token", tool_call_id="opaque-approval",
                                                                  task_id="task1", timeout=1, runtime=Mock())
        assert decision is (case == "approved")
        inbox.acknowledge.assert_not_awaited()
    else:
        manager.send_to_connection.assert_awaited_once()


@pytest.mark.parametrize("existing", [True, False, "true", "false", 1, None])
async def test_persisted_response_retries_compare_exact_boolean(monkeypatch, existing):
    monkeypatch.setattr(ws, "get_async_db", AsyncMock(return_value=TaskDB(task_row(actor=True))))
    store = SimpleNamespace(append=AsyncMock(return_value={"already_enqueued": True, "payload": {"approved": existing}}))
    monkeypatch.setattr(ws, "DatabaseConversationCommandStore", lambda _: store)
    accepted, _ = await ws._persist_actor_tool_confirmation(user_id="u1", task_id="task1", conversation_id="c1",
                                                            tool_call_id="approval", approved=True)
    assert accepted is (existing is True)


async def test_legacy_loop_installs_actor_binding_before_publishing(monkeypatch):
    from services.websocket_manager import WebSocketManager
    from tests.test_tool_confirm import TestRequestUserConfirm
    manager = WebSocketManager()
    monkeypatch.setattr("services.websocket_manager.ws_manager", manager)
    helper = TestRequestUserConfirm()
    loop, context = helper._make_executor(), helper._make_hook_ctx("task1")
    async def respond(*_):
        assert manager.resolve_confirm("approval", True, actor_user_id=context.user_id,
                                       task_id=context.task_id, conversation_id=context.conversation_id)
    manager.send_to_task_or_user = AsyncMock(side_effect=respond)
    assert await loop._request_user_confirm("erp_execute", {}, "approval", context) is None
    assert not manager._pending_confirms
