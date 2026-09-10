"""04 acceptance: real Chat/ToolLoop/legacy entrypoints, mock business handlers only."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.agent.agent_result import AgentResult
from services.agent.execution_budget import ExecutionBudget
from services.agent.loop_types import HookContext, LoopConfig, LoopStrategy
from services.agent.tool_loop_executor import ToolLoopExecutor
from services.handlers.chat_tool_mixin import ChatToolMixin
from services.tools import ToolCall
from services.tools.spec import thaw
from services.websocket_manager import WebSocketManager
from tests.tool_runtime_support import IdentityDB, MockHandlerExecutor


class ChatHarness(ChatToolMixin):
    def __init__(self, db=None, org_id="o1"):
        self.db = db or IdentityDB()
        self.org_id = org_id
        self.request_ctx = SimpleNamespace()
        self.execution_scope = None
        self._workspace_user_id = "u1"
        self._personal_context_allowed = True
        self._resource_manifest = None
        self._actor_enabled = False
        self._actor_invocation_store = None
        self._emit_tool_audit = Mock()
        self._push_tool_step_update = AsyncMock()
        self._pending_emit_payloads = []
        self._get_conv_source = Mock(return_value="web")


@pytest.fixture
def setup(monkeypatch, tmp_path):
    from core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "file_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "file_workspace_enabled", True)
    monkeypatch.setattr(settings, "sandbox_enabled", True)
    monkeypatch.setattr("services.oss_service.get_oss_service", lambda: SimpleNamespace(
        bucket=SimpleNamespace(get_object_meta=Mock(return_value=SimpleNamespace(etag="mock-etag")))))
    manager = WebSocketManager()
    manager.send_to_task_or_user = AsyncMock()
    monkeypatch.setattr("services.handlers.chat_tool_mixin.ws_manager", manager)
    return manager, tmp_path


def existing_files(root, *names):
    """Execution now requires real targets even when the business handler is mocked."""
    for name in names:
        path = root / "org/o1/u1" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")


def restore_records(executor):
    async def lookup(filename, *, record_id=None):
        key = record_id or filename
        return {"id": key, "relative_path": f"org/o1/u1/{key}.txt", "oss_object_key": f"mock/{key}"}
    executor._find_deleted_record = lookup


def loop_for(executor):
    loop = ToolLoopExecutor(adapter=Mock(), executor=executor, all_tools=[],
                            config=LoopConfig(max_turns=4, context_window=50000, tool_timeout=3),
                            strategy=LoopStrategy(force_tool_use_first=False), hooks=[])
    loop._emit_payloads = []
    ctx = HookContext(db=executor.db, user_id=executor.user_id, org_id=executor.org_id,
                      conversation_id=executor.conversation_id, task_id=None,
                      request_ctx=None, messages=[], tools_called=[], selected_tools=[])
    return loop, ctx


def tc(name, args=None, call_id="call"):
    return {"id": call_id, "name": name, "arguments": json.dumps(args or {})}


async def invoke(entry, executor, calls, monkeypatch, harness=None):
    if entry == "legacy":
        output = []
        for call in calls:
            try:
                output.append(await executor.execute(call["name"], json.loads(call["arguments"]), call_id=call["id"]))
            except Exception as error:
                output.append(error)
        return output
    if entry == "loop":
        loop, context = loop_for(executor)
        await loop._execute_tools(calls, [], "", context)
        return [m["content"] for m in context.messages if m["role"] == "tool"]
    harness = harness or ChatHarness(executor.db)
    harness.db = executor.db
    monkeypatch.setattr("services.tool_executor.ToolExecutor", lambda **_: executor)
    result = await harness._execute_tool_calls(calls, "task1", "c1", "m1", "u1", 1,
                                               permission_mode=executor.permission_mode,
                                               agent_domain=executor.agent_domain)
    return [r[1] for r in result]


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("name", ["file_search", "web_search"])
async def test_all_entrypoints_new_and_legacy_reach_dispatcher_once(setup, entry, name, monkeypatch):
    executor = MockHandlerExecutor(agent_domain="general")
    executor.handler.return_value = AgentResult(summary="原返回", status="success")
    dispatch = AsyncMock(wraps=executor.tool_runtime.service.dispatcher.dispatch)
    monkeypatch.setattr(executor.tool_runtime.service.dispatcher, "dispatch", dispatch)
    output = await invoke(entry, executor, [tc(name, {"query": "x"})], monkeypatch)
    assert output and executor.handler.await_count == dispatch.await_count == 1


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("case", ["plan_write", "plan_generation", "query_write", "unknown_action", "domain",
                                  "foreign_user", "foreign_org", "foreign_path", "inactive", "unknown_tool"])
async def test_denial_precedes_handler_cache_and_ledger(setup, entry, case, monkeypatch):
    executor = MockHandlerExecutor(agent_domain="general")
    name, args = "web_search", {"query": "x"}
    if case == "plan_write": executor.permission_mode, name, args = "plan", "file_delete", {"files": ["test.txt"]}
    if case == "plan_generation": executor.permission_mode, name = "plan", "generate_image"
    if case == "query_write":
        from services.kuaimai.registry import TRADE_REGISTRY
        executor.agent_domain = "erp"
        name, args = "erp_trade_query", {"action": next(n for n, e in TRADE_REGISTRY.items() if e.is_write)}
    if case == "unknown_action": executor.agent_domain, name, args = "erp", "erp_trade_query", {"action": "not_an_action"}
    if case == "domain": name, args = "erp_trade_query", {"action": "list"}
    if case == "foreign_user": args["user_id"] = "other"
    if case == "foreign_org": args["org_id"] = "other"
    if case == "foreign_path": name, args = "file_search", {"path": "/other-user/private.txt"}
    if case == "inactive": executor.db.active = False
    if case == "unknown_tool": name = "unregistered_tool"
    cache = Mock()
    cache.get.side_effect = AssertionError("unauthorized cache access")
    lifecycle = Mock()
    lifecycle.replay = AsyncMock(side_effect=AssertionError("unauthorized replay"))
    lifecycle.begin = AsyncMock(side_effect=AssertionError("unauthorized invocation"))
    # Exercise the entrypoint and independently assert the same runtime's hooks.
    output = await invoke(entry, executor, [tc(name, args)], monkeypatch)
    result = await executor.tool_runtime.execute(name, args, call_id="hook-denial", cache=cache, lifecycle=lifecycle)
    assert output and result.execution.status == "not_started"
    assert result.execution.handler_started is False
    executor.handler.assert_not_awaited()
    cache.get.assert_not_called()
    lifecycle.replay.assert_not_awaited()
    lifecycle.begin.assert_not_awaited()


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("outcome", ["approved", "rejected", "timeout", "exception", "disconnect"])
async def test_real_confirmation_channel(setup, entry, outcome, monkeypatch):
    manager, root = setup
    path = root / "org/o1/u1/delete.txt"
    path.parent.mkdir(parents=True)
    path.write_text("fixture")
    harness = ChatHarness()
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    executor.tool_confirmer = lambda call, ctx, decision: harness._confirm_tool_call(call, ctx, decision, "m1")
    sent = asyncio.Event()
    async def send(*args):
        message = args[-1]
        if message["type"] != "tool_confirm_request": return
        sent.set()
        if outcome == "disconnect": raise ConnectionError("closed")
        if outcome == "exception": raise RuntimeError("confirmation failed")
        request_id = message["payload"]["tool_call_id"]
        if outcome in {"approved", "rejected"}:
            assert manager.resolve_confirm(request_id, outcome == "approved", task_id="task1", conversation_id="c1")
    manager.send_to_task_or_user.side_effect = send
    if outcome == "timeout":
        original = manager.wait_for_confirm
        manager.wait_for_confirm = lambda call_id, **kw: original(call_id, timeout=.01, task_id=kw["task_id"], conversation_id=kw["conversation_id"])
    output = await invoke(entry, executor, [tc("file_delete", {"files": ["delete.txt"]})], monkeypatch, harness)
    assert sent.is_set()
    assert executor.handler.await_count == (1 if outcome == "approved" else 0)
    assert path.read_text() == "fixture"  # only mock Handler; no real delete
    assert not manager._pending_confirms
    assert output


@pytest.mark.parametrize("change", ["permission_mode", "workspace_user_id", "org_id", "active", "allowed_tools"])
async def test_approval_cannot_survive_scope_or_authorization_change(setup, change):
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    async def approve(*_):
        if change == "permission_mode": executor.permission_mode = "plan"
        if change == "workspace_user_id": executor.workspace_user_id = "other"
        if change == "org_id": executor.org_id = "other"
        if change == "active": executor.db.active = False
        if change == "allowed_tools": executor.allowed_tool_names = frozenset()
        return True
    executor.tool_confirmer = approve
    result = await executor.tool_runtime.execute("file_delete", {"files": ["delete.txt"]}, call_id="same")
    assert not result.execution.handler_started
    executor.handler.assert_not_awaited()


async def test_changed_arguments_get_new_confirmation_and_duplicate_is_single_use(setup):
    existing_files(setup[1], "a.txt", "b.txt")
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    executor.tool_confirmer = AsyncMock(return_value=True)
    runtime = executor.tool_runtime
    one = await runtime.execute("file_delete", {"files": ["a.txt"]}, call_id="same")
    two = await runtime.execute("file_delete", {"files": ["b.txt"]}, call_id="same")
    duplicate = await runtime.execute("file_delete", {"files": ["a.txt"]}, call_id="same")
    assert one.execution.handler_started and not two.execution.handler_started and not duplicate.execution.handler_started
    assert executor.handler.await_count == 1 and executor.tool_confirmer.await_count == 2
    bindings = [c.args[2].confirmation_binding for c in executor.tool_confirmer.call_args_list]
    assert bindings[0] != bindings[1]


@pytest.mark.parametrize("entry", ["chat", "loop"])
async def test_real_read_overlap_and_write_barriers(setup, entry, monkeypatch):
    executor = MockHandlerExecutor(agent_domain="general")
    restore_records(executor)
    both = asyncio.Event()
    release = asyncio.Event()
    trace, running = [], set()
    async def handler(name, args):
        label = args.get("query") or args.get("keyword") or args.get("filename")
        trace.append(("start", label)); running.add(label)
        if label in {"A", "B"}:
            if {"A", "B"} <= running: both.set()
            await release.wait()
        elif label in {"C", "E"}:
            assert running == {label}
        elif label == "D":
            assert ("end", "C") in trace
        await asyncio.sleep(0)
        running.remove(label); trace.append(("end", label))
        return "ok"
    executor.handler.side_effect = handler
    calls = [tc("file_search", {"keyword": "A"}, "9"), tc("web_search", {"query": "B"}, "8"),
             tc("restore_file", {"filename": "C"}, "7"), tc("web_search", {"query": "D"}, "6"),
             tc("restore_file", {"filename": "E"}, "5")]
    task = asyncio.create_task(invoke(entry, executor, calls, monkeypatch))
    await asyncio.wait_for(both.wait(), 2)
    assert trace == [("start", "A"), ("start", "B")] or trace == [("start", "B"), ("start", "A")]
    release.set()
    await task
    assert trace.index(("start", "C")) > max(trace.index(("end", "A")), trace.index(("end", "B")))
    assert trace.index(("start", "D")) > trace.index(("end", "C"))
    assert trace.index(("start", "E")) > trace.index(("end", "D"))


@pytest.mark.parametrize("stage", ["before", "confirm", "handler"])
async def test_cancellation_propagates(setup, stage):
    existing_files(setup[1], "x.txt")
    event = asyncio.Event()
    executor = MockHandlerExecutor(agent_domain="general", cancellation_event=event, task_id="task1")
    started = asyncio.Event()
    async def blocked(*_):
        started.set()
        await asyncio.Event().wait()
    executor.handler.side_effect = blocked
    executor.tool_confirmer = blocked
    if stage == "before": event.set()
    task = asyncio.create_task(executor.tool_runtime.execute(
        "file_delete" if stage == "confirm" else "web_search", {"files": ["x.txt"]}, call_id="c"))
    if stage != "before":
        await asyncio.wait_for(started.wait(), 2)
        if stage == "confirm": event.set()
        else: task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert executor.handler.await_count == (1 if stage == "handler" else 0)


async def test_budget_exhausted_precedes_everything(setup):
    executor = MockHandlerExecutor(execution_budget=ExecutionBudget(max_wall_time=0))
    with pytest.raises(TimeoutError): await executor.execute("web_search", {"query": "x"})
    assert executor.db.reads == []
    executor.handler.assert_not_awaited()


@pytest.mark.parametrize("mode", ["scheduled", "preflight"])
@pytest.mark.parametrize("scope", ["valid", "empty", "missing", "unknown_version", "outside", "dangerous"])
async def test_scheduled_scope_intersection(setup, mode, scope, monkeypatch):
    name = "file_delete" if scope == "dangerous" else "web_search"
    allowed = {name} if scope != "outside" else {"file_search"}
    snapshot = {"version": 99 if scope == "unknown_version" else 1, "allowed_tools": sorted(allowed)}
    if scope == "missing": snapshot = {}
    if scope == "empty": allowed, snapshot = set(), {"version": 1, "allowed_tools": []}
    executor = MockHandlerExecutor(agent_domain="general", task_id="schedule", execution_mode=mode,
                                  allowed_tool_names=allowed, tool_policy_snapshot=snapshot)
    await invoke("loop", executor, [tc(name, {"query": "x", "files": ["x"]})], monkeypatch)
    assert executor.handler.await_count == (1 if scope == "valid" else 0)


async def test_cache_cannot_survive_revoked_membership(setup):
    from services.agent.tool_result_cache import ToolResultCache
    cache = ToolResultCache()
    executor = MockHandlerExecutor(agent_domain="general")
    runtime = executor.tool_runtime
    args = {"query": "x"}
    first = await runtime.execute("web_search", args, call_id="a", cache=cache)
    cached = await runtime.execute("web_search", args, call_id="b", cache=cache)
    executor.db.active = False
    denied = await runtime.execute("web_search", args, call_id="c", cache=cache)
    assert first.execution.handler_started and cached.execution.cached
    assert denied.execution.status == "not_started"
    assert executor.handler.await_count == 1


async def test_real_tool_loop_run_uses_model_order(setup):
    executor = MockHandlerExecutor(agent_domain="general")
    restore_records(executor)
    loop, ctx = loop_for(executor)
    loop._stream_one_turn = AsyncMock(side_effect=[
        ({0: tc("web_search", {"query": "first"}, "z"), 1: tc("restore_file", {"filename": "next"}, "a")}, "", 3, 2, 1),
        ({}, "完成", 3, 2, 1),
    ])
    result = await loop.run([], executor.tool_runtime.advertised(), [], ctx, ExecutionBudget(max_turns=5))
    assert result.is_llm_synthesis and result.text == "完成"
    assert [call.args[0] for call in executor.handler.call_args_list] == ["web_search", "restore_file"]


@pytest.mark.parametrize("actor", [False, True])
@pytest.mark.parametrize("mode", ["auto", "plan"])
async def test_shared_chat_engine_passes_mode_and_retains_safe_points(setup, monkeypatch, actor, mode):
    from services.handlers.chat.execution_engine import _run_loop, ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    from services.handlers.permission_mode import PermissionMode
    from services.handlers.tool_loop_context import ToolLoopContext
    from services.tools.runtime_context import chat_context
    harness = ChatHarness()
    created = []
    def factory(**kw):
        from services.agent.tool_executor import ToolExecutor
        exe = ToolExecutor(**kw)
        exe._handlers["web_search"] = AsyncMock(return_value="read")
        exe._handlers["generate_image"] = AsyncMock(return_value="image")
        created.append(exe)
        return exe
    monkeypatch.setattr("services.tool_executor.ToolExecutor", factory)
    event, budget = asyncio.Event(), ExecutionBudget(max_turns=5)
    context = chat_context(harness, user_id="u1", conversation_id="c1", task_id="task1",
                           permission_mode=mode, budget=budget, cancellation=event)
    prepared = SimpleNamespace(budget=budget, permission=PermissionMode(mode=mode), core_tools=[],
                               execution_context=context, tool_context=ToolLoopContext(org_id="o1", agent_domain="general"), messages=[])
    calls = [tc("web_search", {"query": "x"}, "a"), tc("generate_image", {"prompt": "x"}, "b")]
    monkeypatch.setattr("services.handlers.chat.execution_engine._read_turn", AsyncMock(side_effect=[
        ("", "", calls, set()), ("结束", "", [], set()),
    ]))
    runtime = None
    if actor:
        runtime = Mock(turn_id="turn", command_store=None, execution_token="lease")
        runtime.safe_point = AsyncMock()
        runtime.consume_subtask_completions.return_value = []
        runtime.consume_steer_messages.return_value = []
    sink = AsyncMock()
    sink.emit_empty_thinking = False
    await _run_loop(handler=harness, request=ChatExecutionRequest(
        content=[], user_id="u1", conversation_id="c1", task_id="task1", message_id="m1",
        model_id="mock", context_anchor=None, permission_mode=mode,
    ), prepared=prepared, cancellation_event=event, sink=sink, totals=StreamTotals(), blocks=[], runtime=runtime)
    assert len(created) == 1
    exe = created[0]
    assert exe.permission_mode == mode and exe.agent_domain == "general"
    assert exe.execution_budget is budget and exe.cancellation_event is event
    assert exe._handlers["web_search"].await_count == 1
    assert exe._handlers["generate_image"].await_count == (1 if mode == "auto" else 0)
    if actor:
        from services.conversation_commands import SafePoint
        points = [c.args[0] for c in runtime.safe_point.call_args_list]
        assert SafePoint.BEFORE_TOOL in points and SafePoint.AFTER_TOOL in points


async def test_channel_actor_owner_and_personal_tool_isolation(setup, monkeypatch):
    from services.handlers.chat.execution_scope import ExecutionScope
    scope = ExecutionScope("u1", "channel", "channel-owner", False, "chat-id")
    executor = MockHandlerExecutor(agent_domain="general", workspace_user_id="channel-owner",
                                  context_scope="channel", personal_context_allowed=False,
                                  execution_scope=scope, channel_scope_id="chat-id")
    executor.db.conversation.update(user_id=None, scope_type="channel", scope_id="chat-id", source="wecom")
    await invoke("loop", executor, [tc("file_search", {}), tc("manage_scheduled_task", {"action": "list"}, "personal")], monkeypatch)
    assert [c.args[0] for c in executor.handler.call_args_list] == ["file_search"]
    context = executor.tool_runtime.context()
    assert context.actor_user_id == "u1" and context.workspace_owner_id == "channel-owner"
    assert "manage_scheduled_task" not in {t["function"]["name"] for t in executor.tool_runtime.advertised()}
    executor.db.conversation["scope_id"] = "other-group"
    result = await executor.tool_runtime.execute("file_search", {}, call_id="other-group")
    assert not result.execution.handler_started
    assert executor.handler.await_count == 1


class InvocationStore:
    def __init__(self, row=None, begin="execute"):
        self.row = row
        self.begin_outcome = begin
        self.trace = []
        self.completed = []
    def lookup(self, **kw): self.trace.append("lookup"); return self.row
    def mark_stale(self, **kw): self.trace.append("mark_stale"); return {}
    def begin(self, **kw): self.trace.append("begin"); return {"outcome": self.begin_outcome}
    def complete(self, **kw): self.trace.append("complete"); self.completed.append(kw); return {}


def actor_harness(store):
    h = ChatHarness()
    h._actor_enabled, h._actor_turn_id, h._actor_execution_token = True, "turn", "lease"
    h._actor_invocation_store = store
    return h


@pytest.mark.parametrize("outcome", ["execute", "in_progress", "uncertain", "ownership_lost"])
async def test_invocation_gate_and_legacy_completion(setup, monkeypatch, outcome):
    store = InvocationStore(begin=outcome)
    harness = actor_harness(store)
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    raw = AgentResult(summary="业务失败", status="error", error_message="business_error")
    executor.handler.return_value = raw
    output = await invoke("chat", executor, [tc("generate_image", {"prompt": "x"})], monkeypatch, harness)
    assert executor.handler.await_count == (1 if outcome == "execute" else 0)
    assert store.trace[:3] == ["lookup", "mark_stale", "begin"]
    if outcome == "execute":
        assert store.completed[0]["status"] == "succeeded"
        assert store.completed[0]["result"]["kind"] == "agent_result"
        assert store.completed[0]["result"]["status"] == "error"
        assert output[0] is raw
    else: assert store.completed == []


@pytest.mark.parametrize("failure", ["handler", "delivery", "completion"])
async def test_handler_uncertain_is_separate_from_delivery_failure(setup, monkeypatch, failure):
    store = InvocationStore()
    harness = actor_harness(store)
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    if failure == "handler": executor.handler.side_effect = RuntimeError("external error")
    if failure == "delivery": monkeypatch.setattr("services.handlers.chat_tool_result_mixin.ChatToolResultMixin._process_tool_result", AsyncMock(side_effect=ConnectionError("delivery")))
    if failure == "completion": store.complete = Mock(side_effect=RuntimeError("DB unavailable"))
    try: await invoke("chat", executor, [tc("generate_image", {"prompt": "x"})], monkeypatch, harness)
    except ConnectionError: assert failure == "delivery"
    assert executor.handler.await_count == 1
    if failure != "completion": assert store.completed[0]["status"] == ("uncertain" if failure == "handler" else "succeeded")


async def test_replay_current_permissions_without_business_or_confirmation(setup, monkeypatch):
    from services.tool_invocation_store import hash_tool_arguments
    args = {"prompt": "x"}
    store = InvocationStore(row={"tool_name": "generate_image", "args_hash": hash_tool_arguments(args),
                                 "status": "succeeded", "result": {"kind": "scalar", "value": "原回放"}})
    harness = actor_harness(store)
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    output = await invoke("chat", executor, [tc("generate_image", args)], monkeypatch, harness)
    assert output == ["原回放"] and store.trace == ["lookup"]
    executor.db.active = False
    await invoke("chat", executor, [tc("generate_image", args, "revoked")], monkeypatch, harness)
    assert store.trace == ["lookup"]
    executor.handler.assert_not_awaited()
    assert store.completed == []


async def test_actor_durable_approval_bound_to_arguments_and_owner(setup, monkeypatch):
    from dataclasses import asdict, replace
    from services.tools.file_calls import resolve_file_call
    existing_files(setup[1], "a.txt", "b.txt")
    from services.tools.policy import _digest
    from services.tools.runtime_context import refresh_context, resolve_resources
    from services.conversation_commands import CommandType, ConversationCommand
    manager, _ = setup
    store = InvocationStore()
    harness = actor_harness(store)
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    executor.tool_confirmer = lambda call, ctx, decision: harness._confirm_tool_call(call, ctx, decision, "m1")
    runtime = executor.tool_runtime
    args = {"files": ["a.txt"]}
    context = await refresh_context(executor, runtime.context("call"), runtime.registry)
    prepared = resolve_file_call(executor, "file_delete", args)
    await prepared.prepare()
    context = replace(context, resource_versions=prepared.binding)
    decision = runtime.policy.decide("file_delete", context, prepared.arguments)
    identifier = "tool-approval:" + _digest(asdict(decision.confirmation_binding))
    commands = SimpleNamespace(load_pending=AsyncMock(return_value=[ConversationCommand(
        command_id="approved", command_type=CommandType.APPROVAL_RESULT, conversation_id="c1",
        task_id="task1", turn_id="turn", payload={"tool_call_id": identifier, "user_id": "u1", "approved": True},
    )]))
    harness._actor_command_store = commands
    await invoke("chat", executor, [tc("file_delete", args)], monkeypatch, harness)
    executor.handler.assert_awaited_once()
    requests = [c for c in manager.send_to_task_or_user.call_args_list if c.args[-1]["type"] == "tool_confirm_request"]
    assert requests == []  # pending durable approval resumes without another dialog
    assert store.completed[0]["status"] == "succeeded"
    other_args = resolve_resources(executor, "file_delete", {"files": ["b.txt"]})
    changed = runtime.policy.decide("file_delete", context, other_args)
    assert "tool-approval:" + _digest(asdict(changed.confirmation_binding)) != identifier


@pytest.mark.parametrize("name,args", [("manage_scheduled_task", {"action": "pause", "task_id": "business-task"}),
                                      ("code_execute", {"code": "print(1)"})])
async def test_proposals_and_resource_notices_do_not_add_confirmation(setup, name, args):
    executor = MockHandlerExecutor(agent_domain="general", permission_mode="plan", task_id="task1")
    executor.tool_confirmer = AsyncMock(side_effect=AssertionError("duplicate confirmation"))
    await executor.execute(name, args)
    executor.handler.assert_awaited_once()
    executor.tool_confirmer.assert_not_awaited()


@pytest.mark.parametrize("mode", ["production", "preflight"])
async def test_scheduled_agent_real_assembly_and_loop(setup, monkeypatch, mode):
    from services.agent.scheduled_task_agent import ScheduledTaskAgent
    from services.agent.tool_executor import ToolExecutor
    from tests.test_scheduled_task_agent_integration import FakeAdapter
    from services.agent.loop_hooks import ToolAuditHook
    adapter = FakeAdapter([
        {"tool_calls": [{"id": "a", "name": "web_search", "args": '{"query":"x"}'}]},
        {"text": "已完成只读查询"},
    ])
    monkeypatch.setattr("services.model_gateway.get_model_gateway", lambda: SimpleNamespace(open_chat=lambda _: adapter))
    handler = AsyncMock(return_value=AgentResult(summary="只读数据"))
    monkeypatch.setattr(ToolExecutor, "_web_search", handler)
    monkeypatch.setattr(ToolAuditHook, "on_tool_end", AsyncMock())
    task = {"id": "scheduled", "user_id": "u1", "org_id": "o1", "prompt": "读取", "timeout_sec": 60,
            "execution_policy": {"version": 1, "allowed_tools": ["web_search"], "required_tools": ["web_search"]}}
    result = await ScheduledTaskAgent(IdentityDB(), task, execution_mode=mode).execute()
    assert result.status == "success" and result.text == "已完成只读查询"
    handler.assert_awaited_once_with({"query": "x"})
    assert adapter.closed


async def test_manifest_revocation_after_confirmation_precedes_invocation(setup, monkeypatch):
    from services.handlers.resource_manifest import ResourceManifest, ResourceAsset
    manifest = ResourceManifest("task1", "input", (ResourceAsset("id", "x", "x.txt", "text/plain", 1, ""),), "fixture")
    store, harness = InvocationStore(), ChatHarness()
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1", resource_manifest=manifest)
    executor.resource_manifest_loader = AsyncMock(return_value=manifest)
    async def approve(*_):
        executor.resource_manifest_loader.side_effect = RuntimeError("RESOURCE_MANIFEST_SCOPE_MISMATCH")
        return True
    executor.tool_confirmer = approve
    from services.handlers.chat.tool_lifecycle import ActorToolLifecycle
    harness = actor_harness(store)
    result = await executor.tool_runtime.execute("file_delete", {"files": ["x.txt"]}, call_id="c",
                                                 lifecycle=ActorToolLifecycle(harness, executor.tool_runtime.context("c")))
    assert not result.execution.handler_started
    assert store.trace == ["lookup"] and store.completed == []
    executor.handler.assert_not_awaited()


async def test_business_permission_checker_denies_before_handler(setup, monkeypatch):
    executor = MockHandlerExecutor(agent_domain="general")
    runtime = executor.tool_runtime
    spec = runtime.registry.require("web_search")
    runtime.registry._specs["web_search"] = replace(spec, policy_rules=replace(spec.policy_rules, required_permissions=("task.view",)))
    executor.tool_policy_snapshot = {"permissions": {"task.view": True}}
    checker = AsyncMock(return_value=False)
    monkeypatch.setattr("services.permissions.checker.PermissionChecker.check", checker)
    result = await runtime.execute("web_search", {"query": "x"}, call_id="permission")
    assert result.execution.status == "not_started"
    checker.assert_awaited_once_with("u1", "o1", "task.view")
    executor.handler.assert_not_awaited()


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
async def test_feature_disabled_is_not_a_handler_call(setup, monkeypatch, entry):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), "file_workspace_enabled", False)
    executor = MockHandlerExecutor(agent_domain="general")
    await invoke(entry, executor, [tc("file_search")], monkeypatch)
    assert "file_search" not in {t["function"]["name"] for t in executor.tool_runtime.advertised()}
    executor.handler.assert_not_awaited()


async def test_legacy_snapshot_is_still_an_upper_bound_in_interactive_mode(setup):
    executor = MockHandlerExecutor(agent_domain="general", tool_policy_snapshot={"allowed_tools": ["file_search"]})
    with pytest.raises(PermissionError, match="outside_execution_authorization"):
        await executor.execute("web_search", {"query": "x"})
    executor.handler.assert_not_awaited()


async def test_restored_file_id_uses_manifest_and_preserves_legacy_replay(setup, monkeypatch):
    from services.handlers.resource_manifest import ResourceManifest, ResourceAsset
    from services.agent.file_id import compute_fid
    from services.tool_invocation_store import hash_tool_arguments
    manager, root = setup
    path = str(root / "org/o1/u1/restored.txt")
    manifest = ResourceManifest("task1", "input", (ResourceAsset("asset", "restored.txt", "restored.txt", "text/plain", 1, ""),), "fixture")
    store = InvocationStore(row={"tool_name": "file_delete", "args_hash": hash_tool_arguments({"files": [path]}),
                                 "status": "succeeded", "result": {"kind": "scalar", "value": "已删除"}})
    harness = actor_harness(store)
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1", resource_manifest=manifest)
    executor.tool_confirmer = AsyncMock(side_effect=AssertionError("replay must not ask again"))
    output = await invoke("chat", executor, [tc("file_delete", {"file_ids": [compute_fid("o1", "restored.txt")]})], monkeypatch, harness)
    assert output == ["已删除"] and store.trace == ["lookup"]
    assert not (root / "org").exists()
    executor.handler.assert_not_awaited()
    executor.tool_confirmer.assert_not_awaited()


async def test_restore_record_with_foreign_destination_is_rejected_before_handler(setup):
    executor = MockHandlerExecutor(agent_domain="general")
    executor._find_deleted_record = AsyncMock(return_value={"relative_path": "org/o1/other/private.txt"})
    result = await executor.tool_runtime.execute("restore_file", {"filename": "private.txt"})
    assert not result.execution.handler_started
    executor.handler.assert_not_awaited()


@pytest.mark.parametrize("entry", ["legacy", "loop"])
@pytest.mark.parametrize("kind", ["read", "write", "query_write", "unknown_category"])
async def test_real_erp_action_route(setup, monkeypatch, entry, kind):
    from services.kuaimai.registry import TRADE_REGISTRY
    executor = MockHandlerExecutor(agent_domain="erp", task_id="task1")
    executor.tool_confirmer = AsyncMock(return_value=True)
    action = next(name for name, rule in TRADE_REGISTRY.items() if rule.is_write == (kind != "read"))
    name = "erp_trade_query" if kind in {"read", "query_write"} else "erp_execute"
    args = {"category": "not_known" if kind == "unknown_category" else "trade", "action": action, "params": {}}
    await invoke(entry, executor, [tc(name, args)], monkeypatch)
    assert executor.handler.await_count == (1 if kind in {"read", "write"} else 0)
    assert executor.tool_confirmer.await_count == (1 if kind == "write" else 0)


@pytest.mark.parametrize("entry", ["chat", "loop"])
async def test_batch_cancellation_stops_sibling_reads_and_skips_write(setup, monkeypatch, entry):
    executor = MockHandlerExecutor(agent_domain="general")
    both, cancel = asyncio.Event(), asyncio.Event()
    active, finished = set(), set()
    async def run(name, args):
        active.add(name)
        if len(active) == 2: both.set()
        try:
            await cancel.wait()
            if name == "file_search": raise asyncio.CancelledError()
            await asyncio.Event().wait()
        finally: finished.add(name)
    executor.handler.side_effect = run
    task = asyncio.create_task(invoke(entry, executor, [tc("file_search", {}, "a"), tc("web_search", {"query": "x"}, "b"), tc("restore_file", {"filename": "c"}, "c")], monkeypatch))
    await asyncio.wait_for(both.wait(), 2)
    cancel.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert finished == {"file_search", "web_search"}
    assert executor.handler.await_count == 2


async def test_replay_foreign_artifact_denied_without_business(setup, monkeypatch):
    from services.tool_invocation_store import hash_tool_arguments
    args = {"prompt": "x"}
    store = InvocationStore(row={"tool_name": "generate_image", "args_hash": hash_tool_arguments(args), "status": "succeeded",
                                 "result": {"kind": "agent_result", "summary": "hidden", "status": "success",
                                            "emit_payloads": [{"kind": "file", "workspace_path": "/other-user/private.txt"}]}})
    harness = actor_harness(store)
    executor = MockHandlerExecutor(agent_domain="general", task_id="task1")
    output = await invoke("chat", executor, [tc("generate_image", args)], monkeypatch, harness)
    assert "replay_resource_scope_mismatch" in output[0]
    assert "hidden" not in output[0]
    executor.handler.assert_not_awaited()
    assert store.trace == ["lookup"] and store.completed == []


async def test_chat_reuses_request_service_across_model_rounds(setup, monkeypatch):
    from services.agent.tool_executor import ToolExecutor
    harness = ChatHarness()
    created = []
    handler = AsyncMock(return_value="one")
    def factory(**kw):
        executor = ToolExecutor(**kw)
        executor._handlers["generate_image"] = handler
        created.append(executor)
        return executor
    monkeypatch.setattr("services.tool_executor.ToolExecutor", factory)
    for turn in [1, 2]:
        await harness._execute_tool_calls([tc("generate_image", {"prompt": "x"})], "task1", "c1", "m1", "u1", turn)
    assert len(created) == 1
    handler.assert_awaited_once()


async def test_scheduled_revocation_precedes_template_copy(setup, monkeypatch):
    from services.agent.scheduled_task_agent import ScheduledTaskAgent
    database = IdentityDB()
    database.active = False
    adapter = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr("services.model_gateway.get_model_gateway", lambda: SimpleNamespace(open_chat=lambda _: adapter))
    task = {"id": "scheduled", "user_id": "u1", "org_id": "o1", "prompt": "读取", "timeout_sec": 60,
            "execution_policy": {"version": 1, "allowed_tools": ["web_search"]}}
    agent = ScheduledTaskAgent(database, task)
    agent._prepare_template = AsyncMock()
    result = await agent.execute()
    assert result.status == "error" and result.error_message == "identity_or_authorization_unavailable"
    agent._prepare_template.assert_not_awaited()


async def test_declared_permission_is_loaded_without_trusting_a_prior_boolean(setup, monkeypatch):
    executor = MockHandlerExecutor(agent_domain="general")
    runtime = executor.tool_runtime
    spec = runtime.registry.require("web_search")
    runtime.registry._specs["web_search"] = replace(spec, policy_rules=replace(spec.policy_rules, required_permissions=("task.view",)))
    monkeypatch.setattr("services.permissions.checker.PermissionChecker.check", AsyncMock(return_value=True))
    result = await runtime.execute("web_search", {"query": "x"}, call_id="granted")
    assert result.execution.handler_started
    executor.handler.assert_awaited_once()


async def test_cache_write_failure_preserves_completed_business_and_single_use(setup):
    executor = MockHandlerExecutor(agent_domain="general")
    cache = Mock(get=Mock(return_value=None), put=Mock(side_effect=RuntimeError("cache unavailable")))
    result = await executor.tool_runtime.execute("web_search", {"query": "x"}, call_id="cached", cache=cache)
    assert result.execution.status == "succeeded" and result.execution.handler_started
    duplicate = await executor.tool_runtime.execute("web_search", {"query": "x"}, call_id="cached", cache=cache)
    assert duplicate.execution.status == "not_started"
    executor.handler.assert_awaited_once()
    cache.put.assert_called_once()
