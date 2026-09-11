"""Current file identities must travel with their user turn, not only system context."""
import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from services.agent.file_id import compute_fid
from services.handlers.chat_context.attachments import format_attachments
from services.prompt_builder.builder import BuildInput, PromptBuilder
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput


def attachment(name="新订单.xlsx", folder="上传"):
    return {"name": name, "workspace_path": f"{folder}/{name}", "size": 6702,
            "mime_type": "application/octet-stream", "url": "https://example.test/file"}


@pytest.mark.parametrize("folder", ["工作区", "上传/2026-09"])
@pytest.mark.parametrize("name", ["订单.xlsx", "数据.csv", "文档.pdf", "图像.png"])
def test_user_turn_binds_current_attachment_with_original_text(folder, name):
    file = attachment(name, folder)
    source = copy.deepcopy(file)
    xml = format_attachments([file])
    result = UserLayer.render(UserMessageInput(
        text="读取文件", workspace_files=[file], attachments_xml=xml,
    ))
    content = result.user_message["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "读取文件"}
    refs = json.loads(content[1]["text"].split("\n", 1)[1])
    assert refs == [{"file_id": compute_fid(None, file["workspace_path"]),
                     "name": name, "path": file["workspace_path"]}]
    assert result.attachments_system_block == xml.strip()
    assert "<action>" not in content[1]["text"]
    assert file == source


def test_no_current_attachment_does_not_promote_history_or_workspace_inventory():
    result = UserLayer.render(UserMessageInput(
        text="你好", workspace_prompt="历史存在旧报表.xlsx",
    ))
    assert result.user_message == {"role": "user", "content": "你好"}


def test_mixed_files_keep_order_escaping_and_image_reference_once():
    files = [attachment('带空格 & "引号".xlsx'), attachment("图片.png")]
    result = UserLayer.render(UserMessageInput(
        text="比较附件", workspace_files=files,
        image_urls=["https://example.test/picture.png"],
    ))
    parts = result.user_message["content"]
    refs = json.loads(parts[1]["text"].split("\n", 1)[1])
    assert [r["path"] for r in refs] == [f["workspace_path"] for f in files]
    assert parts[2:] == [{"type": "image_url", "image_url": {"url": "https://example.test/picture.png"}}]
    assert parts[0]["text"] == "比较附件"


def test_legacy_inline_xml_is_not_repeated_as_an_extra_reference_block():
    file = attachment()
    xml = format_attachments([file], org_id="o1")
    result = UserLayer.render(UserMessageInput(
        text="读取文件", workspace_files=[file],
        attachments_xml=xml, attachments_as_system=False,
    ))
    assert result.user_message["content"] == "读取文件" + xml
    assert result.attachments_system_block is None


@pytest.mark.asyncio
@pytest.mark.parametrize("folder", ["工作区", "上传/2026-09"])
async def test_builder_provider_request_and_old_checkpoint_keep_current_file(monkeypatch, folder):
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    from services.handlers.chat.execution_engine import _build_replay_context

    file = attachment(folder=folder)
    old_history = [{"role": "user", "content": "读取旧表.xlsx"},
                   {"role": "assistant", "content": "没有新的附件，请重新上传。"}]
    monkeypatch.setattr(PromptBuilder, "_parallel_fetch", AsyncMock(return_value=(None, None, old_history)))
    result = await PromptBuilder(BuildInput(
        user_id="u1", conversation_id="binding-test", org_id="o1",
        text_content="读取文件", workspace_files=[file],
        personal_context_allowed=False,
    )).build()
    original = copy.deepcopy(result.messages)
    captured = []
    def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"offline"}}]}\n\ndata: [DONE]\n\n')
    adapter = DashScopeChatAdapter(api_key="offline", model="qwen3.5-plus")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="https://example.test") as client:
        adapter._client = client
        _ = [chunk async for chunk in adapter.stream_chat(result.messages)]
    sent = captured[0]["messages"]
    refs = json.loads(sent[-1]["content"][1]["text"].split("\n", 1)[1])
    assert refs[0]["file_id"] == compute_fid("o1", file["workspace_path"])
    assert refs[0]["path"] == file["workspace_path"]
    assert "旧表.xlsx" not in sent[-1]["content"][1]["text"]
    assert sent[-1]["content"][0]["text"] == "读取文件"
    assert sent == original == _build_replay_context(result.messages, [], 0)["messages"]
