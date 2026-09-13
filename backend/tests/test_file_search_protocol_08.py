"""Full names -> scoped identity -> confirmed action, across public entrances."""
import re
from unittest.mock import AsyncMock

import pytest

from services.handlers.resource_manifest import ResourceAsset, ResourceManifest
from services.tools.resource_access import ResourceAccessBoundary, ResourceRule
from tests.test_file_target_execution import fixture
from tests.test_tool_production_integration import invoke, tc

NAME = "6月23日各平台订单周同比_c7a21c.xlsx"
SPACED = "6 月 23 日各平台订单周同比_c7a21c.xlsx"


def manifest(e, files, paths):
    e.resource_manifest = ResourceManifest("task1", "input1", tuple(
        ResourceAsset(str(i), p.name, str(p.relative_to(files.workspace_root)),
                      "application/octet-stream", p.stat().st_size, "")
        for i, p in enumerate(paths)), "input_message")


@pytest.mark.parametrize("scope", ["current", "workspace"])
@pytest.mark.parametrize("name", [NAME, SPACED])
async def test_full_name_uses_existing_scoped_identity(fixture, scope, name):
    e, files, create = fixture
    target = create("上传/2026-09/" + NAME)
    manifest(e, files, [target] if scope == "current" else [])
    result = await e.execute("file_search", {"path": name, "scope": scope})
    assert result.status == "success"
    assert "上传/2026-09/" + NAME in result.summary
    assert re.search(r"\[fid_[a-z0-9]{8}\]", result.summary)
    assert "resource_ref: fref1_" in result.summary
    assert target.exists()
    e.tool_confirmer.assert_not_awaited()


@pytest.mark.parametrize("entry", ["legacy", "chat", "loop"])
@pytest.mark.parametrize("outcome", ["approved", "rejected", "timeout", "changed"])
async def test_name_search_then_reference_delete(fixture, monkeypatch, entry, outcome):
    e, files, create = fixture
    target = create("上传/2026-09/" + NAME)
    manifest(e, files, [])
    e._handlers["file_search"] = AsyncMock(wraps=e._handlers["file_search"])
    await invoke(entry, e, [tc("file_search", {"path": NAME, "scope": "workspace"}, "search")], monkeypatch)
    assert e._handlers["file_search"].await_count == 1
    # Read the real scoped result, not a hand-written replacement for the
    # search selector. Public execute has no caller-supplied result cache here.
    found = await e.execute("file_search", {"path": NAME, "scope": "workspace"})
    ref = re.search(r"resource_ref: (\S+)", found.summary)[1]
    if outcome == "rejected":
        e.tool_confirmer.return_value = False
    elif outcome == "timeout":
        e.tool_confirmer.side_effect = TimeoutError("confirmation expired")
    elif outcome == "changed":
        async def change(*_):
            target.write_text("changed after selection")
            return True
        e.tool_confirmer.side_effect = change
    output = await invoke(entry, e, [tc("file_delete", {"resource_refs": [ref]}, "delete")], monkeypatch)
    assert target.exists() is (outcome != "approved"), output
    assert e._handlers["file_delete"].await_count == (1 if outcome == "approved" else 0)
    assert e._record_deleted_files.call_count == (1 if outcome == "approved" else 0)
    assert e.tool_confirmer.await_count == 1


@pytest.mark.parametrize("scope", ["current", "workspace"])
@pytest.mark.parametrize("kind", ["same_name", "normalized_collision"])
async def test_ambiguous_name_is_not_swallowed_into_keyword_search(fixture, scope, kind):
    e, files, create = fixture
    first = create("a/销售报表.csv")
    second = create("b/" + ("销售报表.csv" if kind == "same_name" else "销售 报表.csv"))
    manifest(e, files, [first, second] if scope == "current" else [])
    name = "销售报表.csv" if kind == "same_name" else "销 售 报 表.csv"
    e._handlers["file_search"] = AsyncMock(wraps=e._handlers["file_search"])
    result = await e.tool_runtime.execute("file_search", {"path": name, "scope": scope})
    assert "RESOURCE_AMBIGUOUS" in str(result.exception)
    assert "a/销售报表.csv" in str(result.exception) and "b/" in str(result.exception)
    assert not result.execution.handler_started
    e._handlers["file_search"].assert_not_awaited()
    direct = await e._file_dispatch("file_search", {"path": name, "scope": scope})
    assert direct.status == "error" and direct.metadata["error_code"] == "RESOURCE_AMBIGUOUS"
    assert len(direct.metadata["candidates"]) == 2


@pytest.mark.parametrize("scope", ["current", "workspace"])
async def test_literal_spaces_win_before_normalized_candidates(fixture, scope):
    e, files, create = fixture
    target = create("a/销售 报表.csv")
    other = create("b/销售报表.csv")
    manifest(e, files, [target, other] if scope == "current" else [])
    found = await e.execute("file_search", {"path": target.name, "scope": scope})
    assert found.status == "success" and "a/销售 报表.csv" in found.summary
    assert "b/销售报表.csv" not in found.summary


async def test_explicit_wrong_path_never_falls_back_to_basename(fixture):
    e, _, create = fixture
    create("right/" + NAME)
    result = await e.execute("file_search", {"path": "wrong/" + NAME, "scope": "workspace"})
    assert result.status == "error" and "resource_ref:" not in result.summary


async def test_interactive_missing_scope_finds_workspace_and_empty_is_truthful(fixture):
    e, files, create = fixture
    create("上传/" + NAME)
    manifest(e, files, [])
    result = await e.execute("file_search", {"keyword": "c7a21c"})
    assert result.status == "success" and NAME in result.summary
    current = await e.execute("file_search", {"keyword": "c7a21c", "scope": "current"})
    assert current.status == "empty" and "当前任务资源" in current.summary
    assert current.metadata["resource_scope"] == "current"
    result = await e.execute("file_search", {"keyword": "not-a-file", "scope": "workspace"})
    assert result.status == "empty" and "工作区获准搜索范围" in result.summary
    assert result.metadata["resource_scope"] == "workspace"


async def test_search_name_does_not_enlarge_resource_authorization(fixture):
    e, files, create = fixture
    create("private/" + NAME)
    manifest(e, files, [])
    e.resource_access_boundary = ResourceAccessBoundary(
        (ResourceRule(("list",), directories=("allowed",)),), "test", True)
    e._handlers["file_search"] = AsyncMock(wraps=e._handlers["file_search"])
    result = await e.tool_runtime.execute("file_search", {"path": NAME, "scope": "workspace"})
    assert not result.execution.handler_started
    assert "private/" not in str(result.exception)
    e._handlers["file_search"].assert_not_awaited()


def test_model_scope_optional_and_legacy_validation_stays_compatible():
    from services.tools.catalog import definition_registry
    from services.agent.tool_args_validator import validate_tool_args
    spec = definition_registry().require("file_search")
    assert spec.to_schema()["function"]["parameters"].get("required", []) == []
    assert spec.to_legacy_validation_schema()["required"] == []
    # The execution compatibility facade continues to accept historical calls.
    assert validate_tool_args("file_search", {"keyword": "report"},
        [{"function": {"name": "file_search", "parameters": spec.to_legacy_validation_schema()}}]) == ({"keyword": "report"}, None)
    assert validate_tool_args("file_search", {"keyword": "report"}, [spec.to_schema()]) == ({"keyword": "report"}, None)


async def test_legacy_loop_calls_keep_original_optional_scope(fixture):
    from copy import deepcopy
    from tests.test_tool_production_integration import loop_for
    e, files, create = fixture
    create("uploads/" + NAME)
    manifest(e, files, [])
    loop, ctx = loop_for(e)
    tools = deepcopy(e.tool_runtime.advertised())
    next(t for t in tools if t["function"]["name"] == "file_search")["function"]["parameters"].pop("required", None)
    await loop._execute_tools([tc("file_search", {"scope": "workspace"}, "root")], tools, "", ctx)
    await loop._execute_tools([tc("file_search", {"path": "uploads/"}, "browse")], tools, "", ctx)
    output = [m["content"] for m in ctx.messages if m["role"] == "tool"][-1]
    assert NAME in output and "resource_ref: fref1_" in output


@pytest.mark.parametrize("scope", ["current", "workspace"])
async def test_full_name_miss_reports_effective_scope(fixture, scope):
    e, files, _ = fixture
    manifest(e, files, [])
    result = await e.tool_runtime.execute("file_search", {"path": NAME, "scope": scope})
    assert not result.execution.handler_started
    assert result.error.retry_context["effective_scope"] == scope
