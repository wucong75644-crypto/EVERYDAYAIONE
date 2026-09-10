"""Real file handlers through production entrances, with only external IO mocked."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.agent.tool_executor import ToolExecutor
from services.file_executor import FileExecutor
from services.file_resources import FileTargetResolver, FileTargetError
from services.workspace_coordination import workspace_lock
from tests.tool_runtime_support import IdentityDB
from tests.test_tool_production_integration import invoke, tc


async def test_complete_model_loop_carries_discovered_reference(fixture):
    from tests.test_tool_production_integration import loop_for
    from services.agent.execution_budget import ExecutionBudget
    e, _, create = fixture
    target = create("uploads/report | final.csv")
    loop, ctx = loop_for(e)
    turns = 0
    async def model_turn(*_):
        nonlocal turns
        turns += 1
        if turns == 1:
            return ({0: tc("file_search", {"keyword": "report |", "scope": "workspace"}, "search")}, "", 1, 1, 1)
        if turns == 2:
            output = next(m["content"] for m in ctx.messages if m["role"] == "tool")
            reference = re.search(r"resource_ref: (fref1_[^\s]+)", output)[1]
            return ({0: tc("file_delete", {"resource_refs": [reference]}, "delete")}, "", 1, 1, 1)
        return ({}, "完成", 1, 1, 1)
    loop._stream_one_turn = AsyncMock(side_effect=model_turn)
    result = await loop.run([], e.tool_runtime.advertised(), [], ctx, ExecutionBudget(max_turns=5))
    assert result.is_llm_synthesis and result.text == "完成"
    assert not target.exists() and e._handlers["file_delete"].await_count == 1
    assert e.tool_confirmer.await_count == 1 and e._record_deleted_files.call_count == 1


async def test_budget_expires_during_confirmation_before_invocation(fixture):
    from services.agent.execution_budget import ExecutionBudget
    e, _, create = fixture
    target = create("report.csv")
    e.execution_budget = ExecutionBudget()
    async def approve(*_):
        e.execution_budget._start -= 601
        return True
    e.tool_confirmer.side_effect = approve
    lifecycle = SimpleNamespace(replay=AsyncMock(return_value=None), begin=AsyncMock(), complete=AsyncMock())
    result = await e.tool_runtime.execute("file_delete", {"files": ["report.csv"]}, lifecycle=lifecycle)
    assert not result.execution.handler_started and target.exists()
    lifecycle.begin.assert_not_awaited()
    e._handlers["file_delete"].assert_not_awaited()


async def test_deleted_record_ambiguity_uses_real_lookup(fixture, monkeypatch):
    from unittest.mock import MagicMock
    e, _, _ = fixture
    cur = AsyncMock()
    cur.fetchall.return_value = [(1, "org/o1/u1/a.csv", "backup1"), (2, "org/o1/u1/a.csv", "backup2")]
    conn = MagicMock()
    conn.cursor.return_value.__aenter__.return_value = cur
    manager = MagicMock()
    manager.__aenter__.return_value = conn
    monkeypatch.setattr("services.knowledge_config.is_kb_available", lambda: True)
    monkeypatch.setattr("services.knowledge_config.get_pg_connection", AsyncMock(return_value=manager))
    result = await e.tool_runtime.execute("restore_file", {"filename": "a.csv"})
    assert not result.execution.handler_started and "RESOURCE_AMBIGUOUS" in str(result.exception)
    assert "record_id=1" in str(result.exception) and "record_id=2" in str(result.exception)
    cur.fetchall.return_value = [cur.fetchall.return_value[1]]
    assert (await e._find_deleted_record("", record_id=2))["id"] == 2
    assert cur.execute.call_args.args[1]["record_id"] == 2


async def test_cache_file_lock_wait_is_cancellable(tmp_path):
    from services.agent.data_query_cache import _FileLock
    path = str(tmp_path / "cache.lock")
    entered = asyncio.Event()
    async def waiter():
        entered.set()
        async with _FileLock(path):
            pytest.fail("lock entered before owner released")
    async with _FileLock(path):
        task = asyncio.create_task(waiter())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with _FileLock(path):
        pass  # cancelled waiter did not retain ownership


async def test_cold_actor_resumes_signed_target_without_second_dialog(fixture, monkeypatch):
    from dataclasses import asdict, replace
    from services.tools.file_calls import resolve_file_call
    from services.tools.policy import _digest
    from services.tools.runtime_context import refresh_context
    from services.conversation_commands import CommandType, ConversationCommand
    from services.websocket_manager import WebSocketManager
    from tests.test_tool_production_integration import InvocationStore, actor_harness
    e, _, create = fixture
    target = create("uploads/report.csv")
    e.task_id, e.conversation_id = "task1", "c1"
    args = {"resource_refs": [FileTargetResolver(e).reference(target)]}
    runtime = e.tool_runtime
    context = await refresh_context(e, runtime.context("call"), runtime.registry)
    prepared = resolve_file_call(e, "file_delete", args)
    await prepared.prepare()
    decision = runtime.policy.decide("file_delete", replace(context, resource_versions=prepared.binding), prepared.arguments)
    identifier = "tool-approval:" + _digest(asdict(decision.confirmation_binding))
    # A fresh runtime has no pending task or receipt from the first process.
    e._tool_runtime = None
    store = InvocationStore()
    harness = actor_harness(store)
    harness._actor_command_store = SimpleNamespace(load_pending=AsyncMock(return_value=[ConversationCommand(
        command_id="approved", command_type=CommandType.APPROVAL_RESULT, conversation_id="c1",
        task_id="task1", turn_id="turn", payload={"tool_call_id": identifier, "user_id": "u1", "approved": True},
    )]))
    manager = WebSocketManager()
    manager.send_to_task_or_user = AsyncMock()
    monkeypatch.setattr("services.handlers.chat_tool_mixin.ws_manager", manager)
    e.tool_confirmer = lambda call, ctx, decision: harness._confirm_tool_call(call, ctx, decision, "m1")
    await invoke("chat", e, [tc("file_delete", args)], monkeypatch, harness)
    assert not target.exists() and e._handlers["file_delete"].await_count == 1
    assert store.completed[0]["status"] == "succeeded"
    assert not [c for c in manager.send_to_task_or_user.call_args_list if c.args[-1]["type"] == "tool_confirm_request"]


async def test_source_snapshot_rejects_change_and_cleans_temporary_files(tmp_path):
    from services.file_resources import source_snapshot
    source = tmp_path / "report.csv"
    source.write_text("old")
    staging = tmp_path / "staging"
    with pytest.raises(FileTargetError, match="RESOURCE_CHANGED"):
        async with source_snapshot(str(source), str(staging)) as snapshot:
            source.write_text("new")
            assert Path(snapshot).read_text() == "old"
    assert not list(staging.iterdir())


async def test_cancelled_oss_sync_drains_source_read_before_unlock(fixture):
    import threading
    import fcntl
    import os
    from services.oss_service import OSSService
    from services.workspace_coordination import _open
    _, files, create = fixture
    target = create("report.csv")
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    def put(*_, **__):
        loop.call_soon_threadsafe(started.set)
        release.wait(5)
        assert target.read_text() == "x\n1\n"
    oss = object.__new__(OSSService)
    oss.bucket = SimpleNamespace(put_object_from_file=put)
    oss.get_url = Mock(return_value="https://test.invalid/resource")
    async def upload():
        async with workspace_lock(files.workspace_root, write=True):
            await oss.sync_workspace_file(target, "report.csv")
    task = asyncio.create_task(upload())
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        fd = _open(files.workspace_root)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("failure", ["unreadable", "limit"])
async def test_incomplete_candidate_inventory_never_selects_first(fixture, monkeypatch, failure):
    e, files, create = fixture
    target = create("report.csv")
    def walk(root, *, onerror, **_):
        if failure == "limit":
            yield str(root), [], ["report.csv"] * 20001
        else:
            yield str(root), [], ["report.csv"]
            onerror(PermissionError("unreadable subdirectory"))
    monkeypatch.setattr("services.file_resources.os.walk", walk)
    result = await e.tool_runtime.execute("file_delete", {"files": ["report"]})
    assert not result.execution.handler_started and "RESOURCE_SEARCH_INCOMPLETE" in str(result.exception)
    assert target.exists()
    e.tool_confirmer.assert_not_awaited()
    e._handlers["file_delete"].assert_not_awaited()


async def test_colliding_legacy_fid_and_tampered_reference_are_not_authority(fixture, monkeypatch):
    e, _, create = fixture
    first = create("a.csv")
    second = create("b.csv")
    monkeypatch.setattr("services.agent.file_id.compute_fid", lambda *_: "fid_abcd1234")
    collision = await e.tool_runtime.execute("file_delete", {"file_ids": ["fid_abcd1234"]})
    reference = FileTargetResolver(e).reference(first).replace("fref1_", "fref1_a", 1)
    forged = await e.tool_runtime.execute("file_delete", {"resource_refs": [reference]})
    assert not collision.execution.handler_started and "RESOURCE_AMBIGUOUS" in str(collision.exception)
    assert not forged.execution.handler_started and "RESOURCE_REFERENCE_INVALID" in str(forged.exception)
    assert first.exists() and second.exists()
    e.tool_confirmer.assert_not_awaited()
    e._handlers["file_delete"].assert_not_awaited()


async def test_cancel_between_batch_deletes_stops_remaining_and_records_completed(fixture, monkeypatch):
    import os
    e, _, create = fixture
    first, second = create("a.csv"), create("b.csv")
    event = asyncio.Event()
    e.cancellation_event = event
    remove = os.remove
    def delete_then_cancel(path):
        remove(path)
        event.set()
    monkeypatch.setattr("services.agent.file_delete_mixin.os.remove", delete_then_cancel)
    with pytest.raises(asyncio.CancelledError):
        await e.execute("file_delete", {"files": ["a.csv", "b.csv"]})
    assert not first.exists() and second.exists()
    assert e._handlers["file_delete"].await_count == 1
    e._record_deleted_files.assert_called_once_with([{"raw": str(first), "resolved": str(first)}])


async def test_csv_and_tsv_same_bytes_have_distinct_conversion_identity(tmp_path):
    import pandas as pd
    from services.agent.data_query_cache import ensure_parquet_cache_csv
    csv, tsv = tmp_path / "table.csv", tmp_path / "table.tsv"
    csv.write_text("a\tb\n1\t2\n")
    tsv.write_bytes(csv.read_bytes())
    staging = tmp_path / "staging"
    staging.mkdir()
    comma, _ = await ensure_parquet_cache_csv(str(csv), str(staging))
    tab, _ = await ensure_parquet_cache_csv(str(tsv), str(staging))
    assert comma != tab
    assert len(pd.read_parquet(comma).columns) == 1
    assert len(pd.read_parquet(tab).columns) == 2


async def test_shared_content_cache_does_not_rebind_its_metadata_to_each_caller(fixture):
    from services.agent.file_path_cache import get_file_cache
    e, _, create = fixture
    first, second = create("a/report.csv"), create("b/another.csv")
    result_a = await e.execute("file_analyze", {"path": str(first), "scope": "workspace"})
    path = get_file_cache(e.conversation_id).resolve(str(first))
    metadata = Path(path.replace(".parquet", ".meta.json"))
    before = metadata.read_bytes()
    result_b = await e.execute("file_analyze", {"path": str(second), "scope": "workspace"})
    assert get_file_cache(e.conversation_id).resolve(str(second)) == path
    assert metadata.read_bytes() == before
    assert "a/report.csv" in result_a.summary and "b/another.csv" in result_b.summary


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    from core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "file_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "file_workspace_enabled", True)
    files = FileExecutor(str(tmp_path), "u1", "o1")
    root = Path(files.workspace_root)
    e = ToolExecutor(IdentityDB(), "u1", str(uuid4()), "o1", tool_confirmer=AsyncMock(return_value=True))
    e._record_deleted_files = Mock()
    e._handlers["file_delete"] = AsyncMock(wraps=e._handlers["file_delete"])
    def create(path, content="x\n1\n"):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target
    return e, files, create


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("selector", ["name", "partial", "path", "fid", "reference"])
async def test_search_then_delete_real_handlers(fixture, monkeypatch, entry, selector):
    e, files, create = fixture
    target = create("uploads/report | final.csv")
    result = await e.execute("file_search", {"keyword": "report |", "scope": "workspace"})
    assert "uploads/report | final.csv" in result.summary
    ref = re.search(r"resource_ref: (\S+)", result.summary)[1]
    fid = re.search(r"\[(fid_\w+)\]", result.summary)[1]
    args = {"name": {"files": [target.name]}, "partial": {"files": ["report | final"]},
            "path": {"files": ["uploads/report | final.csv"]}, "fid": {"file_ids": [fid]},
            "reference": {"resource_refs": [ref]}}[selector]
    await invoke(entry, e, [tc("file_delete", args)], monkeypatch)
    assert not target.exists()
    assert e._handlers["file_delete"].await_count == 1
    assert e.tool_confirmer.await_count == 1
    assert e._record_deleted_files.call_count == 1


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("case", ["missing", "ambiguous", "plan", "denied", "changed", "foreign"])
async def test_bad_target_never_reaches_handler(fixture, monkeypatch, entry, case):
    e, files, create = fixture
    target = create("uploads/report.csv")
    args = {"files": ["report.csv"]}
    if case == "missing": args = {"files": ["missing.csv"]}
    if case == "ambiguous": create("other/report.csv")
    if case == "plan": e.permission_mode = "plan"
    if case == "denied": e.tool_confirmer.return_value = False
    if case == "foreign": args = {"files": [str(Path(files.workspace_root).parent / "other/private.csv")]}
    if case == "changed":
        async def approve(*_):
            target.write_text("replacement")
            return True
        e.tool_confirmer.side_effect = approve
    await invoke(entry, e, [tc("file_delete", args)], monkeypatch)
    assert target.exists()
    assert e._handlers["file_delete"].await_count == 0
    assert e._record_deleted_files.call_count == 0
    if case not in {"denied", "changed"}:
        assert e.tool_confirmer.await_count == 0


async def test_cold_reference_and_fid_are_scoped_and_resolvable(fixture):
    from services.agent.file_id import compute_fid
    e, files, create = fixture
    target = create("uploads/report.csv")
    reference = FileTargetResolver(e).reference(target)
    e.conversation_id = str(uuid4())
    resolver = FileTargetResolver(e)
    assert resolver.resolve(reference).path == target
    assert resolver.resolve(compute_fid("o1", "uploads/report.csv")).path == target
    e.workspace_user_id = "other"
    with pytest.raises(PermissionError):
        FileTargetResolver(e).resolve(reference)


async def test_explicit_path_does_not_use_same_named_cache(fixture):
    from services.agent.file_path_cache import get_file_cache
    e, files, create = fixture
    target = create("uploads/report.csv")
    get_file_cache(e.conversation_id).register("uploads/report.csv", workspace=str(target))
    result = await e.tool_runtime.execute("file_analyze", {"path": "other/report.csv", "scope": "workspace"})
    assert not result.execution.handler_started
    assert target.exists()


async def test_missing_manifest_item_has_no_actionable_reference(fixture):
    from services.handlers.resource_manifest import ResourceAsset, ResourceManifest
    e, _, _ = fixture
    e.resource_manifest = ResourceManifest("t", "m", (
        ResourceAsset("a", "ghost.csv", "ghost.csv", "text/csv", 3, ""),), "test")
    result = await e.execute("file_search", {})
    assert "不可用" in result.summary
    assert "fref1_" not in result.summary and "fid_" not in result.summary


async def test_restore_no_clobber_and_pinned_record(fixture, monkeypatch):
    e, files, create = fixture
    target = create("uploads/restored.csv", "current")
    record = {"id": 123, "relative_path": "org/o1/u1/uploads/restored.csv", "oss_object_key": "mock"}
    e._find_deleted_record = AsyncMock(return_value=record)
    e._mark_restored = AsyncMock()
    download = Mock(side_effect=lambda key, path, **kwargs: Path(path).write_text("backup"))
    monkeypatch.setattr("services.oss_service.get_oss_service", lambda: SimpleNamespace(bucket=SimpleNamespace(
        get_object_to_file=download, get_object_meta=Mock(return_value=SimpleNamespace(etag="backup-version")))))
    result = await e.tool_runtime.execute("restore_file", {"filename": "restored.csv"})
    assert not result.execution.handler_started and target.read_text() == "current"
    download.assert_not_called()
    target.unlink()
    result = await e.tool_runtime.execute("restore_file", {"filename": "restored.csv"})
    assert result.execution.handler_started and target.read_text() == "backup"
    download.assert_called_once()
    assert download.call_args.kwargs == {"headers": {"If-Match": '"backup-version"'}}
    e._mark_restored.assert_awaited_once_with(123)
    assert e._find_deleted_record.call_args.kwargs == {"record_id": 123}


async def test_csv_tail_change_rebuilds_real_conversion(fixture, tmp_path):
    import pandas as pd
    from services.agent.data_query_cache import ensure_parquet_cache_csv
    e, _, create = fixture
    prefix = "value\n" + "1\n" * 524288
    source = create("uploads/large.csv", prefix + "2\n")
    staging = tmp_path / "staging"
    staging.mkdir()
    first, _ = await ensure_parquet_cache_csv(str(source), str(staging))
    source.write_text(prefix + "9\n")
    second, _ = await ensure_parquet_cache_csv(str(source), str(staging))
    assert first != second
    assert pd.read_parquet(second).iloc[-1, 0] == 9


async def test_cross_conversation_writer_waits_for_readers(fixture):
    _, files, _ = fixture
    root = files.workspace_root
    a, b, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    trace = []
    async def reader(name, event):
        async with workspace_lock(root):
            trace.append(name + "+")
            event.set()
            await release.wait()
            trace.append(name + "-")
    async def writer():
        async with workspace_lock(root, write=True):
            trace.append("C+")
            assert "A-" in trace and "B-" in trace
            trace.append("C-")
    readers = [asyncio.create_task(reader("A", a)), asyncio.create_task(reader("B", b))]
    await asyncio.wait_for(asyncio.gather(a.wait(), b.wait()), 2)
    c = asyncio.create_task(writer())
    release.set()
    await asyncio.wait_for(asyncio.gather(*readers, c), 2)
    assert trace.index("A+") < trace.index("B-") and trace.index("B+") < trace.index("A-")


async def test_cancellation_waiting_for_workspace_never_registers_invocation(fixture):
    e, files, create = fixture
    create("uploads/report.csv")
    started, release = asyncio.Event(), asyncio.Event()
    checked = asyncio.Event()
    async def replay(*_):
        checked.set()
        return None
    lifecycle = SimpleNamespace(replay=replay, begin=AsyncMock(), complete=AsyncMock())
    async def blocker():
        async with workspace_lock(files.workspace_root, write=True):
            started.set()
            await release.wait()
    holding = asyncio.create_task(blocker())
    await started.wait()
    task = asyncio.create_task(e.tool_runtime.execute("file_delete", {"files": ["uploads/report.csv"]}, lifecycle=lifecycle))
    await asyncio.wait_for(checked.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await holding
    lifecycle.begin.assert_not_called()
    e._handlers["file_delete"].assert_not_called()


async def test_complete_filename_does_not_select_another_extension(fixture):
    from services.agent.file_path_cache import get_file_cache
    e, _, create = fixture
    target = create("uploads/report.xlsx")
    get_file_cache(e.conversation_id).register("uploads/report.xlsx", workspace=str(target))
    result = await e.tool_runtime.execute("file_delete", {"files": ["report.csv"]})
    assert not result.execution.handler_started and target.exists()
    e.tool_confirmer.assert_not_called()


async def test_analysis_uses_real_conversion_and_invalidates_source_cache(fixture):
    import pandas as pd
    from services.agent.file_path_cache import get_file_cache
    e, _, create = fixture
    source = create("uploads/report.csv", "value\n2\n")
    result = await e.execute("file_analyze", {"path": "uploads/report.csv", "scope": "workspace"})
    assert result.status == "success"
    cache = get_file_cache(e.conversation_id)
    first = cache.resolve(str(source))
    assert pd.read_parquet(first).iloc[0, 0] == 2
    source.write_text("value\n9\n")
    assert cache.resolve(str(source)) is None
    result = await e.execute("file_analyze", {"path": "uploads/report.csv", "scope": "workspace"})
    assert result.status == "success"
    second = cache.resolve(str(source))
    assert first != second and pd.read_parquet(second).iloc[0, 0] == 9


async def test_changed_backup_and_denied_invocation_never_enter_handler(fixture, monkeypatch):
    e, _, _ = fixture
    record = {"id": 1, "relative_path": "org/o1/u1/backup.csv", "oss_object_key": "mock"}
    e._find_deleted_record = AsyncMock(return_value=record)
    e._handlers["restore_file"] = AsyncMock(wraps=e._handlers["restore_file"])
    head = Mock(side_effect=[SimpleNamespace(etag="first"), SimpleNamespace(etag="second")])
    download = Mock()
    monkeypatch.setattr("services.oss_service.get_oss_service", lambda: SimpleNamespace(bucket=SimpleNamespace(
        get_object_meta=head, get_object_to_file=download)))
    lifecycle = SimpleNamespace(replay=AsyncMock(return_value=None), begin=AsyncMock(), complete=AsyncMock())
    result = await e.tool_runtime.execute("restore_file", {"filename": "backup.csv"}, lifecycle=lifecycle)
    assert not result.execution.handler_started and "RESOURCE_CHANGED" in str(result.exception)
    lifecycle.begin.assert_not_called()
    download.assert_not_called()
    e._handlers["restore_file"].assert_not_called()


async def test_invocation_refusal_is_before_real_delete(fixture):
    e, _, create = fixture
    target = create("uploads/report.csv")
    lifecycle = SimpleNamespace(replay=AsyncMock(return_value=None),
        begin=AsyncMock(side_effect=PermissionError("invocation refused")), complete=AsyncMock())
    result = await e.tool_runtime.execute("file_delete", {"files": ["uploads/report.csv"]}, lifecycle=lifecycle)
    assert not result.execution.handler_started and target.exists()
    lifecycle.begin.assert_awaited_once()
    lifecycle.complete.assert_not_called()
    e._handlers["file_delete"].assert_not_called()


async def test_group_actor_deletes_owner_resource_only(fixture, monkeypatch):
    e, files, create = fixture
    from core.config import get_settings
    personal = create("report.csv")
    group_files = FileExecutor(get_settings().file_workspace_root, "channel-owner", "o1")
    group = Path(group_files.workspace_root) / "report.csv"
    group.write_text("group")
    db = IdentityDB(scope_type="channel", source="wecom", scope_id="channel1")
    db.conversation["user_id"] = None
    scope = SimpleNamespace(actor_user_id="u1", workspace_owner_id="channel-owner")
    owner = ToolExecutor(db, "u1", str(uuid4()), "o1", workspace_user_id="channel-owner",
        context_scope="channel", personal_context_allowed=False, execution_scope=scope,
        channel_scope_id="channel1", tool_confirmer=AsyncMock(return_value=True))
    owner._record_deleted_files = Mock()
    ref = FileTargetResolver(owner).reference(group)
    result = await owner.execute("file_delete", {"resource_refs": [ref]})
    assert result.status == "success" and not group.exists() and personal.exists()
    with pytest.raises(PermissionError):
        FileTargetResolver(e).resolve(ref)


@pytest.mark.parametrize("operation", ["pause", "resume", "delete", "update"])
async def test_ambiguous_scheduled_id_does_not_submit_proposal(operation):
    from services.scheduler.chat_task_manager import ChatTaskManager
    rows = [{"id": "12345678-aaaa", "name": "A"}, {"id": "12345678-bbbb", "name": "B"}]
    db = Mock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = rows
    manager = ChatTaskManager(db, "u1", "o1")
    manager._propose_chat_change = AsyncMock()
    result = await getattr(manager, "_handle_" + operation)({"task_id": "12345678"})
    assert all(row["id"] in result["text"] for row in rows)
    manager._propose_chat_change.assert_not_called()


async def test_cross_process_shared_and_exclusive_lock_protocol(fixture):
    import sys
    _, files, _ = fixture
    code = '''
import fcntl, sys
from services.workspace_coordination import _open
fd = _open(sys.argv[1])
fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
print('shared', flush=True)
fcntl.flock(fd, fcntl.LOCK_UN)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    raise AssertionError('writer entered while reader held lock')
except BlockingIOError:
    print('blocked', flush=True)
sys.stdin.readline()
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print('exclusive', flush=True)
'''
    async with workspace_lock(files.workspace_root):
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", code, files.workspace_root,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        assert await asyncio.wait_for(child.stdout.readline(), 3) == b"shared\n"
        assert await asyncio.wait_for(child.stdout.readline(), 3) == b"blocked\n"
    child.stdin.write(b"continue\n")
    await child.stdin.drain()
    stdout, stderr = await asyncio.wait_for(child.communicate(), 3)
    assert child.returncode == 0, stderr.decode()
    assert stdout == b"exclusive\n"


async def test_repeated_cancellation_drains_io_before_unlock(fixture):
    import fcntl
    import os
    import threading
    from services.workspace_coordination import finish_file_io, _open
    _, files, _ = fixture
    running, release = threading.Event(), threading.Event()
    trace = []
    def write():
        running.set()
        release.wait(3)
        trace.append("io_finished")
    async def operation():
        async with workspace_lock(files.workspace_root, write=True):
            await finish_file_io(write)
    task = asyncio.create_task(operation())
    assert await asyncio.to_thread(running.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    fd = _open(files.workspace_root)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with workspace_lock(files.workspace_root, write=True):
        assert trace == ["io_finished"]
