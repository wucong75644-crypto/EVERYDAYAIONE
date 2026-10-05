"""Upload -> prompt discussion -> confirmation preserves exact image identity."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from services.handlers.chat_context.history_loader import _row_to_oai_messages
from services.handlers.chat_image_request import ChatImageInputResolver
from services.handlers.conversation_cache import get_closed_messages as real_cache_get
from services.media_tool_executor import MediaToolMixin


def locators(messages):
    for message in messages:
        content = message.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for part in parts:
            text = part.get("text") or ""
            if "对话图片定位" in text:
                return json.loads(text.split("对话图片定位", 1)[1].split("\n", 1)[1])
    raise AssertionError("Historical image identity was dropped")


@pytest.fixture
def source(tmp_path, mock_db):
    from services.file_executor import FileExecutor
    from services.tools.resource_access import ResourceAccessBoundary, ResourceRule

    user = "00000000-0000-4000-8000-000000000001"
    files = FileExecutor(str(tmp_path), user, None)
    for name, color in (("A.png", "blue"), ("B.png", "red")):
        Image.new("RGB", (4, 4), color).save(files.resolve_safe_path(name))
    row = {"id": "source-message", "role": "user", "status": "completed", "org_id": None,
           "conversation_id": "conversation", "context_revision": 1,
           "content": [{"type": "text", "text": "先分析这两张，暂不生成"},
                       {"type": "image", "name": "A.png", "workspace_path": "A.png", "url": "https://media.test/A"},
                       {"type": "text", "text": "B是产品原图"},
                       {"type": "image", "name": "B.png", "workspace_path": "B.png", "url": "https://media.test/B"}]}
    mock_db.set_table_data("messages", [row])
    owner = SimpleNamespace(db=mock_db, user_id=user, workspace_user_id=user, org_id=None,
                            context_scope="user", conversation_id="conversation", resource_manifest=None,
                            resource_access_boundary=ResourceAccessBoundary(
                                (ResourceRule(("read",), directories=(".",)),), "test", True))
    return row, owner, files


@pytest.mark.parametrize("serialized", [False, True])
def test_confirmation_can_select_b_original_without_guessing_a_file_id(source, serialized):
    row, owner, files = source
    projected = dict(row, content=json.dumps(row["content"]) if serialized else row["content"])
    messages, count = _row_to_oai_messages(projected, 10)
    refs = locators(messages)
    assert count == 2
    assert [ref["reference"] for ref in refs] == [{"message_id": "source-message", "content_index": 1},
                                              {"message_id": "source-message", "content_index": 3}]
    assert [ref["name"] for ref in refs] == ["A.png", "B.png"]
    assert all(ref["file_id"].startswith("fid_") and ref["available"] for ref in refs)
    assert "workspace_path" not in json.dumps(refs) and "url" not in json.dumps(refs)
    resolver = ChatImageInputResolver(owner, base_revision=1, input_message_id="confirmation", files=files)
    selected = {k: refs[1][k] for k in ("message_id", "content_index")}
    result = resolver.resolve([{**selected, "role": "product"}])
    assert len(result) == 1 and result[0]["workspace_path"] == "B.png"
    assert result[0]["content_sha256"] == hashlib.sha256(files.resolve_safe_path("B.png").read_bytes()).hexdigest()
    resolver.verify(result)


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_locators_survive_visual_budget_without_granting_or_selecting_references(source, role):
    row, _, _ = source
    messages, count = _row_to_oai_messages(dict(row, role=role), 0)
    assert count == 0 and len(locators(messages)) == 2
    assert "不是自动选定" in json.dumps(messages, ensure_ascii=False)
    assert '"type": "image_url"' not in json.dumps(messages)
    assert "https://media.test/" not in json.dumps(locators(messages))


@pytest.mark.parametrize("changed", [{"context_revision": 2}, {"conversation_id": "other"}, {"org_id": "other"}])
def test_presenting_a_locator_does_not_override_revision_or_scope(source, changed):
    row, owner, files = source
    refs = locators(_row_to_oai_messages(row, 10)[0])
    owner.db.set_table_data("messages", [{**row, **changed}])
    resolver = ChatImageInputResolver(owner, base_revision=1, input_message_id="confirmation", files=files)
    with pytest.raises(PermissionError, match="IMAGE_SOURCE_(?:REVISION|MESSAGE)_DENIED"):
        resolver.resolve([refs[1]["reference"] | {"role": "product"}])


def test_locators_still_recheck_current_file_read_permission(source):
    from services.tools.resource_access import ResourceAccessBoundary, ResourceRule
    row, owner, files = source
    refs = locators(_row_to_oai_messages(row, 10)[0])
    owner.resource_access_boundary = ResourceAccessBoundary((ResourceRule(("list",), directories=(".",)),), "test", True)
    resolver = ChatImageInputResolver(owner, base_revision=1, input_message_id="confirmation", files=files)
    with pytest.raises(PermissionError, match="RESOURCE_ACTION_DENIED"):
        resolver.resolve([refs[1]["reference"] | {"role": "product"}])


def test_failed_and_unpersisted_images_do_not_advertise_generation_locators():
    messages, _ = _row_to_oai_messages({"id": "m", "role": "assistant", "content": [
        {"type": "image", "failed": True, "workspace_path": "A.png"},
        {"type": "image", "url": "https://media.test/temporary"},
    ]}, 5)
    refs = locators(messages)
    assert all(not ref["available"] and "reference" not in ref and "file_id" not in ref for ref in refs)


@pytest.mark.asyncio
async def test_missing_reference_is_known_rejection_with_no_task_or_credit_io(source, tmp_path, monkeypatch):
    from core.config import Settings
    from services.tools.dispatcher import _dispatch_call_id

    _, facts, _ = source
    owner = MediaToolMixin()
    owner.__dict__.update(vars(facts), task_id="parent", image_execution_token="token",
                          execution_mode="interactive", cancellation_event=None)
    owner.db.set_table_data("tasks", [{"id": "parent", "user_id": owner.user_id, "org_id": None,
        "conversation_id": owner.conversation_id, "turn_id": "turn", "input_message_id": "confirmation",
        "base_context_revision": 1}])
    owner.db.rpc = Mock(side_effect=AssertionError("Must stop before accepting or locking credits"))
    settings = Settings(_env_file=None, database_url="postgresql://invalid/test", jwt_secret_key="isolated-test-key",
                        file_workspace_root=str(tmp_path), chat_image_async_enabled=True)
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    provider = Mock(side_effect=AssertionError("No provider IO"))
    monkeypatch.setattr("services.adapters.factory.create_image_adapter", provider)
    token = _dispatch_call_id.set("call")
    try:
        result = await owner._generate_image({"mode": "image_to_image", "prompt": "完整原文",
            "references": [{"file_id": "fid_2a8b9c3d", "role": "product"}]})
    finally:
        _dispatch_call_id.reset(token)
    assert result.error_message == "IMAGE_SOURCE_MESSAGE_DENIED"
    assert result.metadata["accepted"] is False and result.metadata["submission_state"] == "not_accepted"
    assert "参考图" in result.summary and "get_conversation_context" in result.summary
    assert "不能用 size/format" not in result.summary
    owner.db.rpc.assert_not_called()
    provider.assert_not_called()


def test_resource_rejection_does_not_claim_schema_correction_was_attempted():
    from services.agent.agent_result import AgentResult
    from services.handlers.chat.image_argument_correction import ImageArgumentCorrection
    blocks = [{"tool_call_id": "call"}]
    guard = ImageArgumentCorrection(blocks)
    call = {"id": "call", "name": "generate_image", "arguments": json.dumps({
        "mode": "image_to_image", "prompt": "原文", "references": [{"file_id": "fid_2a8b9c3d", "role": "product"}]})}
    result = AgentResult(summary="参考图未找到", status="error", error_message="RESOURCE_NOT_FOUND",
                         metadata={"accepted": False, "submission_state": "not_accepted"})
    guard.observe([(call, result, True, result.summary)])
    assert guard.repair is None and not guard.start_round(1)
    assert "参数自动纠正" not in guard.stop_message
    assert "参考图未找到" in guard.stop_message
    assert guard.summary()["correction_rounds"] == 0


@pytest.mark.asyncio
async def test_unreadable_original_is_rejected_before_acceptance(source, tmp_path, monkeypatch):
    from core.config import Settings
    from services.tools.dispatcher import _dispatch_call_id
    _, facts, files = source
    files.resolve_safe_path("B.png").write_bytes(b"not image bytes")
    owner = MediaToolMixin()
    owner.__dict__.update(vars(facts), task_id="parent", image_execution_token="token",
                          execution_mode="interactive", cancellation_event=None)
    owner.db.set_table_data("tasks", [{"id": "parent", "user_id": owner.user_id, "org_id": None,
        "conversation_id": owner.conversation_id, "turn_id": "turn", "input_message_id": "confirmation",
        "base_context_revision": 1}])
    owner.db.rpc = Mock(side_effect=AssertionError("No acceptance IO"))
    monkeypatch.setattr("core.config.get_settings", lambda: Settings(_env_file=None,
        database_url="postgresql://invalid/test", jwt_secret_key="isolated-test-key",
        file_workspace_root=str(tmp_path), chat_image_async_enabled=True))
    token = _dispatch_call_id.set("call")
    try:
        result = await owner._generate_image({"mode": "image_to_image", "prompt": "原文",
            "references": [{"message_id": "source-message", "content_index": 3, "role": "product"}]})
    finally:
        _dispatch_call_id.reset(token)
    assert result.is_failure and result.error_message == "IMAGE_INPUT_UNAVAILABLE"
    assert result.metadata["accepted"] is False and "参考图" in result.summary
    owner.db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_old_projection_cache_is_not_reused(monkeypatch):
    old_key = "conv:msgs:outcomes-v1:org:conversation"
    storage = {old_key: json.dumps({"schema_version": 2, "revision": 1, "through_message_id": "source-message",
                                   "closed_messages": [{"role": "user", "content": "only pixels"}]})}
    redis = AsyncMock()
    redis.get.side_effect = lambda key: storage.get(key)
    monkeypatch.setattr("services.handlers.conversation_cache.get_redis", AsyncMock(return_value=redis))
    assert await real_cache_get("conversation", 1, "source-message", "org") is None
    assert storage[old_key]
    redis.delete.assert_not_awaited()
