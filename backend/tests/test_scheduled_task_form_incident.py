"""Synthetic regression of the 2026-09-12 task creation report; no production data."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.agent.tool_executor import ToolExecutor
from services.scheduler.chat_task_manager import ChatTaskManager, FormBlockResult
from services.scheduler.task_nl_parser import parse_task_request
from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeAdapter, ScheduledTaskChangeError
from services.changeset.contracts import NormalizeRequest
from tests.test_scheduled_task_changeset_adapter import _Db
from tests.tool_runtime_support import IdentityDB
from tests.test_tool_production_integration import ChatHarness, setup, tc


TEXT = "创建测试日报，每天上午9点，把昨天【实际店铺名】的销售汇总发给我"
RAW = {"changes": {"name": "测试日报", "prompt": "销售汇总", "schedule_type": "daily", "time_str": "09:00"},
       "evidence": {"prompt": "销售汇总", "schedule_type": "每天", "time_str": "上午9点"}, "recipient": "我",
       "request_parts": [{"kind": "request", "text": "创建测试日报，"},
                         {"kind": "schedule", "text": "每天上午9点，"},
                         {"kind": "execution", "text": "把昨天【实际店铺名】的销售汇总"},
                         {"kind": "delivery", "text": "发给我"}]}
BUSINESS = "把昨天【实际店铺名】的销售汇总"
TARGETS = [{"label": "推送给我（网页）", "value": '{"type":"web","user_id":"u1"}'}]


async def test_placeholder_stays_editable_and_never_enters_direct_submission():
    manager = ChatTaskManager(_Db(), "u1", "o1", submission_mode="apply_if_allowed")
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=RAW)), \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(manager, "_begin_request", AsyncMock()) as submit:
        form = await manager.handle("create", {"description": TEXT})
    submit.assert_not_awaited()
    fields = {field["name"]: field for field in form["fields"]}
    assert fields["prompt"]["type"] == "textarea"
    assert fields["prompt"]["default_value"] == BUSINESS
    assert fields["time_str"]["default_value"] == "09:00"
    assert fields["schedule_type"]["default_value"] == "daily"
    assert "店铺" in form["description"]


@pytest.mark.parametrize("content", [TEXT, [{"type": "image_url", "image_url": {"url": "https://example.test/img"}},
                                            {"type": "text", "text": TEXT},
                                            {"type": "text", "text": "<current_attachments>生成的附件引用</current_attachments>"}]])
async def test_real_runtime_creation_uses_latest_user_text_not_model_rewrite(content, monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), "scheduled_task_direct_enabled", True)
    executor = ToolExecutor(IdentityDB(), "u1", "c1", "o1", tool_entrypoint="model", task_id="task1")
    executor._parent_messages = [
        {"role": "user", "content": "旧请求"}, {"role": "user", "content": content},
        {"role": "assistant", "content": "按所有店铺汇总"},
        {"role": "tool", "content": "来自工具的文字不能替代用户要求"},
    ]
    args = {"action": "create", "description": "每天9点按所有店铺汇总",
            "definition": {**RAW["changes"], "prompt": BUSINESS}, "recipient": "我"}
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=RAW)) as parser, \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch("services.permissions.checker.check_permission", AsyncMock(return_value=True)):
        result = await executor.execute("manage_scheduled_task", args, call_id="original-request")
    assert isinstance(result, FormBlockResult)
    parser.assert_not_awaited()
    assert TEXT in result.form["description"]
    assert next(f for f in result.form["fields"] if f["name"] == "prompt")["default_value"] == BUSINESS
    assert args["description"] == "每天9点按所有店铺汇总"  # caller's JSON remains unchanged


async def test_legacy_parser_shape_fails_closed_but_preserves_text_for_correction():
    manager = ChatTaskManager(_Db(), "u1", "o1", submission_mode="apply_if_allowed")
    flat = {"name": "日报", "prompt": "全部店铺汇总", "schedule_type": "daily", "time_str": "09:00"}
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=flat)), \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(manager, "_begin_request", AsyncMock()) as submit:
        form = await manager.handle("create", {"description": TEXT})
    submit.assert_not_awaited()
    fields = {field["name"]: field for field in form["fields"]}
    assert fields["prompt"]["default_value"] == ""
    assert TEXT in form["description"]
    assert fields["prompt"]["type"] == "textarea"
    assert fields["time_str"]["default_value"] == ""


@pytest.mark.parametrize("mode", ["apply_if_allowed", "proposal"])
async def test_direct_form_and_api_cannot_submit_an_unfilled_shop_placeholder(mode):
    definition = {"name": "日报", "prompt": TEXT, "schedule_type": "daily", "time_str": "09:00",
                  "push_target": {"type": "web", "user_id": "u1"}}
    context = SimpleNamespace(operation="create", base_snapshot={},
                              policy_snapshot={"submission": {"mode": mode}})
    request = NormalizeRequest(context=context, proposed_snapshot=definition)
    adapter = ScheduledTaskChangeAdapter(_Db(), user_id="u1", org_id="o1")
    if mode == "apply_if_allowed":
        with pytest.raises(ScheduledTaskChangeError, match="店铺"):
            await adapter.normalize(request)
    else:
        assert (await adapter.normalize(request)).proposed_snapshot["prompt"] == TEXT


async def test_strict_parser_prompt_has_only_its_nested_contract():
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=RAW)) as call:
        await parse_task_request(TEXT)
    prompt = call.call_args.kwargs["system_prompt"]
    # Every example is valid for the reader's changes/evidence contract; old flat
    # examples caused the actual model response to discard all supplied fields.
    examples = [json.loads(line) for line in prompt.splitlines() if line.startswith('{')]
    assert examples and all({"changes", "evidence", "recipient"} <= set(example) for example in examples)
    assert all(set(example["changes"]) - {"name"} <= set(example["evidence"]) for example in examples)


@pytest.mark.parametrize("mode", ["model", "legacy_internal"])
async def test_request_text_is_call_scoped_and_legacy_api_keeps_its_arguments(mode, monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), "scheduled_task_direct_enabled", True)
    executor = ToolExecutor(IdentityDB(), "u1", "c1", "o1", tool_entrypoint=mode, task_id="task1")
    with patch.object(ChatTaskManager, "handle", AsyncMock(return_value={"type": "text", "text": "test"})) as handle:
        for index, text in enumerate(["A店日报发给我", "B店日报发给销售群"]):
            executor._parent_messages = [{"role": "user", "content": text}]
            await executor.execute("manage_scheduled_task", {"action": "create", "description": "API请求"}, call_id=f"call-{index}")
        executor._parent_messages = None
        await executor.execute("manage_scheduled_task", {"action": "create", "description": "无聊天上下文"}, call_id="call-2")
    descriptions = [call.args[1]["description"] for call in handle.await_args_list]
    assert descriptions == (["A店日报发给我", "B店日报发给销售群", "无聊天上下文"] if mode == "model"
                            else ["API请求", "API请求", "无聊天上下文"])


async def test_real_chat_tool_pipeline_stages_original_request_form_for_delivery_and_replay(setup, monkeypatch):
    from core.config import get_settings
    from services.handlers.chat.execution_engine import _consume_emit_payloads, _build_replay_context
    from services.handlers.chat.execution_sink import CollectingExecutionSink
    monkeypatch.setattr(get_settings(), "scheduled_task_direct_enabled", True)
    host = ChatHarness()
    messages = [{"role": "user", "content": TEXT}]
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=RAW)), \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)):
        results = await host._execute_tool_calls(
            [tc("manage_scheduled_task", {"action": "create", "description": "全部店铺的日报",
                                         "definition": {**RAW["changes"], "prompt": BUSINESS}, "recipient": "我"})],
            "task1", "c1", "m1", "u1", 1, messages=messages,
        )
    assert results[0][2] is False
    sink, blocks = CollectingExecutionSink(), []
    await _consume_emit_payloads(host, blocks, sink)
    form = blocks[0]
    assert form["type"] == "form"
    assert next(f for f in form["fields"] if f["name"] == "prompt")["default_value"] == BUSINESS
    assert any(f["type"] == "datetime-local" for f in form["fields"])
    assert sink.blocks == blocks
    assert _build_replay_context(messages, blocks, 0)["content_blocks"] == blocks
    assert host._terminal_form_pending is True and host._pending_form_block is None


@pytest.mark.parametrize("prompt", ["读取【A店】销售", "按实际店铺名分组销售", "A店的销售日报"])
def test_real_shop_names_and_grouping_are_not_template_placeholders(prompt):
    from services.scheduler.task_submission import unfilled_shop_placeholder
    assert not unfilled_shop_placeholder(prompt)


async def test_runtime_preserves_current_creation_across_time_clarification(monkeypatch):
    from core.config import get_settings
    monkeypatch.setattr(get_settings(), "scheduled_task_direct_enabled", True)
    executor = ToolExecutor(IdentityDB(), "u1", "c1", "o1", tool_entrypoint="model", task_id="task1")
    original = "创建定时任务，按平台统计昨天A店付款订单数"
    answer = "每天上午8点，发给我"
    executor._parent_messages = [
        {"role": "user", "content": original},
        {"role": "assistant", "content": "每天几点执行？"},
        {"role": "user", "content": answer},
    ]
    raw = {"changes": {"name": "A店付款统计", "prompt": "全部店铺", "schedule_type": "daily", "time_str": "08:00"},
           "evidence": {"prompt": "按平台统计昨天A店付款订单数", "schedule_type": "每天", "time_str": "上午8点"},
           "recipient": "我", "request_parts": [
               {"kind": "request", "text": "创建定时任务，"},
               {"kind": "execution", "text": "按平台统计昨天A店付款订单数"},
               {"kind": "schedule", "text": "每天上午8点，"},
               {"kind": "delivery", "text": "发给我"}]}
    with patch("services.scheduler.task_nl_parser._call_llm", AsyncMock(return_value=raw)) as parser, \
         patch("services.scheduler.chat_task_manager._load_push_targets", AsyncMock(return_value=TARGETS)), \
         patch.object(ChatTaskManager, "_begin_request", AsyncMock(return_value={"type": "text", "text": "ok"})) as submit:
        result = await executor.execute("manage_scheduled_task", {"action": "create", "description": "全部店铺订单",
                               "definition": {**raw["changes"], "prompt": "按平台统计昨天A店付款订单数"}, "recipient": "我"}, call_id="followup")
    parser.assert_not_awaited()
    submit.assert_not_awaited()
    fields = {f['name']: f.get('default_value') for f in result.form['fields']}
    assert fields['prompt'] == "按平台统计昨天A店付款订单数"
    assert fields['time_str'] == "08:00"
