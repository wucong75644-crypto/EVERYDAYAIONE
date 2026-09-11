"""Production scope omission, with real Runtime/search and mock analysis IO."""
import asyncio
import json
import re
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.agent.agent_result import AgentResult
from services.handlers.resource_manifest import ResourceAsset, ResourceManifest
from services.tools.resource_access import ResourceAccessBoundary, ResourceRule
from tests.test_file_target_execution import fixture
from tests.test_tool_production_integration import tc, invoke, loop_for


def empty(e):
    e.resource_manifest = ResourceManifest("task1", "input1", (), "input_message")
    e._handlers["file_analyze"] = AsyncMock(return_value=AgentResult(summary="analysis-ok"))


def grant(*, actions=("list", "read"), paths=(), directories=()):
    return ResourceAccessBoundary((ResourceRule(actions, paths, directories),), "test-approved", True)


def reference(output):
    return re.search(r"fref1_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", str(output))[0]


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("selector", ["resource_ref", "file_id", "path"])
async def test_workspace_search_to_analysis_omitted_scope(fixture, monkeypatch, entry, selector):
    e, _, create = fixture
    empty(e)
    create("downloads/report.csv")
    found = await e.execute("file_search", {"scope": "workspace", "keyword": "report"})
    value = {"resource_ref": reference(found), "file_id": re.search(r"fid_[a-z0-9]{8}", str(found))[0],
             "path": "downloads/report.csv"}[selector]
    await invoke(entry, e, [tc("file_analyze", {selector: value})], monkeypatch)
    e._handlers["file_analyze"].assert_awaited_once()
    assert e._handlers["file_analyze"].call_args.args[0]["scope"] == "workspace"


async def test_real_loop_browses_directories_then_uses_reference(fixture):
    e, _, create = fixture
    empty(e)
    create("downloads/report.csv")
    loop, ctx = loop_for(e)
    turn = 0
    async def model(*_):
        nonlocal turn
        turn += 1
        calls = {1: tc("file_search", {"scope": "workspace"}, "root"),
                 2: tc("file_search", {"path": "downloads/"}, "directory")}
        if turn == 3:
            result = [m["content"] for m in ctx.messages if m["role"] == "tool"][-1]
            calls[3] = tc("file_analyze", {"resource_ref": reference(result)}, "analyze")
        return ({0: calls[turn]} if turn in calls else {}, "done" if turn == 4 else "", 1, 1, 1)
    loop._stream_one_turn = AsyncMock(side_effect=model)
    from services.agent.execution_budget import ExecutionBudget
    result = await loop.run([], e.tool_runtime.advertised(), [], ctx, ExecutionBudget(max_turns=8))
    assert result.text == "done" and result.is_llm_synthesis
    e._handlers["file_analyze"].assert_awaited_once()


async def test_explicit_current_never_inherits_workspace(fixture):
    e, _, create = fixture
    empty(e)
    create("report.csv")
    found = await e.execute("file_search", {"scope": "workspace"})
    result = await e.tool_runtime.execute("file_analyze", {"resource_ref": reference(found), "scope": "current"})
    assert not result.execution.handler_started
    assert "RESOURCE_PATH_NOT_IN_MANIFEST" in str(result.exception)
    assert result.error.retry_context["recovery_action"] == "select_scope"


async def test_old_unqualified_path_keeps_current_without_selection(fixture):
    e, _, create = fixture
    empty(e)
    create("downloads/report.csv")
    result = await e.tool_runtime.execute("file_analyze", {"path": "downloads/report.csv"})
    assert not result.execution.handler_started
    assert "当前任务附件" in str(result.exception)


@pytest.mark.parametrize("recovery", ["cold_reference", "completed_blocks"])
async def test_cold_actor_selection_rebuild_has_no_handler_replay(fixture, recovery):
    e, _, create = fixture
    empty(e)
    create("downloads/report.csv")
    found = await e.execute("file_search", {"scope": "workspace", "keyword": "report"})
    e._tool_runtime = None
    if recovery == "cold_reference":
        args = {"resource_ref": reference(found)}
    else:
        e.tool_runtime.resource_selections.restore(e, [{"type": "tool_step", "tool_name": "file_search",
            "status": "completed", "input": json.dumps({"scope": "workspace"})}])
        args = {"path": "downloads/report.csv"}
    e._handlers["file_search"] = AsyncMock(side_effect=AssertionError("must not replay business search"))
    assert str(await e.execute("file_analyze", args)) == "analysis-ok"


@pytest.mark.parametrize("status", ["running", "error"])
async def test_failed_checkpoint_search_is_not_a_selection(fixture, status):
    e, _, create = fixture
    empty(e)
    create("downloads/report.csv")
    e.tool_runtime.resource_selections.restore(e, [{"type": "tool_step", "tool_name": "file_search",
        "status": status, "input": '{"scope":"workspace"}'}])
    result = await e.tool_runtime.execute("file_analyze", {"path": "downloads/report.csv"})
    assert not result.execution.handler_started


async def test_task_switch_discards_browse_selection(fixture):
    e, _, create = fixture
    empty(e)
    create("report.csv")
    e.task_id = "first"
    await e.execute("file_search", {"scope": "workspace"})
    e.task_id = "second"
    result = await e.tool_runtime.execute("file_analyze", {"path": "report.csv"})
    assert not result.execution.handler_started


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("mode", ["interactive", "scheduled"])
async def test_list_permission_does_not_grant_read_or_hit_cache(fixture, entry, mode, monkeypatch):
    e, _, create = fixture
    empty(e)
    create("report.csv")
    e.execution_mode = mode
    e.task_id = "task1"
    e.allowed_tool_names = {"file_search", "file_analyze"}
    e.tool_policy_snapshot = {"version": 1, "allowed_tools": ["file_search", "file_analyze"]}
    e.resource_access_boundary = grant(actions=("list",), paths=("report.csv",))
    found = await e.execute("file_search", {"scope": "workspace"})
    cache = SimpleNamespace(get=Mock(side_effect=AssertionError("unauthorized cache")), put=Mock())
    lifecycle = SimpleNamespace(replay=AsyncMock(side_effect=AssertionError("unauthorized ledger")), begin=AsyncMock())
    args = {"resource_ref": reference(found), "scope": "workspace"}
    result = await e.tool_runtime.execute("file_analyze", args, cache=cache, lifecycle=lifecycle)
    assert not result.execution.handler_started and result.execution.status == "not_started"
    await invoke(entry, e, [tc("file_analyze", args)], monkeypatch)
    e._handlers["file_analyze"].assert_not_awaited()


async def test_finite_boundary_filters_inventory_before_limits_and_output(fixture):
    e, _, create = fixture
    empty(e)
    for i in range(105):
        create(f"private/a{i:03}.csv")
    create("approved/report.csv")
    e.resource_access_boundary = grant(paths=("approved/report.csv",))
    result = await e.execute("file_search", {"scope": "workspace", "file_pattern": "*.csv"})
    assert "approved/report.csv" in str(result) and "private" not in str(result)
    result = await e.tool_runtime.execute("file_analyze", {"path": "private/a001.csv", "scope": "workspace"})
    assert not result.execution.handler_started


async def test_confirmation_cannot_survive_resource_action_revocation(fixture):
    e, _, create = fixture
    create("report.csv")
    e.resource_access_boundary = grant(actions=("list", "delete"), paths=("report.csv",))
    async def approve(*_):
        e.resource_access_boundary = grant(actions=("list",), paths=("report.csv",))
        return True
    e.tool_confirmer.side_effect = approve
    result = await e.tool_runtime.execute("file_delete", {"files": ["report.csv"]})
    assert not result.execution.handler_started
    e._handlers["file_delete"].assert_not_awaited()


@pytest.mark.parametrize("mode", ["scheduled", "preflight"])
async def test_tool_names_alone_do_not_authorize_scheduled_files(fixture, mode):
    e, _, _ = fixture
    e.task_id = "task1"
    e.execution_mode = mode
    e.allowed_tool_names = {"file_search"}
    e.tool_policy_snapshot = {"version": 1, "allowed_tools": ["file_search"]}
    result = await e.tool_runtime.execute("file_search", {"scope": "workspace"})
    assert not result.execution.handler_started
    assert "RESOURCE_AUTHORIZATION_UNAVAILABLE" in str(result.exception)


async def test_scheduled_manifest_limits_explicit_workspace_and_reference(fixture):
    e, _, create = fixture
    empty(e)
    create("allowed.csv")
    create("other.csv")
    e.execution_mode, e.task_id = "scheduled", "task1"
    e.allowed_tool_names = {"file_search", "file_analyze"}
    e.tool_policy_snapshot = {"version": 1, "allowed_tools": list(e.allowed_tool_names)}
    e.resource_manifest = ResourceManifest("task1", "input1", (
        ResourceAsset("a", "allowed.csv", "allowed.csv", "text/csv", 4, ""),), "approved")
    found = await e.execute("file_search", {"scope": "workspace", "file_pattern": "*.csv"})
    assert "allowed.csv" in str(found) and "other.csv" not in str(found)
    assert str(await e.execute("file_analyze", {"resource_ref": reference(found)})) == "analysis-ok"
    result = await e.tool_runtime.execute("file_analyze", {"path": "other.csv", "scope": "workspace"})
    assert not result.execution.handler_started


async def test_repeated_scope_error_stops_even_when_model_changes_target(fixture):
    e, _, create = fixture
    empty(e)
    create("a.csv")
    create("b.csv")
    await e.tool_runtime.execute("file_analyze", {"path": "a.csv"})
    assert not e.tool_runtime.resource_stop_reason
    await e.tool_runtime.execute("file_analyze", {"path": "b.csv"})
    assert "停止" in e.tool_runtime.resource_stop_reason
    e._handlers["file_analyze"].assert_not_awaited()


async def test_parallel_browse_uses_scoped_set_not_last_completion(fixture):
    e, _, create = fixture
    empty(e)
    create("one/a.csv")
    create("two/b.csv")
    await asyncio.gather(e.execute("file_search", {"scope": "workspace", "path": "one/"}),
                         e.execute("file_search", {"scope": "workspace", "path": "two/"}))
    await e.execute("file_analyze", {"path": "one/a.csv"})
    await e.execute("file_analyze", {"path": "two/b.csv"})
    assert e._handlers["file_analyze"].await_count == 2


@pytest.mark.parametrize("resume,blocked", [(False, False), (True, False), (False, True)])
async def test_chat_actor_real_engine_uses_scope_across_model_rounds(fixture, monkeypatch, resume, blocked):
    from services.handlers.chat.execution_engine import _run_loop, ChatExecutionRequest
    from services.handlers.chat.stream_session import StreamTotals
    from services.agent.execution_budget import ExecutionBudget
    from tests.test_tool_production_integration import actor_harness, InvocationStore
    e, _, create = fixture
    empty(e)
    e.conversation_id, e.task_id = "c1", "task1"
    create("downloads/report.csv")
    h = actor_harness(InvocationStore())
    h._resource_manifest = e.resource_manifest
    if blocked:
        e.resource_access_boundary = ResourceAccessBoundary()
        h._resource_access_boundary = e.resource_access_boundary
    h._tool_executor = e
    h._tool_executor_scope = ("task1", "c1", "u1", "o1")
    prepared = SimpleNamespace(
        execution_context=e.tool_runtime.context(), budget=ExecutionBudget(max_turns=8),
        core_tools=e.tool_runtime.advertised(), messages=[],
        tool_context=SimpleNamespace(discovered_tools=set(), build_context_prompt=lambda: "",
                                     update_from_batch=lambda *_: None),
        permission=SimpleNamespace(mode=SimpleNamespace(value="auto"), need_exit_attachment=False,
                                   get_reminder=lambda _: ""),
    )
    turns = 0
    async def model(*_, **__):
        nonlocal turns
        turns += 1
        if turns == 1:
            call = tc("file_search", {"scope": "workspace"}, "root")
        elif turns == 2:
            if resume:
                # Engine's existing completed blocks are the only continuity
                # input; discard the in-process Runtime and its selection set.
                e._tool_runtime = None
            call = tc("file_search", {"path": "downloads/"}, "directory")
        elif turns == 3:
            found = [m["content"] for m in prepared.messages if m["role"] == "tool"][-1]
            call = tc("file_analyze", {"resource_ref": reference(found)}, "analysis")
        else:
            return "完成", "", [], set()
        return "", "", [call], set()
    monkeypatch.setattr("services.handlers.chat.execution_engine._read_turn", model)
    monkeypatch.setattr("services.handlers.chat.execution_engine.compact_tool_context", AsyncMock())
    sink = SimpleNamespace(on_tool_calls=AsyncMock(), on_tool_result=AsyncMock(), on_block=AsyncMock(),
                           on_block_update=AsyncMock(), on_text=AsyncMock())
    h._execution_sink = sink
    blocks = []
    await _run_loop(handler=h, request=ChatExecutionRequest(content=[], user_id="u1", conversation_id="c1",
        task_id="task1", message_id="m1", model_id="mock", context_anchor=None), prepared=prepared,
        cancellation_event=asyncio.Event(), sink=sink, totals=StreamTotals(), blocks=blocks, runtime=None)
    if blocked:
        assert turns == 1 and "授权" in blocks[-1]["text"]
        e._handlers["file_analyze"].assert_not_awaited()
        return
    assert turns == 4
    assert all(b["status"] == "completed" for b in blocks if b["type"] == "tool_step")
    e._handlers["file_analyze"].assert_awaited_once()
    assert not h._actor_invocation_store.completed  # safe reads never create side-effect ledger rows


async def test_tool_loop_stops_without_more_tool_requests_on_missing_authority(fixture, monkeypatch):
    e, _, _ = fixture
    empty(e)
    e.resource_access_boundary = ResourceAccessBoundary()
    loop, ctx = loop_for(e)
    loop._stream_one_turn = AsyncMock(return_value=({0: tc("file_search", {"scope": "workspace"})}, "", 1, 1, 1))
    monkeypatch.setattr("services.agent.stop_policy.synthesize_wrap_up", AsyncMock(return_value="当前文件授权不可验证，未执行。"))
    from services.agent.execution_budget import ExecutionBudget
    result = await loop.run([], e.tool_runtime.advertised(), [], ctx, ExecutionBudget(max_turns=8))
    assert result.stop_reason == "resource_access_blocked"
    assert result.tool_outcomes == [{"tool_name": "file_search", "status": "error"}]
    loop._stream_one_turn.assert_awaited_once()


@pytest.mark.parametrize("args", [{"path": "image.png", "scope": "workspace"},
                                  {"path": "image", "scope": "workspace"},
                                  {"keyword": "secret", "search_content": True, "scope": "workspace"}])
async def test_listing_cannot_read_images_or_search_content(fixture, args):
    e, _, create = fixture
    create("image.png")
    e.resource_access_boundary = grant(actions=("list",), directories=(".",))
    e._handlers["file_search"] = AsyncMock(side_effect=AssertionError("content read must be rejected before Handler"))
    result = await e.tool_runtime.execute("file_search", args)
    assert not result.execution.handler_started
    assert "RESOURCE_ACTION_DENIED" in str(result.exception)


async def test_recovered_reference_rechecks_revoked_access(fixture):
    e, _, create = fixture
    empty(e)
    create("report.csv")
    found = await e.execute("file_search", {"scope": "workspace"})
    e._tool_runtime = None
    e.resource_access_boundary = grant(actions=("list",), paths=("report.csv",))
    result = await e.tool_runtime.execute("file_analyze", {"resource_ref": reference(found)})
    assert not result.execution.handler_started


async def test_cancelled_selection_never_reaches_handler(fixture):
    e, _, create = fixture
    empty(e)
    create("report.csv")
    found = await e.execute("file_search", {"scope": "workspace"})
    e.cancellation_event = asyncio.Event()
    e.cancellation_event.set()
    with pytest.raises(asyncio.CancelledError):
        await e.execute("file_analyze", {"resource_ref": reference(found)})
    e._handlers["file_analyze"].assert_not_awaited()


async def test_actor_checkpoint_from_another_task_cannot_seed_scope(fixture, monkeypatch):
    from tests.test_tool_production_integration import ChatHarness
    e, _, create = fixture
    empty(e)
    create("report.csv")
    h = ChatHarness(e.db)
    h._tool_selection_history_task_id = "another-task"
    h._tool_selection_history = ({"type": "tool_step", "tool_name": "file_search",
        "status": "completed", "input": '{"scope":"workspace"}'},)
    await invoke("chat", e, [tc("file_analyze", {"path": "report.csv"})], monkeypatch, h)
    e._handlers["file_analyze"].assert_not_awaited()


def test_scheduled_template_manifest_uses_only_approved_template_field():
    from services.agent.scheduled_task_agent import ScheduledTaskAgent
    owner = SimpleNamespace(task_id="t", task={"template_file": {"name": "report.csv", "path": "approved/report.csv"},
                                             "prompt": "read every other workspace file"})
    manifest = ScheduledTaskAgent._template_manifest(owner)
    assert manifest.allowed_paths == {"approved/report.csv"}
    owner.task["template_file"]["path"] = "../other/report.csv"
    with pytest.raises(ValueError):
        ScheduledTaskAgent._template_manifest(owner)


async def test_real_search_reference_to_real_csv_conversion(fixture):
    import pandas as pd
    from services.agent.file_path_cache import get_file_cache
    e, _, create = fixture
    e.resource_manifest = ResourceManifest("t", "m", (), "input_message")
    path = create("approved/report.csv", "value\n17\n")
    found = await e.execute("file_search", {"scope": "workspace", "keyword": "report"})
    result = await e.execute("file_analyze", {"resource_ref": reference(found)})
    assert result.status == "success"
    assert pd.read_parquet(get_file_cache(e.conversation_id).resolve(str(path))).iloc[0, 0] == 17


@pytest.mark.parametrize("at_confirmation", [False, True])
async def test_expired_resource_authority_never_executes(fixture, at_confirmation):
    import time
    e, _, create = fixture
    create("report.csv")
    e.resource_access_boundary = replace(grant(actions=("delete",), paths=("report.csv",)),
                                        expires_at=time.time() + (60 if at_confirmation else -1))
    if at_confirmation:
        async def expire(*_):
            e.resource_access_boundary = replace(e.resource_access_boundary, expires_at=time.time() - 1)
            return True
        e.tool_confirmer.side_effect = expire
    lifecycle = SimpleNamespace(replay=AsyncMock(return_value=None), begin=AsyncMock(), complete=AsyncMock())
    result = await e.tool_runtime.execute("file_delete", {"files": ["report.csv"]}, lifecycle=lifecycle)
    assert not result.execution.handler_started
    lifecycle.begin.assert_not_awaited()
    e._handlers["file_delete"].assert_not_awaited()
    assert e.tool_confirmer.await_count == int(at_confirmation)


async def test_sibling_parallel_call_cannot_inherit_a_later_completed_search(fixture, monkeypatch):
    from services.tools import runtime as module
    e, _, create = fixture
    empty(e)
    create("downloads/report.csv")
    original = module.refresh_context
    child_started, root_done = asyncio.Event(), asyncio.Event()
    async def refresh(owner, context, registry):
        if context.call_id == "child":
            child_started.set()
            await root_done.wait()
        return await original(owner, context, registry)
    monkeypatch.setattr(module, "refresh_context", refresh)
    child = asyncio.create_task(e.execute("file_search", {"path": "downloads/"}, call_id="child"))
    await asyncio.wait_for(child_started.wait(), 2)
    await e.execute("file_search", {"scope": "workspace"}, call_id="root")
    root_done.set()
    result = await asyncio.wait_for(child, 2)
    assert result.status == "empty" and "当前任务资源" in result.summary
    assert "report.csv" in str(await e.execute("file_search", {"path": "downloads/"}, call_id="next-round"))


async def test_finite_listing_does_not_fail_on_hidden_staging(fixture):
    e, _, create = fixture
    empty(e)
    create("report.csv")
    create("staging/internal.csv")
    create(".hidden")
    e.resource_access_boundary = grant(paths=("report.csv",))
    result = await e.execute("file_search", {"scope": "workspace"})
    assert result.status == "success" and "report.csv" in str(result)
    assert "staging" not in str(result) and ".hidden" not in str(result)


async def test_manifest_fid_is_intersected_with_action_grants(fixture):
    from services.agent.file_id import compute_fid
    e, _, create = fixture
    empty(e)
    create("allowed.csv")
    create("other.csv")
    e.resource_manifest = ResourceManifest("t", "m", tuple(
        ResourceAsset(name, name, name, "text/csv", 4, "") for name in ("allowed.csv", "other.csv")
    ), "input_message")
    e.resource_access_boundary = grant(actions=("read",), paths=("allowed.csv",))
    assert str(await e.execute("file_analyze", {"file_id": compute_fid("o1", "allowed.csv")})) == "analysis-ok"


@pytest.mark.parametrize("path", ["image.png", "image", "png"])
async def test_manifest_image_selection_checks_read_before_handler(fixture, path):
    e, _, create = fixture
    create("uploads/image.png")
    e.resource_manifest = ResourceManifest("t", "m", (
        ResourceAsset("a", "image.png", "uploads/image.png", "image/png", 4, ""),), "input_message")
    e.resource_access_boundary = grant(actions=("list",), paths=("uploads/image.png",))
    e._handlers["file_search"] = AsyncMock(side_effect=AssertionError("image must not be injected"))
    result = await e.tool_runtime.execute("file_search", {"path": path, "scope": "current"})
    assert not result.execution.handler_started
    assert "RESOURCE_ACTION_DENIED" in str(result.exception)


@pytest.mark.parametrize("path", ["uploads/", "."])
async def test_manifest_directory_listing_does_not_auto_read_its_only_image(fixture, path):
    e, _, create = fixture
    create("uploads/image.png")
    e.resource_manifest = ResourceManifest("t", "m", (
        ResourceAsset("a", "image.png", "uploads/image.png", "image/png", 4, ""),), "input_message")
    e.resource_access_boundary = grant(actions=("list",), paths=("uploads/image.png",))
    result = await e.execute("file_search", {"path": path, "scope": "current"})
    assert isinstance(result, AgentResult) and "image.png" in result.summary
    assert not result.emit_payloads
