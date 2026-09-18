"""Actor model-loop barriers, checkpoints, resume/cancel and disabled regression."""

import asyncio
import copy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent.execution_budget import ExecutionBudget
from services.conversation_commands import CommandType, ConversationCommand, SafePoint
from services.conversation_state import ConversationPauseRequested, ConversationStopRequested
from services.conversation_turn_runtime import ConversationTurnRuntime
from services.handlers.chat.execution_engine import _execute_tools, _run_loop, execute_chat
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.handlers.permission_mode import PermissionMode
from services.handlers.tool_loop_context import ToolLoopContext
from services.tools import build_legacy_catalog
from services.tools.runtime_context import catalog_context
from tests.test_chat_execution_engine import _request
from tests.test_skill_runtime import Source, activate, state


def prepared():
    registry = build_legacy_catalog()
    return SimpleNamespace(
        execution_context=catalog_context("org-1"), messages=[],
        budget=ExecutionBudget(max_turns=10, max_wall_time=60),
        permission=PermissionMode(mode="auto"),
        core_tools=[registry.require(name).to_schema() for name in ("file_search", "web_search")],
        tool_context=ToolLoopContext(org_id="org-1", agent_domain="general"),
        adapter=SimpleNamespace(close=AsyncMock()),
    )


def actor(callback=None):
    return ConversationTurnRuntime(
        conversation_id="conv-1", task_id="task-1", turn_id="turn-1",
        cancellation_event=asyncio.Event(), replay_checkpoint_callback=callback,
    )


def call(name="activate_skill", args=None, id="activate-1"):
    return {"id": id, "name": name, "arguments": args if args is not None else activate(topic="orders")}


def handler():
    return SimpleNamespace(org_id="org-1", _execute_tool_calls=AsyncMock(return_value=[]),
                           _get_conv_source=lambda _: "web", _pending_emit_payloads=[],
                           _calculate_credits=lambda _: 0)


async def batch(runtime, prepared, calls, h=None):
    h = h or handler()
    blocks = []
    await _execute_tools(
        handler=h, request=_request(), prepared=prepared, turn=0, turn_text="",
        calls=calls, previewed_call_ids=set(), cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(), blocks=blocks, runtime=runtime, next_model_round=1,
    )
    return h, blocks


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("reverse", [True, False])
async def test_activation_barrier_blocks_every_business_call_even_when_activation_fails(valid, reverse):
    runtime = actor()
    runtime.skill_runtime = state()
    await runtime.skill_runtime.initialize()
    calls = [call(args=None if valid else "bad json"), call("web_search", "{}", "business")]
    p = prepared()
    h, blocks = await batch(runtime, p, list(reversed(calls)) if reverse else calls)
    h._execute_tool_calls.assert_not_awaited()
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in p.messages if m["role"] == "tool"}
    assert results["business"] == {"ok": False, "code": "SKILL_ACTIVATION_BARRIER", "message": "请下一轮重新请求"}
    assert results["activate-1"]["ok"] is valid
    assert all(b["status"] != "running" for b in blocks)


async def test_duplicate_activate_in_same_batch_is_idempotent():
    runtime = actor()
    runtime.skill_runtime = state()
    await runtime.skill_runtime.initialize()
    p = prepared()
    await batch(runtime, p, [call(), call(id="activate-2")])
    runtime.skill_runtime.source.load.assert_awaited_once()
    assert len([m for m in p.messages if "Turn Skill instructions" in (m.get("content") or "")]) == 1
    assert json.loads([m for m in p.messages if m["role"] == "tool"][1]["content"])["code"] == "SKILL_ALREADY_ACTIVE"


async def test_next_model_round_readvertises_and_passes_narrowed_scope_to_execution(monkeypatch):
    runtime = actor()
    runtime.skill_runtime = state()
    await runtime.skill_runtime.initialize()
    p, h, seen = prepared(), handler(), []

    async def model(prep, tools, *args, **kwargs):
        seen.append({t["function"]["name"] for t in tools})
        if len(seen) == 1:
            return "", "", [call(), call("web_search", "{}", "deferred")], set()
        if len(seen) == 2:
            assert "Read orders." in json.dumps(prep.messages)
            return "", "", [call("file_search", "{}", "allowed")], set()
        return "done", "", [], set()

    monkeypatch.setattr("services.handlers.chat.execution_engine._read_turn", model)
    monkeypatch.setattr("services.handlers.chat.execution_engine.compact_tool_context", AsyncMock())
    await _run_loop(handler=h, request=_request(), prepared=p, cancellation_event=runtime.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=[], runtime=runtime)
    assert seen[0] == {"file_search", "web_search", "activate_skill"}
    assert seen[1] == {"file_search", "activate_skill"}
    h._execute_tool_calls.assert_awaited_once()
    assert h._execute_tool_calls.await_args.kwargs["authorized_tool_names"] == {"file_search"}


@pytest.mark.parametrize("command,exception", [(CommandType.PAUSE, ConversationPauseRequested),
                                                (CommandType.CANCEL, ConversationStopRequested)])
async def test_activation_checkpoint_precedes_pause_or_cancel_and_resumes_exactly(command, exception):
    checkpoints = []

    async def save(point, payload):
        checkpoints.append((point, copy.deepcopy(payload)))
        return {"outcome": "saved"}

    runtime = actor(save)
    source = Source()
    runtime.skill_runtime = state(source)
    await runtime.skill_runtime.initialize()
    load = source._load

    async def interrupt(c):
        result = await load(c)
        runtime.push(ConversationCommand("interrupt", command, "conv-1", "task-1", "turn-1"))
        return result

    source.load.side_effect = interrupt
    p, h = prepared(), handler()
    with pytest.raises(exception):
        await batch(runtime, p, [call(), call("file_search", "{}", "deferred")], h)
    h._execute_tool_calls.assert_not_awaited()
    point, checkpoint = checkpoints[-1]
    assert point is SafePoint.AFTER_SKILL_ACTIVATION
    assert checkpoint["next_model_round"] == 1
    assert len([m for m in checkpoint["messages"] if m["role"] == "tool"]) == 2
    assert checkpoint["skill_runtime"]["active"][0]["effective_allowed_tool_names"] == ["file_search"]
    if command is CommandType.PAUSE:
        resumed = state(source)
        source.load.side_effect = source._load
        await resumed.initialize(checkpoint["skill_runtime"])
        messages = copy.deepcopy(checkpoint["messages"])
        resumed.ensure_messages(messages)
        assert messages == checkpoint["messages"]
        assert (await resumed.activate(activate(topic="orders")))["code"] == "SKILL_ALREADY_ACTIVE"


async def test_cancellation_before_activation_does_not_load_or_dispatch():
    runtime = actor()
    runtime.skill_runtime = state()
    await runtime.skill_runtime.initialize()
    runtime.push(ConversationCommand("cancel", CommandType.CANCEL, "conv-1", "task-1", "turn-1"))
    h = handler()
    with pytest.raises(ConversationStopRequested):
        await batch(runtime, prepared(), [call()], h)
    runtime.skill_runtime.source.load.assert_not_awaited()
    h._execute_tool_calls.assert_not_awaited()


async def test_disabled_actor_never_advertises_or_dispatches_control_calls(monkeypatch):
    runtime, p, h = actor(), prepared(), handler()
    seen = []

    async def model(_prep, tools, *args, **kwargs):
        seen.extend(t["function"]["name"] for t in tools)
        return "done", "", [], set()

    monkeypatch.setattr("services.handlers.chat.execution_engine._read_turn", model)
    await _run_loop(handler=h, request=_request(), prepared=p, cancellation_event=runtime.cancellation_event,
                    sink=CollectingExecutionSink(), totals=StreamTotals(), blocks=[], runtime=runtime)
    assert seen == ["file_search", "web_search"]
    await batch(runtime, p, [call()], h)
    h._execute_tool_calls.assert_not_awaited()
    assert json.loads([m for m in p.messages if m["role"] == "tool"][-1]["content"])["code"] == "SKILL_RUNTIME_DISABLED"


async def test_execute_chat_restores_before_model_and_closes_gateway_on_invalid_revision(monkeypatch):
    from core.config import get_settings
    settings = get_settings().model_copy(update={"skill_runtime_enabled": True, "skill_catalog_enabled": True})
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    source = Source()
    skills = state(source)
    await skills.initialize()
    await skills.activate(activate(topic="orders"))
    from services.skills.contracts import SkillError
    source.load.side_effect = SkillError("SKILL_PINNED_REVISION_UNAVAILABLE")
    monkeypatch.setattr("services.skills.runtime_source.ActorSkillSource", lambda *_: source)
    p = prepared()
    monkeypatch.setattr("services.handlers.chat.execution_engine.prepare_chat_stream", AsyncMock(return_value=p))
    model = AsyncMock()
    monkeypatch.setattr("services.handlers.chat.execution_engine._run_loop", model)
    from services.skills.runtime import SkillReplayError
    with pytest.raises(SkillReplayError, match="PINNED_REVISION"):
        await execute_chat(handler=handler(), runtime=actor(), request=replace(_request(), replay_context={
            "messages": [], "content_blocks": [], "skill_runtime": skills.checkpoint(),
        }))
    model.assert_not_awaited()
    p.adapter.close.assert_awaited_once()


async def test_full_actor_pause_resume_reuses_revision_messages_and_model_round(monkeypatch):
    from core.config import get_settings
    settings = get_settings().model_copy(update={"skill_runtime_enabled": True, "skill_catalog_enabled": True})
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    source = Source()
    monkeypatch.setattr("services.skills.runtime_source.ActorSkillSource", lambda *_: source)
    checkpoints, rounds = [], []
    first = actor()

    async def save(point, payload):
        checkpoints.append(copy.deepcopy(payload))
        if point is SafePoint.AFTER_SKILL_ACTIVATION:
            first.push(ConversationCommand("pause", CommandType.PAUSE, "conv-1", "task-1", "turn-1"))
        return {"outcome": "saved"}

    first._replay_checkpoint_callback = save

    async def setup(**kwargs):
        p = prepared()
        replay = kwargs.get("replay_context")
        if replay:
            p.messages = copy.deepcopy(replay["messages"])
        return p

    async def model(p, tools, *args, **kwargs):
        model_round = kwargs["model_round"]
        rounds.append(model_round)
        if model_round == 0:
            return "", "", [call()], set()
        assert {t["function"]["name"] for t in tools} == {"file_search", "activate_skill"}
        assert len([m for m in p.messages if "Turn Skill instructions" in (m.get("content") or "")]) == 1
        if model_round == 1:
            return "", "", [call(id="retry-with-new-provider-id")], set()
        return "done", "", [], set()

    monkeypatch.setattr("services.handlers.chat.execution_engine.prepare_chat_stream", setup)
    monkeypatch.setattr("services.handlers.chat.execution_engine._read_turn", model)
    with pytest.raises(ConversationPauseRequested):
        await execute_chat(handler=handler(), request=_request(), runtime=first)
    assert source.load.await_count == 1
    request = replace(_request(), replay_context=checkpoints[-1])
    result = await execute_chat(handler=handler(), request=request, runtime=actor())
    assert rounds == [0, 1, 2]
    assert source.load.await_count == 2
    assert result.replay_context["skill_runtime"]["active"] == checkpoints[-1]["skill_runtime"]["active"]


async def test_commit_ready_replay_also_revalidates_skill_and_boundary_maps_to_store(monkeypatch):
    from tests.test_chat_generation_executor import _DB, _ReplayCheckpointStore, _claim, _task
    from services.handlers.chat.executor import ChatGenerationExecutor
    from services.skills.runtime import SkillReplayError
    store = _ReplayCheckpointStore()
    store.read_result = {"outcome": "found", "payload": {
        "checkpoint_kind": "commit_ready", "skill_runtime": {"active": [{}]},
        "result_content": [{"type": "text", "text": "done"}], "usage": {}, "credits_cost": 0,
    }}
    validate = AsyncMock(side_effect=SkillReplayError("SKILL_PINNED_REVISION_UNAVAILABLE"))
    monkeypatch.setattr("services.skills.runtime.create_skill_runtime", validate)
    row = {"id": "input-1", "conversation_id": "conv-1", "turn_id": "turn-1", "role": "user",
           "content": [{"type": "text", "text": "hello"}]}
    executor = ChatGenerationExecutor(_DB(row), lambda _: handler(), handler_db_factory=lambda: object(),
                                       replay_checkpoint_store=store)
    with pytest.raises(SkillReplayError):
        await executor.execute(_task(), _claim(), asyncio.Event())
    validate.assert_awaited_once()
    await executor._write_replay_checkpoint(_claim(), SafePoint.AFTER_SKILL_ACTIVATION, {"messages": []})
    assert store.calls[-1]["boundary"].value == "after_skill_activation"


async def test_chat_mixin_propagates_skill_ceiling_to_new_and_cached_executor():
    from services.handlers.chat_tool_mixin import ChatToolMixin

    class Handler(ChatToolMixin):
        db = None
        org_id = "org-1"
        request_ctx = object()

        async def _execute_single_tool(self, tc, executor, *args):
            context = executor.tool_runtime.context(tc["id"])
            observed.append(context.authorized_tool_names)
            return tc, "ok", False, "ok"

    h, observed = Handler(), []
    for ceiling in ({"file_search"}, set(), None):
        await h._execute_tool_calls([call("file_search", "{}")], "task-1", "conv-1", "msg", "user", 1,
                                    authorized_tool_names=ceiling)
    assert observed == [{"file_search"}, set(), None]
