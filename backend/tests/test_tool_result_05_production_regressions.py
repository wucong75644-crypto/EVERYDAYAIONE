"""Sanitized production reproduction: old export request, new read-only attachment.

The model transport is captured, not called. These tests prove input/selector
contracts; they do not claim a live model will always follow the request.
"""
import json
import re
from xml.etree import ElementTree
from unittest.mock import AsyncMock

import httpx
import pytest

from services.agent.agent_result import AgentResult
from services.agent.file_id import compute_fid
from services.handlers.chat_context.attachments import format_attachments, build_workspace_prompt
from services.handlers.chat.execution_engine import _build_replay_context
from services.handlers.resource_manifest import ResourceManifest, ResourceAsset
from services.prompt_builder.builder import PromptBuilder
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
from tests.test_file_target_execution import fixture


def attachment(path="整理/新表.csv"):
    return {"name": path.rsplit("/", 1)[-1], "workspace_path": path, "size": 12}


@pytest.mark.parametrize("as_system", [True, False])
@pytest.mark.parametrize("cache_control", [True, False])
@pytest.mark.parametrize("text", ["读取文件", "按刚才的方式生成汇总表和图"])
async def test_current_user_and_attachment_survive_provider_and_checkpoint(monkeypatch, as_system, cache_control, text):
    from core.config import get_settings
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    monkeypatch.setattr(get_settings(), "prompt_cache_control_enabled", cache_control)
    files = [attachment()]
    history = [
        {"role": "user", "content": "统计旧表，生成汇总 Excel 和图供下载\n[附件]旧表.csv"},
        {"role": "assistant", "content": "旧表统计完成，文件和图已生成。"},
    ]
    user = UserLayer.render(UserMessageInput(
        text=text, workspace_files=files, attachments_as_system=as_system, org_id="o1",
        attachments_xml=format_attachments(files, org_id="o1"),
        workspace_prompt=build_workspace_prompt(files, org_id="o1"),
    ))
    messages = PromptBuilder._compose_messages(
        "静态规则", "会话规则", "本轮时间", history, None, user,
    )
    captured = []
    def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"读取完成"}}]}\n\ndata: [DONE]\n\n')
    adapter = DashScopeChatAdapter(api_key="offline-test", model="qwen3.5-plus")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="https://offline.test") as client:
        adapter._client = client
        _ = [chunk async for chunk in adapter.stream_chat(messages)]
    sent = captured[0]["messages"]
    assert sent == messages  # Actual provider payload has the same sequence.
    end_of_history = sent.index(history[-1])
    assert sent.index(history[0]) < end_of_history
    current_indices = [i for i, m in enumerate(sent) if "新表.csv" in str(m["content"])]
    assert current_indices
    assert sent[-1] == user.user_message
    if as_system:
        parts = sent[-1]["content"]
        assert len(parts) == 2
        assert parts[0] == {"type": "text", "text": text}
        assert parts[1]["type"] == "text"
        label, refs_json = parts[1]["text"].split("\n", 1)
        assert label == "本条消息附件："
        assert json.loads(refs_json) == [{
            "file_id": compute_fid("o1", files[0]["workspace_path"]),
            "name": files[0]["name"], "path": files[0]["workspace_path"],
        }]
    else:
        assert sent[-1]["content"] == text + format_attachments(files, org_id="o1")
    focus = next(m["content"] for m in sent if "以用户最新一条消息为准" in str(m["content"]))
    assert "明确要求继续" in focus
    replay = _build_replay_context(messages, [], 0)
    assert replay["messages"] == sent


@pytest.mark.parametrize("entry", ["chat", "loop"])
async def test_attachment_call_is_valid_without_inventing_reference(fixture, monkeypatch, entry):
    from tests.test_tool_production_integration import invoke, tc
    e, _, create = fixture
    file = attachment()
    create(file["workspace_path"])
    e.resource_manifest = ResourceManifest("task1", "input1", (
        ResourceAsset("asset1", file["name"], file["workspace_path"], "text/csv", 12, ""),
    ), "input_message")
    raw = AgentResult("文件已读取：1 行，字段 x", source="file_analyze")
    e._handlers["file_analyze"] = AsyncMock(return_value=raw)
    xml = format_attachments([file], org_id="o1")
    root = ElementTree.fromstring(xml[:xml.index("</attachments>") + len("</attachments>")].strip())
    call = root.find("file/read_call")
    assert call is not None, "Attached file must supply a copyable read call, not only an ambiguous ID."
    assert call.attrib["tool"] == "file_analyze"
    args = json.loads(call.text)
    assert set(args) == {"file_id"}
    result = await invoke(entry, e, [tc("file_analyze", args)], monkeypatch)
    e._handlers["file_analyze"].assert_awaited_once()
    assert "resource_ref" not in e._handlers["file_analyze"].call_args.args[0]
    # The normal consumer still receives its original projection; no model/WS format change.
    if entry == "chat":
        assert result[0].model_content("chat") == raw.to_message_content()
    else:
        assert result == [raw.to_tool_content()]


async def test_bad_extra_reference_stays_rejected_before_dispatch(fixture):
    from services.agent.file_id import compute_fid
    e, _, create = fixture
    create("report.csv")
    e._handlers["file_analyze"] = AsyncMock()
    result = await e.tool_runtime.execute("file_analyze", {
        "file_id": compute_fid("o1", "report.csv"), "resource_ref": "report.csv",
    })
    assert "RESOURCE_REFERENCE_INVALID" in str(result.exception)
    assert not result.execution.handler_started
    e._handlers["file_analyze"].assert_not_awaited()


@pytest.mark.parametrize("entry", ["chat", "loop"])
@pytest.mark.parametrize("search", [
    {"keyword": "report", "scope": "workspace"},
    {"path": "reports", "scope": "workspace"},
    {"path": "reports/report.csv", "scope": "workspace"},
    {"scope": "current"},
])
async def test_search_read_call_uses_verified_reference(fixture, monkeypatch, entry, search):
    from tests.test_tool_production_integration import invoke, tc
    from services.file_resources import FileTargetResolver
    e, _, create = fixture
    target = create("reports/report.csv")
    e.resource_manifest = ResourceManifest("task1", "input1", (
        ResourceAsset("asset1", target.name, "reports/report.csv", "text/csv", 4, ""),
    ), "input_message")
    raw_results = []
    original = e._handlers["file_analyze"]
    async def analyze(args):
        raw = await original(args)
        raw_results.append(raw)
        return raw
    e._handlers["file_analyze"] = AsyncMock(side_effect=analyze)
    found = await e.execute("file_search", search)
    args = json.loads(re.search(r"read_call \(file_analyze\): (.+)", found.summary)[1])
    assert set(args) == {"resource_ref"}
    path, version = FileTargetResolver(e).codec.decode(args["resource_ref"])
    assert path == "reports/report.csv" and version
    assert f"resource_ref: {args['resource_ref']}" in found.summary
    assert "file_analyze('" not in found.summary  # No misleading positional/path example.
    result = await invoke(entry, e, [tc("file_analyze", args)], monkeypatch)
    e._handlers["file_analyze"].assert_awaited_once()
    assert e._handlers["file_analyze"].call_args.args[0]["path"] == str(target)
    raw = raw_results[0]
    assert raw.status == "success" and "<file_analysis>" in raw.summary
    assert (result[0].model_content("chat") if entry == "chat" else result[0]) == (
        raw.to_message_content() if entry == "chat" else raw.to_tool_content())


async def test_generated_search_read_call_rejects_changed_file(fixture):
    e, _, create = fixture
    target = create("report.csv")
    found = await e.execute("file_search", {"keyword": "report", "scope": "workspace"})
    args = json.loads(re.search(r"read_call \(file_analyze\): (.+)", found.summary)[1])
    target.write_text("x\nchanged\n")
    e._handlers["file_analyze"] = AsyncMock()
    result = await e.tool_runtime.execute("file_analyze", args)
    assert "RESOURCE_CHANGED" in str(result.exception)
    assert not result.execution.handler_started
    e._handlers["file_analyze"].assert_not_awaited()


async def test_non_tabular_search_has_no_analyze_call(fixture):
    e, _, create = fixture
    create("note.txt")
    found = await e.execute("file_search", {"path": "note.txt", "scope": "workspace"})
    assert "resource_ref:" in found.summary and "read_call" not in found.summary


def test_attachment_and_tool_schemas_share_selector_contract():
    from config.file_call_contract import FILE_ANALYZE_SELECTOR_GUIDANCE, file_analyze_arguments
    from config.file_tools import build_file_tools
    assert FILE_ANALYZE_SELECTOR_GUIDANCE in format_attachments([attachment()], org_id="o1")
    schemas = {tool["function"]["name"]: tool["function"] for tool in build_file_tools()}
    for name in ("file_search", "file_analyze"):
        assert FILE_ANALYZE_SELECTOR_GUIDANCE in schemas[name]["description"]
    for args in ({}, {"file_id": "fid_12345678", "resource_ref": "fref1_test"}):
        with pytest.raises(ValueError, match="exactly one"):
            file_analyze_arguments(**args)
