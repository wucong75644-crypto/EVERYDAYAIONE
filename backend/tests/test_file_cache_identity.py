"""文件登记顺序、同名隔离及搜索→附件→ID 读取回归；仅临时文件和 mock。"""

from itertools import permutations
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent.file_analysis_service import _build_analysis_result, _resolve_analysis_path, analyze_file
from services.agent.file_id import compute_fid, resolve_fid_to_workspace
from services.agent.file_meta import FileMeta
from services.agent.file_path_cache import FilePathCache
from services.agent.file_tool_mixin import FileToolMixin
from services.handlers.chat_context.attachments import build_workspace_prompt, format_attachments
from services.handlers.chat_context_mixin import ChatContextMixin


@pytest.mark.parametrize("order", list(permutations(["report.xlsx", "目录/report.xlsx", "附件/report.xlsx"])))
def test_registration_order_preserves_all_ids_and_analysis(order):
    cache = FilePathCache()
    source = "/ws/目录/report.xlsx"
    cache.register(order[0], workspace=source, parquet="/staging/report.parquet")
    cache.set_analyzed(order[0])
    for name in order:
        cache.register(name, workspace=source)
    for name in order:
        assert resolve_fid_to_workspace(compute_fid("org", name), "org", cache) == source
        assert cache.resolve(name) == "/staging/report.parquet"
        assert cache.is_analyzed(name)
    assert cache.list_all() == [{"name": "report.xlsx", "workspace": source, "parquet": "/staging/report.parquet"}]


@pytest.mark.parametrize("names", [("report.xlsx", "report.xlsx"), ("销售-报表.xlsx", "销售 报表.xlsx")])
@pytest.mark.parametrize("reverse", [False, True])
def test_same_or_normalized_names_are_distinct_files(names, reverse):
    cache = FilePathCache()
    paths = [f"甲/{names[0]}", f"乙/{names[1]}"]
    for path in reversed(paths) if reverse else paths:
        cache.register(Path(path).name, workspace=f"/ws/{path}")
        cache.register(path, workspace=f"/ws/{path}")
    cache.set_parquet(paths[0], "/staging/first.parquet")
    cache.set_analyzed(paths[0])
    for path in paths:
        assert resolve_fid_to_workspace(compute_fid("org", path), "org", cache) == f"/ws/{path}"
    assert cache.is_analyzed(paths[0])
    assert not cache.is_analyzed(paths[1])
    assert cache.resolve(paths[1]) is None
    assert len(cache.list_all()) == 2
    if names[0] == names[1]:
        assert cache.resolve(names[0], "analyze") is None
        assert resolve_fid_to_workspace(compute_fid("org", names[0]), "org", cache) is None
        cache.set_analyzed(names[0])
        assert not cache.is_analyzed(paths[1])
        with pytest.raises(FileNotFoundError):
            cache.resolve_path(names[0], "delete")
    else:
        assert cache.resolve("销售报表.xlsx", "analyze") is None


@pytest.mark.parametrize("query", ["report", "report2026"])
def test_ambiguous_stem_and_prefix_do_not_choose_first(query):
    cache = FilePathCache()
    for name in ["report2026first.csv", "report2026second.csv"] if query.endswith("2026") else ["report.csv", "report.xlsx"]:
        cache.register(name, workspace=f"/ws/{name}")
    assert cache.resolve(query, "analyze") is None


def test_eviction_removes_all_aliases_and_analysis_state():
    cache = FilePathCache(max_entries=1)
    cache.register("first.csv", workspace="/ws/甲/first.csv", parquet="/staging/first.parquet")
    cache.register("甲/first.csv", workspace="/ws/甲/first.csv")
    cache.register("乙/second.csv", workspace="/ws/乙/second.csv")
    for name in ["first.csv", "甲/first.csv", "/ws/甲/first.csv", "first"]:
        assert cache.resolve(name, "analyze") is None
        assert resolve_fid_to_workspace(compute_fid("org", name), "org", cache) is None
    assert len(cache.list_all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["search", "list", "describe", "manifest"])
async def test_discovery_then_real_attachment_registration_resolves_id(tmp_path, monkeypatch, entrypoint):
    cache = FilePathCache()
    monkeypatch.setattr("services.agent.file_path_cache.get_file_cache", lambda _: cache)
    relative = "已整理表格/report.csv"
    source = tmp_path / relative
    source.parent.mkdir()
    source.write_text("month,sales\n1,10\n")
    executor = SimpleNamespace(
        workspace_root=str(tmp_path),
        resolve_safe_path=lambda path: tmp_path / path,
        file_search=AsyncMock(return_value=f"  [文件] {relative}"),
        file_list_entries=AsyncMock(return_value={"error": None, "dirs": [], "path": "已整理表格", "files": [{"name": source.name, "abs_path": str(source), "size": 20}]}),
        _format_size=lambda _: "20B",
    )
    owner = FileToolMixin()
    owner.org_id = "org"
    owner.conversation_id = "conv"
    if entrypoint == "search":
        discovered = await FileToolMixin._search_files(owner, executor, {"keyword": "report"})
    elif entrypoint == "list":
        discovered = await FileToolMixin._list_directory(owner, executor, {})
    elif entrypoint == "describe":
        discovered = await FileToolMixin._describe_single_file(owner, executor, str(source))
    else:
        from services.handlers.resource_manifest import ResourceAsset, ResourceManifest
        owner.resource_manifest = ResourceManifest(
            task_id="task", input_message_id="message", source="task_attachment_refs",
            assets=(ResourceAsset(asset_id="asset", attachment_set_id="set", name=source.name, workspace_path=relative, mime_type="text/csv", size=20, url="https://cdn.example.com/report.csv"),),
        )
        discovered = await owner._search_manifest(executor, {"keyword": "report"})
    assert discovered.status == "success"
    fid = compute_fid("org", relative)
    assert (relative if entrypoint == "describe" else fid) in discovered.summary
    # 搜索本身就必须输出可解析的 ID，不能依赖随后插入附件修复缓存。
    assert resolve_fid_to_workspace(fid, "org", cache) == str(source)

    files = [{"name": source.name, "workspace_path": relative, "size": 20}]
    chat = SimpleNamespace(
        org_id="org", db=None,
        _extract_image_urls=lambda _: [], _extract_file_urls=lambda _: [],
        _extract_workspace_files=lambda _: files,
    )
    monkeypatch.setattr("core.config.get_settings", lambda: SimpleNamespace(file_workspace_root=str(tmp_path), messages_attachments_as_system=False))
    monkeypatch.setattr("core.workspace.resolve_workspace_dir", lambda *args: str(tmp_path))
    monkeypatch.setattr("core.workspace.resolve_staging_dir", lambda *args: str(tmp_path / "staging"))
    builder = MagicMock()
    builder.build = AsyncMock(return_value=SimpleNamespace(messages=[], static_block_chars=0, dynamic_block_chars=0, persona_injected=False, memory_injected=False))
    monkeypatch.setattr("services.prompt_builder.PromptBuilder", lambda _: builder)
    await ChatContextMixin._build_llm_messages(chat, [], "user", "conv", "读取这个文件")
    rendered = format_attachments(files, conversation_id="conv", org_id="org")
    attachment_fid = re.search(r"<id>(.*?)</id>", rendered).group(1)
    assert attachment_fid == fid
    assert _resolve_analysis_path(owner, executor, {"file_id": attachment_fid}, cache) == (str(source), fid)
    assert _resolve_analysis_path(owner, executor, {"path": relative}, cache) == (str(source), relative)
    # 实际分析编排只 mock 外部转换和元数据读取，验证首次 ID 调用就执行一次。
    owner.workspace_user_id = "user"
    staging = tmp_path / "staging"
    staging.mkdir()
    parquet = staging / "report.parquet"
    parquet.write_bytes(b"isolated parquet placeholder")
    convert = AsyncMock(return_value=(str(parquet), None))
    monkeypatch.setattr("services.agent.file_analysis_service._convert_to_parquet", convert)
    monkeypatch.setattr("services.agent.file_meta.read_file_meta", lambda _: FileMeta(source_file=str(source), summary={"row_count": 1, "col_count": 2, "sheet_count": 1}))
    result = await analyze_file(owner, executor, {"file_id": fid}, SimpleNamespace(file_workspace_root=str(tmp_path)))
    assert result.status == "success"
    convert.assert_awaited_once_with(executor, cache, str(source), str(staging))
    assert cache.resolve(relative) == str(parquet)

    convert.reset_mock()
    result = await analyze_file(owner, executor, {"file_id": "fid_00000000"}, SimpleNamespace(file_workspace_root=str(tmp_path)))
    assert result.is_failure
    assert "未找到" in result.summary
    convert.assert_not_awaited()


def test_analysis_and_attachment_status_target_full_path(tmp_path, monkeypatch):
    cache = FilePathCache()
    monkeypatch.setattr("services.agent.file_path_cache.get_file_cache", lambda _: cache)
    files = [{"name": "report.csv", "workspace_path": f"{folder}/report.csv"} for folder in ["甲", "乙"]]
    for item in files:
        source = tmp_path / item["workspace_path"]
        source.parent.mkdir()
        source.write_text("sales\n1\n")
        cache.register(item["workspace_path"], workspace=str(source))
    parquet = tmp_path / "first.parquet"
    parquet.write_bytes(b"isolated parquet placeholder")
    source = tmp_path / files[0]["workspace_path"]
    monkeypatch.setattr("services.agent.file_meta.read_file_meta", lambda _: FileMeta(source_file=str(source), summary={"row_count": 1, "col_count": 1, "sheet_count": 1}))
    result = _build_analysis_result(SimpleNamespace(workspace_root=str(tmp_path)), cache, str(source), str(parquet), str(tmp_path), None, 0.1)
    assert result.status == "success"
    assert cache.resolve(files[0]["workspace_path"]) == str(parquet)
    assert cache.resolve(files[1]["workspace_path"]) is None
    rendered = format_attachments(files, conversation_id="conv", org_id="org")
    assert re.findall(r"<status>(.*?)</status>", rendered) == ["analyzed", "raw"]
    assert re.findall(r"<parquet>(.*?)</parquet>", rendered) == ["staging/first.parquet"]
    prompt = build_workspace_prompt(files, conversation_id="conv", org_id="org")
    assert prompt.count("已分析") == 1
    assert prompt.count("待治理") == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_root_file_exact_path_wins_over_subdirectory_basename(reverse):
    cache = FilePathCache()
    paths = ["report.csv", "子目录/report.csv"]
    for path in reversed(paths) if reverse else paths:
        cache.register(Path(path).name, workspace=f"/ws/{path}")
        cache.register(path, workspace=f"/ws/{path}")
    for path in paths:
        assert resolve_fid_to_workspace(compute_fid("org", path), "org", cache) == f"/ws/{path}"
        assert cache.resolve(path, "analyze") == f"/ws/{path}"


def test_hash_collision_does_not_resolve_arbitrary_source(monkeypatch):
    cache = FilePathCache()
    cache.register("first.csv", workspace="/ws/first.csv")
    cache.register("second.csv", workspace="/ws/second.csv")
    monkeypatch.setattr("services.agent.file_id.compute_fid", lambda *args: "fid_12345678")
    assert resolve_fid_to_workspace("fid_12345678", "org", cache) is None


def test_directory_qualified_path_does_not_gain_basename_fuzzy_fallback():
    cache = FilePathCache()
    cache.register("甲/report.csv", workspace="/ws/甲/report.csv")
    # 显式指定另一个目录时，不新增 basename 归一化匹配；继续交给旧路径解析器。
    assert cache.resolve("乙/report-.csv", usage="analyze") is None
    executor = SimpleNamespace(resolve_safe_path=lambda path: Path("/ws") / path)
    assert _resolve_analysis_path(
        SimpleNamespace(), executor, {"path": "乙/report-.csv"}, cache,
    ) == ("/ws/乙/report-.csv", "乙/report-.csv")
