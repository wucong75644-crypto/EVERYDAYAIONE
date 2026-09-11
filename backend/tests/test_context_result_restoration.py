"""Completed outcomes and per-call state: synthetic regression, no live model."""
import asyncio
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent.agent_result import AgentResult
from services.handlers.chat.tool_loop import apply_tool_results, prepare_tool_turn
from services.handlers.chat_context.history_loader import _row_to_oai_messages
from services.handlers.tool_loop_context import ToolLoopContext
from services.tools import ToolCall, ToolResult
from services.tools.policy import ToolDecision
from services.tools.runtime_context import catalog_context
from services.handlers.conversation_cache import get_closed_messages as real_cache_get, set_closed_messages as real_cache_set


def history(blocks, preserve=False):
    messages, _ = _row_to_oai_messages({"role": "assistant", "content": blocks}, 5,
                                      preserve_tool_protocol=preserve)
    return messages


@pytest.mark.parametrize("block,expected", [
    ({"type": "form", "form_id": "f1", "form_type": "scheduled_task_create", "title": "创建定时任务"}, "已提供表单"),
    ({"type": "file", "name": "汇总.csv", "workspace_path": "staging/汇总.csv"}, "staging/汇总.csv"),
    ({"type": "table", "title": "库存", "columns": ["SKU"], "rows": [{"SKU": "private-row"}], "truncated": True}, "已展示表格"),
    ({"type": "chart", "title": "销售走势", "option": {"private": "chart-data"}}, "销售走势"),
    ({"type": "diagram", "title": "订单流程", "source": "private-diagram-code"}, "订单流程"),
    ({"type": "image", "url": "https://example.test/a.png", "alt": "统计图"}, "a.png"),
    ({"type": "image", "failed": True, "url": None, "error": "provider failure", "retry_context": {"prompt": "private-prompt"}}, "生成失败"),
    ({"type": "video", "url": "https://example.test/a.mp4"}, "a.mp4"),
    ({"type": "audio", "url": "https://example.test/a.mp3"}, "a.mp3"),
    ({"type": "tool_result", "tool_name": "erp_agent", "text": "库存为 7"}, "库存为 7"),
])
@pytest.mark.parametrize("encoding", ["blocks", "json"])
def test_nontext_completed_outcome_survives_without_reexecuting(block, expected, encoding):
    source = [block, copy.deepcopy(block)]
    encoded = json.dumps(source, ensure_ascii=False) if encoding == "json" else source
    before = copy.deepcopy(encoded)
    messages = history(encoded)
    assert len(messages) == 1 and messages[0]["role"] == "assistant"
    assert messages[0]["content"].count(expected) == 1
    assert "private-" not in messages[0]["content"]
    assert not messages[0].get("tool_calls")
    assert encoded == before


def test_form_projection_does_not_invent_mutable_business_success():
    form = {"type": "form", "form_id": "f1", "form_type": "scheduled_task_create", "title": "创建任务"}
    snapshots = [history([{**form, "status": status, "result_message": "任务已生效",
                           "next_form": {"type": "form", "form_id": "later"}}])
                 for status in ("open", "submitted", "cancelled")]
    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert snapshots[0] and "已提供表单" in snapshots[0][0]["content"]
    assert "任务已生效" not in snapshots[0][0]["content"]
    assert "later" not in snapshots[0][0]["content"]


def test_text_and_distinct_same_name_files_keep_their_own_references():
    messages = history([{"type": "text", "text": "已完成。"},
                        {"type": "file", "name": "汇总.csv", "workspace_path": "a/汇总.csv"},
                        {"type": "file", "name": "汇总.csv", "workspace_path": "b/汇总.csv"}])
    text = messages[0]["content"]
    assert text.startswith("已完成。")
    assert "a/汇总.csv" in text and "b/汇总.csv" in text


def test_distinct_tables_are_not_collapsed_just_because_their_titles_match():
    tables = [{"type": "table", "title": "库存", "columns": ["SKU"], "rows": [{"SKU": sku}]}
              for sku in ("private-A", "private-B")]
    text = history(tables + [copy.deepcopy(tables[0])])[0]["content"]
    assert text.count("已展示表格") == 2
    assert "private-" not in text


def test_legacy_digest_without_text_has_an_independent_history_message():
    from services.handlers.chat_context.history_loader import _append_tool_digest
    messages = history([])
    _append_tool_digest(messages, {"role": "assistant", "content": [], "generation_params": {
        "tool_digest": {"tools": [{"name": "web_search", "ok": False, "hint": "检索关键词"}]}}})
    assert len(messages) == 1
    assert "web_search" in messages[0]["content"] and "✗" in messages[0]["content"]


def test_explicit_step_state_is_not_duplicated_or_overruled_by_old_digest():
    from services.handlers.chat_context.history_loader import _append_tool_digest
    blocks = [{"type": "tool_step", "tool_name": "file_analyze", "tool_call_id": "bad-call", "status": "error"}]
    row = {"role": "assistant", "content": blocks, "generation_params": {
        "tool_digest": {"tools": [{"name": "file_analyze", "ok": True}]}}}
    messages = history(blocks)
    _append_tool_digest(messages, row)
    assert messages[0]["content"].count("file_analyze") == 1
    assert "调用失败" in messages[0]["content"] and "✓" not in messages[0]["content"]


def test_tool_steps_keep_status_without_old_code_or_success_inference():
    steps = [{"type": "tool_step", "tool_name": "code_execute", "tool_call_id": f"c{i}",
              "status": state, "input": {"code": "private-old-export-code"},
              "output": "private-old-raw-output"}
             for i, state in enumerate(("completed", "error", "cancelled", "running"))]
    messages = history(steps)
    assert len(messages) == 1
    text = messages[0]["content"]
    assert all(f"c{i}" in text for i in range(3)) and "c3" not in text
    assert "调用已返回" in text and "调用失败" in text and "已取消" in text
    assert "private-" not in text and "成功" not in text
    recovered = history(steps[:1], preserve=True)
    assert [m["role"] for m in recovered] == ["assistant", "tool"]
    assert "private-old-export-code" in recovered[0]["tool_calls"][0]["function"]["arguments"]


def result(status="success", call_id="c1", filename="a.csv", effects=("none",)):
    call = {"id": call_id, "name": "file_analyze", "arguments": json.dumps({"path": filename})}
    typed_call = ToolCall(call_id, call["name"], {"path": filename})
    context = catalog_context("o1")
    decision = ToolDecision("allow", "", "safe", "read", True, True, effects, "none")
    if status in {"cancelled", "uncertain"}:
        error = asyncio.CancelledError() if status == "cancelled" else RuntimeError("unknown effect")
        unified = ToolResult.from_exception(error, call=typed_call, context=context, decision=decision,
                                            handler_started=True)
    else:
        raw = AgentResult("实际结果", status=status, error_message="原始失败",
                          tokens_used=17, thinking_text="必要 thinking",
                          metadata={"retry_context": {"recovery_action": "原恢复信息"}, "sample": "kept"})
        unified = ToolResult.wrap(raw, call=typed_call, context=context, decision=decision)
    return call, unified, unified.is_failure, unified.display["text"]


def apply(context, results, messages):
    apply_tool_results(tool_results=results, messages=messages, content_blocks=[],
                       start_times={}, tool_context=context)


def prepare(context, messages, turn):
    permission = SimpleNamespace(mode=SimpleNamespace(value="auto"), need_exit_attachment=False,
                                 get_reminder=lambda _: "")
    prepare_tool_turn(core_tools=[], discovered_names=set(), org_id="o1", turn=turn,
                      messages=messages, tool_context=context, permission=permission)


@pytest.mark.parametrize("status", ["success", "error", "timeout", "empty", "partial", "plan", "cancelled", "uncertain"])
def test_live_context_uses_actual_result_state_and_retains_original(status):
    ctx, messages = ToolLoopContext(), []
    item = result(status, effects=("external_write",) if status == "uncertain" else ("none",))
    original = item[1]
    if status == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            apply(ctx, [item], messages)
        assert messages == []
    else:
        apply(ctx, [item], messages)
    prompt = ctx.build_context_prompt()
    assert status in prompt
    assert "c1" in prompt
    assert ctx.recent_results[0][1] is original
    if status != "cancelled":
        assert messages[0]["content"] == original.model_content("chat")
    if status == "uncertain":
        assert "不要自动重试" in prompt and "success" not in prompt
    elif status == "cancelled":
        assert "success" not in prompt
    else:
        assert original.raw.tokens_used == 17 and original.raw.thinking_text == "必要 thinking"
        assert original.raw.metadata["sample"] == "kept"


@pytest.mark.parametrize("second_file", ["a.csv", "b.csv"])
def test_failure_then_success_updates_one_prompt_without_erasing_failure(second_file):
    ctx, messages = ToolLoopContext(), [{"role": "system", "content": "普通说明里提及失败工具，不得删除"}]
    failed = result("error", "failed-call")
    apply(ctx, [failed], messages)
    prepare(ctx, messages, 1)
    succeeded = result("success", "success-call", second_file)
    apply(ctx, [succeeded], messages)
    prepare(ctx, messages, 2)
    prompts = [m["content"] for m in messages if m["role"] == "system"]
    assert len(prompts) == 2  # One unrelated system message plus exactly one current summary.
    assert "success-call" in prompts[-1] and "success" in prompts[-1]
    assert "failed-call" not in prompts[-1] and "上轮失败工具" not in prompts[-1]
    assert "已恢复" not in prompts[-1]  # No recovery claim based on tool name alone.
    assert next(m for m in messages if m.get("tool_call_id") == "failed-call")["content"] == failed[1].model_content("chat")
    assert ctx.failed_tools == ["file_analyze"]


def test_parallel_batch_preserves_each_call_and_replaces_legacy_prompt():
    ctx = ToolLoopContext()
    messages = [{"role": "system", "content": "上轮失败工具: old_tool，考虑换其他工具或参数"}]
    items = [result("error", "a-call", "a.csv"), result("success", "b-call", "b.csv")]
    apply(ctx, items, messages)
    prepare(ctx, messages, 1)
    prompt = messages[-1]["content"]
    assert "a-call" in prompt and "error" in prompt and "b-call" in prompt and "success" in prompt
    assert len([m for m in messages if m["role"] == "system"]) == 1
    assert len(ctx.recent_results) == 2


def test_budget_dedup_does_not_delete_unrelated_rules_or_duplicate_current_context():
    from services.handlers.context_compressor.tokens import deduplicate_system_prompts
    rule = {"role": "system", "content": "业务规则：必须保留失败工具的原始记录"}
    ctx = ToolLoopContext()
    apply(ctx, [result("error")], [])
    messages = [rule, {"role": "system", "content": ctx.build_context_prompt()},
                {"role": "system", "content": ctx.build_context_prompt()}]
    deduplicate_system_prompts(messages)
    assert messages[0] is rule and len(messages) == 2


@pytest.mark.asyncio
async def test_projection_cache_isolated_from_old_empty_history(monkeypatch):
    from services.handlers import conversation_cache as cache
    monkeypatch.setattr(cache, "get_closed_messages", real_cache_get)
    monkeypatch.setattr(cache, "set_closed_messages", real_cache_set)
    old_key = "conv:msgs:o1:c1"
    storage = {old_key: json.dumps({"schema_version": 2, "revision": 2,
                                  "through_message_id": "m2", "closed_messages": []})}
    redis = AsyncMock()
    redis.get.side_effect = lambda key: storage.get(key)
    monkeypatch.setattr(cache, "get_redis", AsyncMock(return_value=redis))
    assert await cache.get_closed_messages("c1", 2, "m2", "o1") is None
    projected = history([{"type": "form", "form_id": "f1"}])
    assert await cache.set_closed_messages("c1", 2, "m2", projected, "o1")
    key, _, raw = redis.setex.await_args.args
    assert key != old_key
    assert json.loads(raw)["schema_version"] == 2
    assert json.loads(raw)["closed_messages"] == projected


@pytest.mark.parametrize("method", ["total", "history"])
def test_budget_compression_keeps_delivery_facts_when_body_is_archived(method, monkeypatch):
    from services.handlers.context_compressor.budget import enforce_budget, enforce_history_budget_sync
    old = history([{"type": "text", "text": "旧说明" * 3000},
                   {"type": "file", "name": "汇总.csv", "workspace_path": "staging/汇总.csv"}])[0]
    messages = [{"role": "user", "content": "统计旧文件"}, old]
    messages += [{"role": role, "content": "最近问答"} for role in ("user", "assistant") for _ in range(3)]
    monkeypatch.setattr("services.handlers.message_scorer.score_messages_sync", lambda rows: [1] * len(rows))
    if method == "total":
        enforce_budget(messages, 250)
    else:
        enforce_history_budget_sync(messages, 250)
    assert len(old["content"]) < 1000
    assert "staging/汇总.csv" in old["content"] and "已提供文件" in old["content"]
    assert not old.get("tool_calls")


@pytest.mark.asyncio
async def test_loop_summary_keeps_historical_outcomes_and_latest_tool_pairs(monkeypatch):
    from services.handlers.context_compressor.summary import compact_loop_with_summary
    old = history([{"type": "form", "form_id": "form-before", "title": "创建定时任务"}])[0]
    messages = [{"role": "user", "content": "旧问题"}, old, {"role": "user", "content": "读取新文件"}]
    for i in range(3):
        messages.extend([{"role": "assistant", "content": None, "tool_calls": [{
            "id": f"call-{i}", "type": "function", "function": {"name": "file_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"call-{i}", "content": "读取结果" * 600}])
    monkeypatch.setattr("services.handlers.session_memory.format_session_memory", lambda: "已读取数据")
    assert await compact_loop_with_summary(messages, 100, 0.1)
    assert any(m["role"] == "assistant" and "form-before" in str(m["content"]) for m in messages)
    for i in (1, 2):
        assert any(m.get("tool_call_id") == f"call-{i}" for m in messages)
        assert any(any(c["id"] == f"call-{i}" for c in m.get("tool_calls", [])) for m in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("latest_text", ["读取文件", "按刚才的方式生成汇总表和图"])
async def test_history_cache_snapshot_provider_and_checkpoint_preserve_outcomes(monkeypatch, latest_text):
    import httpx
    from services.handlers import conversation_cache as cache
    monkeypatch.setattr(cache, "get_closed_messages", real_cache_get)
    monkeypatch.setattr(cache, "set_closed_messages", real_cache_set)
    from services.handlers.context_snapshot import build_context_snapshot, ContextAnchor
    from services.handlers.chat.execution_engine import _build_replay_context
    from services.prompt_builder.builder import PromptBuilder
    from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
    from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
    from tests.test_context_snapshot import _query
    storage = {}
    redis = AsyncMock()
    redis.get.side_effect = lambda key: storage.get(key)
    redis.setex.side_effect = lambda key, ttl, value: storage.update({key: value})
    monkeypatch.setattr(cache, "get_redis", AsyncMock(return_value=redis))
    input_row = {"id": "input1", "role": "user", "conversation_id": "c1", "turn_id": "t1",
                 "content": [{"type": "text", "text": latest_text},
                             {"type": "file", "name": "新表.csv", "workspace_path": "new.csv"}]}
    anchor = ContextAnchor("task1", "c1", "t1", "input1", 4, "a4", "o1")
    def closed(role, content, revision):
        return {"role": role, "content": content, "status": "completed", "generation_params": {},
                "context_revision": revision, "message_kind": "conversation"}
    history_query = _query([
        closed("assistant", [{"type": "form", "form_id": "old-form", "title": "创建定时任务"}], 4),
        closed("user", "创建定时任务", 3),
        closed("assistant", [{"type": "file", "name": "旧汇总.csv", "workspace_path": "staging/old.csv"}], 2),
        closed("user", "统计旧文件并生成汇总表", 1),
    ])
    db = MagicMock()
    db.table.side_effect = [_query(input_row), history_query, _query({}), _query([])]
    first = await build_context_snapshot(db, anchor, latest_text)
    history_query.lte.assert_called_once_with("context_revision", 4)
    db.table.side_effect = [_query(input_row), _query({}), _query([])]
    second = await build_context_snapshot(db, anchor, latest_text)
    assert first.history_messages == second.history_messages
    assert len(first.history_messages) == 4
    assert "old-form" in first.history_messages[-1]["content"]
    assert first.resource_manifest.allowed_paths == frozenset({"new.csv"})
    user = UserLayer.render(UserMessageInput(text=latest_text))
    messages = PromptBuilder._compose_messages("规则", "会话", "时间", second.history_messages, None, user)
    captured = []
    def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"离线占位回复"}}]}\n\ndata: [DONE]\n\n')
    adapter = DashScopeChatAdapter(api_key="offline-test", model="qwen3.5-plus")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="https://offline.test") as client:
        adapter._client = client
        _ = [chunk async for chunk in adapter.stream_chat(messages)]
    sent = captured[0]["messages"]
    assert sent == messages and sent[-1]["content"] == latest_text
    assert any("old-form" in str(m["content"]) for m in sent)
    assert _build_replay_context(messages, [], 0)["messages"] == sent
