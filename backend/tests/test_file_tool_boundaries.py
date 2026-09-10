"""文件资源边界回归；真实路径守卫，删除与外部记录全部 mock。"""

from copy import deepcopy
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent.agent_result import AgentResult
from services.agent.file_id import compute_fid, resolve_fid_to_workspace
from services.agent.file_path_cache import FilePathCache
from services.agent.file_tool_mixin import FileToolMixin
from services.agent.sandbox_tool_mixin import SandboxToolMixin
from services.agent.tool_output import FileRef
from services.file_executor import FileExecutor
from services.handlers.chat_tool_helpers import resolve_file_ids


class Owner(FileToolMixin, SandboxToolMixin):
    user_id = "actor"
    workspace_user_id = "workspace-owner"
    org_id = "org"
    conversation_id = "conv"


@pytest.fixture
def env(tmp_path, monkeypatch):
    executor = FileExecutor(str(tmp_path), user_id=Owner.workspace_user_id, org_id=Owner.org_id)
    workspace = Path(executor.workspace_root)
    staging = workspace / "staging" / "conv"
    staging.mkdir(parents=True)
    cache = FilePathCache()
    monkeypatch.setattr("services.agent.file_path_cache.get_file_cache", lambda _: cache)
    owner = Owner()
    owner._get_workspace_dir = lambda: str(workspace)
    owner._get_staging_dir = lambda: str(staging)
    owner._record_deleted_files = MagicMock()
    remove = MagicMock()
    monkeypatch.setattr("os.remove", remove)
    # 不能切到会立即删除 OSS 的另一个业务实现。
    executor.file_delete = AsyncMock(side_effect=AssertionError("do not replace the restore-preserving handler"))
    settings = SimpleNamespace(file_workspace_root=str(tmp_path), file_workspace_enabled=True)
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    return SimpleNamespace(
        owner=owner, executor=executor, workspace=workspace, staging=staging,
        cache=cache, remove=remove, settings=settings, outside=tmp_path / "outside.csv",
    )


def source(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture\n1\n")
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["relative", "absolute", "name", "fid", "both"])
async def test_safe_delete_uses_original_recording_and_does_not_mutate_args(env, protocol):
    path = source(env.workspace / "目录" / "销售 报表.csv")
    relative = str(path.relative_to(env.workspace))
    env.cache.register(relative, workspace=str(path))
    fid = compute_fid("org", relative)
    args = {
        "relative": {"files": [relative]},
        "absolute": {"files": [str(path)]},
        "name": {"files": [path.name]},
        "fid": {"file_ids": [fid]},
        "both": {"file_ids": [fid], "files": [relative]},
    }[protocol]
    original = deepcopy(args)
    result = await env.owner._file_delete(env.executor, args, env.settings)
    assert result.status == "success"
    assert "已删除 1 个文件" in result.summary
    env.remove.assert_called_once_with(str(path))
    env.owner._record_deleted_files.assert_called_once()
    records = env.owner._record_deleted_files.call_args.args[0]
    assert records == [{"raw": original.get("files", [str(path)])[0], "resolved": str(path)}]
    assert args == original
    env.executor.file_delete.assert_not_awaited()
    assert path.exists()  # 删除动作是 mock。


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["absolute", "relative_escape", "other_owner", "blocked", "staging", "symlink", "parent_symlink", "cached", "fid"])
async def test_entire_batch_is_rejected_before_any_delete(env, kind):
    safe = source(env.workspace / "safe.csv")
    outside = source(env.outside)
    if kind == "absolute":
        denied = str(outside)
    elif kind == "relative_escape":
        denied = "../../../outside.csv"
    elif kind == "other_owner":
        denied = str(source(env.workspace.parent / "other-owner" / "data.csv"))
    elif kind == "blocked":
        denied = str(source(env.workspace / ".env"))
    elif kind == "staging":
        denied = str(source(env.staging / "internal.parquet"))
    elif kind == "symlink":
        link = env.workspace / "link.csv"
        link.symlink_to(safe)
        denied = str(link)
    elif kind == "parent_symlink":
        link = env.workspace / "linked"
        link.symlink_to(outside.parent, target_is_directory=True)
        denied = "linked/outside.csv"
    else:
        env.cache.register("poison.csv", workspace=str(outside))
        denied = "poison.csv"
    args = {"files": [str(safe), denied]}
    if kind == "fid":
        args = {"files": [str(safe)], "file_ids": [compute_fid("org", "poison.csv")]}
    result = await env.owner._file_delete(env.executor, args, env.settings)
    assert result.is_failure
    assert result.metadata["retryable"] is False
    env.remove.assert_not_called()
    env.owner._record_deleted_files.assert_not_called()
    assert safe.exists() and outside.exists()


@pytest.mark.asyncio
async def test_chat_keeps_explicit_delete_path_until_scoped_handler(env):
    safe = source(env.workspace / "same.csv")
    outside = source(env.outside.parent / "same.csv")
    env.cache.register("same.csv", workspace=str(safe))
    args = {"files": [str(outside)]}
    # 即使缓存有同名合法文件，不能在守卫之前改写原始路径。
    prepared = resolve_file_ids(args, "conv", "file_delete")
    assert prepared == {"files": [str(outside)]}
    result = await env.owner._file_dispatch("file_delete", prepared)
    assert result.is_failure
    env.remove.assert_not_called()


@pytest.mark.asyncio
async def test_missing_target_rejects_batch_and_valid_duplicates_delete_once(env):
    safe = source(env.workspace / "safe.csv")
    result = await env.owner._file_delete(
        env.executor, {"files": ["missing.csv", "safe.csv", str(safe)]}, env.settings,
    )
    assert result.status == "error"
    assert "RESOURCE_NOT_FOUND" in result.summary
    env.remove.assert_not_called()
    result = await env.owner._file_delete(env.executor, {"files": ["safe.csv", str(safe)]}, env.settings)
    assert result.status == "success"
    assert "已删除 1 个" in result.summary
    env.remove.assert_called_once_with(str(safe))


@pytest.mark.asyncio
async def test_partial_io_failure_keeps_records_without_retry(env):
    first = source(env.workspace / "first.csv")
    second = source(env.workspace / "second.csv")
    env.remove.side_effect = [None, PermissionError("filesystem denied")]
    result = await env.owner._file_dispatch("file_delete", {"files": [str(first), str(second)]})
    assert result.is_failure
    assert result.metadata["retryable"] is False
    assert env.remove.call_count == 2
    env.owner._record_deleted_files.assert_called_once_with([
        {"raw": str(first), "resolved": str(first)},
    ])


@pytest.mark.parametrize("kind", ["relative_escape", "absolute", "other_owner", "symlink", "parent_symlink", "blocked", "staging", "directory"])
def test_stdout_does_not_register_disallowed_paths(env, kind):
    outside = source(env.outside)
    if kind == "relative_escape":
        value = "../../../outside.csv"
    elif kind == "absolute":
        value = str(outside)
    elif kind == "other_owner":
        value = str(source(env.workspace.parent / "other-owner" / "data.csv"))
    elif kind == "symlink":
        source(env.workspace / "inside.csv")
        (env.workspace / "link.csv").symlink_to(env.workspace / "inside.csv")
        value = "link.csv"
    elif kind == "parent_symlink":
        (env.workspace / "linked").symlink_to(outside.parent, target_is_directory=True)
        value = "linked/outside.csv"
    elif kind == "blocked":
        value = ".git/data.csv"
        source(env.workspace / value)
    elif kind == "staging":
        value = "staging/conv/internal.parquet"
        source(env.workspace / value)
    else:
        value = "directory.csv"
        (env.workspace / value).mkdir()
    env.owner._register_files_from_output(repr(value))
    assert env.cache.list_all() == []


@pytest.mark.parametrize("absolute", [False, True])
def test_stdout_registers_valid_files_and_ids(env, absolute):
    path = source(env.workspace / "下载" / "销售 报表.csv")
    relative = str(path.relative_to(env.workspace))
    value = str(path) if absolute else relative
    env.owner._register_files_from_output(f"已生成 {value!r}")
    assert resolve_fid_to_workspace(compute_fid("org", relative), "org", env.cache) == str(path)
    assert env.cache.resolve(path.name, "analyze") == str(path)


def artifact(path):
    return AgentResult(
        summary="internal result", file_ref=FileRef(
            path=str(path), filename=path.name, format="parquet",
            row_count=1, size_bytes=10, columns=[],
        ),
    )


@pytest.mark.parametrize("protocol", ["file_ref", "new_text", "legacy_text"])
def test_staging_artifacts_still_register_for_code(env, protocol):
    path = source(env.staging / "report.parquet")
    result = artifact(path) if protocol == "file_ref" else AgentResult(
        summary="read 'staging/report.parquet'" if protocol == "new_text" else "read STAGING_DIR + '/report.parquet'",
    )
    env.owner._register_staging_files(result)
    assert env.cache.resolve(path.name, "code") == str(path)
    assert env.cache.resolve(path.name, "analyze") == str(path)


@pytest.mark.parametrize("protocol", ["file_ref", "new_text", "legacy_text"])
def test_staging_registration_cannot_escape_current_conversation(env, protocol):
    outside = source(env.workspace / "staging" / "other-conv" / "report.parquet")
    result = artifact(outside) if protocol == "file_ref" else AgentResult(
        summary="read 'staging/../other-conv/report.parquet'" if protocol == "new_text" else "read STAGING_DIR + '/../other-conv/report.parquet'",
    )
    env.owner._register_staging_files(result)
    assert env.cache.list_all() == []


@pytest.mark.parametrize("kind", ["stdout", "staging"])
def test_optional_registration_does_not_create_missing_roots(env, kind):
    missing = env.workspace / "not-created"
    if kind == "stdout":
        env.owner._get_workspace_dir = lambda: str(missing)
        env.owner._register_files_from_output("'report.csv'")
    else:
        env.owner._get_staging_dir = lambda: str(missing)
        env.owner._register_staging_files(AgentResult(summary="read 'staging/report.parquet'"))
    assert not missing.exists()
    assert env.cache.list_all() == []


@pytest.mark.parametrize("fallback_json", [False, True])
def test_real_department_artifact_remains_readable(env, monkeypatch, fallback_json):
    import pandas as pd
    from services.agent.department_agent import DepartmentAgent
    from services.agent.tool_output import ColumnMeta

    rows = [{"sku": "fixture", "quantity": 2}]
    if fallback_json:
        monkeypatch.setattr(pd.DataFrame, "to_parquet", MagicMock(side_effect=ValueError("force JSON fallback")))
    ref, profile, _ = DepartmentAgent._write_to_staging(
        SimpleNamespace(domain="warehouse"), rows,
        [ColumnMeta(name="sku", dtype="text"), ColumnMeta(name="quantity", dtype="integer")],
        str(env.staging),
    )
    result = AgentResult(summary=profile, file_ref=ref)
    before = result.to_message_content()
    env.owner._register_staging_files(result)
    path = env.cache.resolve(ref.filename, "code")
    assert path == ref.path
    frame = pd.read_json(path) if fallback_json else pd.read_parquet(path)
    assert frame.to_dict("records") == rows
    assert result.file_ref is ref
    assert result.to_message_content() == before


@pytest.mark.asyncio
async def test_public_delete_uses_workspace_owner_not_actor(env):
    path = source(env.workspace / "owner.csv")
    actor_path = source(env.workspace.parent / "actor" / "owner.csv")
    result = await env.owner._file_dispatch("file_delete", {"files": ["owner.csv"]})
    assert result.status == "success"
    env.remove.assert_called_once_with(str(path))
    assert actor_path.exists()


@pytest.mark.asyncio
async def test_cancellation_propagates_and_records_completed_deletions(env):
    first = source(env.workspace / "first.csv")
    second = source(env.workspace / "second.csv")
    env.remove.side_effect = [None, asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await env.owner._file_dispatch("file_delete", {"files": [str(first), str(second)]})
    assert env.remove.call_count == 2
    env.owner._record_deleted_files.assert_called_once_with([
        {"raw": str(first), "resolved": str(first)},
    ])


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_available", [False, True])
async def test_code_execute_preserves_result_when_registering_output(env, monkeypatch, workspace_available):
    path = source(env.workspace / "report.csv")
    if not workspace_available:
        env.owner._get_workspace_dir = lambda: str(env.workspace / "missing-root")
    result = AgentResult(summary="generated 'report.csv'", metadata={"fixture": True})
    execute = AsyncMock(return_value=result)
    monkeypatch.setattr("services.sandbox.functions.build_sandbox_executor", lambda **kwargs: SimpleNamespace(execute=execute))
    monkeypatch.setattr("services.sandbox.kernel_manager.get_kernel_manager", lambda: None)
    env.settings.sandbox_enabled = True
    env.settings.sandbox_timeout = 10
    env.settings.sandbox_max_result_chars = 1000
    env.owner._record_sandbox_metric = MagicMock()
    env.owner._record_sandbox_knowledge = MagicMock()
    returned = await env.owner._code_execute({"code": "print('fixture')", "description": "fixture"})
    assert returned is result
    execute.assert_awaited_once_with("print('fixture')", "fixture")
    env.owner._record_sandbox_metric.assert_called_once()
    assert env.owner._record_sandbox_metric.call_args.kwargs["status"] == "success"
    env.owner._record_sandbox_knowledge.assert_not_called()
    assert env.cache.resolve(path.name, "analyze") == (str(path) if workspace_available else None)
