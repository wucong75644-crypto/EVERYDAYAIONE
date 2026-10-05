"""Actual shared execution and Actor loop, with isolated provider/business doubles.

No production DB, paid provider, transaction or RLS claims are made here.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import Settings
from services.agent.agent_result import AgentResult
from services.agent.execution_budget import ExecutionBudget
from services.handlers.chat.execution_engine import _run_loop, _build_replay_context
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.image_argument_correction import ImageArgumentCorrection, KEY, summarize_metrics
from services.handlers.chat.stream_session import StreamTotals
from services.handlers.chat_tool_mixin import ChatToolMixin
from services.handlers.chat_tool_result_mixin import ChatToolResultMixin
from services.handlers.permission_mode import PermissionMode
from services.handlers.tool_loop_context import ToolLoopContext
from services.tools import build_legacy_catalog, ToolCall
from services.tools.argument_validation import ToolArgumentValidationError, validate_model_image_arguments
from services.tools.result import ToolResult
from services.tools.runtime_context import catalog_context
from tests.test_chat_execution_engine import _request
from tests.test_skill_runtime import Source, item, state, activate
from tests.test_skill_runtime_actor import actor
from tests.test_tool_execution import context, stack
from tests.tool_runtime_support import MockHandlerExecutor


@pytest.fixture
def enabled(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql://invalid/test",
                        jwt_secret_key="isolated-test-key", chat_image_async_enabled=True)
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    return build_legacy_catalog().require("generate_image")


def call(args, id="bad"):
    return {"id": id, "name": "generate_image", "arguments": json.dumps(args, ensure_ascii=False)}


def rejected(spec, args):
    with pytest.raises(ToolArgumentValidationError) as failure:
        validate_model_image_arguments(spec, args)
    from services.tools import ToolPolicy
    ctx = context(permission_mode="auto", feature_flags={"chat_image_async_enabled": True})
    tc = ToolCall("bad", "generate_image", args)
    decision = ToolPolicy(build_legacy_catalog()).decide(tc.name, ctx, args)
    return ToolResult.from_exception(failure.value, call=tc, context=ctx, decision=decision, handler_started=False)


@pytest.mark.parametrize("args,path", [
    ({"prompt": "exact", "format": "PNG", "size": "1K"}, "$.format"),
    ({"mode": "wrong", "prompt": "exact"}, "$.mode"),
    ({"mode": "text_to_image", "prompt": 12}, "$.prompt"),
    ({"mode": "text_to_image", "prompt": "exact", "resolution": "8K"}, "$.resolution"),
    ({"mode": "image_to_image", "prompt": "exact", "references": [{"message_id": "m", "role": "subject"}]}, "$.references[0]"),
    ({"mode": "image_to_image", "prompt": "exact", "references": [{"asset_id": "a", "file_id": "f", "role": "subject"}]}, "$.references[0]"),
    ({"mode": "image_to_image", "prompt": "exact", "references": [{"asset_id": "a", "message_id": "m", "role": "subject"}]}, "$.references[0]"),
    ({"mode": "image_to_image", "prompt": "exact", "references": [{"message_id": "m", "content_index": True, "role": "subject"}]}, "$.references[0].content_index"),
    ({"mode": "text_to_image", "prompt": "exact", "source_prompt": {"task_id": "t", "sha256": "x"}}, "$.source_prompt.sha256"),
])
def test_complete_contract_rejects_shape_and_nested_errors(enabled, args, path):
    original = deepcopy(args)
    result = rejected(enabled, args)
    assert any(issue["path"] == path for issue in result.exception.issues)
    assert not result.execution.handler_started and result.execution.status == "not_started"
    assert not result.uncertainty_notice
    assert args == original


async def test_runtime_does_not_reserve_invocation_or_confirm_bad_arguments(enabled):
    exe = MockHandlerExecutor(agent_domain="general", permission_mode="ask", tool_entrypoint="model")
    confirmer = AsyncMock(return_value=True)
    exe.tool_confirmer = confirmer
    lifecycle = SimpleNamespace(replay=AsyncMock(return_value=None), begin=AsyncMock(), complete=AsyncMock())
    result = await exe.tool_runtime.execute("generate_image", {"prompt": "exact", "format": "PNG"},
                                            call_id="bad", lifecycle=lifecycle)
    assert isinstance(result.exception, ToolArgumentValidationError)
    exe.handler.assert_not_awaited()
    confirmer.assert_not_awaited()
    lifecycle.begin.assert_not_awaited()
    lifecycle.complete.assert_not_awaited()


async def test_shared_service_and_internal_compatibility(enabled):
    service, _, business = stack("generate_image")
    ctx = context(permission_mode="auto", feature_flags={"chat_image_async_enabled": True})
    invalid = ToolCall("bad", "generate_image", {"prompt": "exact", "format": "PNG"})
    result = await service.execute(invalid, ctx)
    assert isinstance(result.exception, ToolArgumentValidationError)
    business.assert_not_awaited()
    result = await service.execute(ToolCall("internal", "generate_image", {"prompt": "exact"}),
                                   replace(ctx, entrypoint="legacy_internal"))
    assert not result.is_failure
    business.assert_awaited_once()


async def test_replay_precedes_new_contract_and_permissions_still_deny(enabled):
    exe = MockHandlerExecutor(agent_domain="general", permission_mode="auto", tool_entrypoint="model")
    sentinel = object()
    lifecycle = SimpleNamespace(replay=AsyncMock(return_value=sentinel))
    assert await exe.tool_runtime.execute("generate_image", {"prompt": "historical"},
                                          call_id="old", lifecycle=lifecycle) is sentinel
    exe.allowed_tool_names = frozenset({"file_search"})
    result = await exe.tool_runtime.execute("generate_image", {"prompt": "historical"}, call_id="denied", lifecycle=lifecycle)
    assert result.status == "denied" and not isinstance(result.exception, ToolArgumentValidationError)
    assert lifecycle.replay.await_count == 1


def guard_for(spec, args):
    blocks = [{"type": "tool_step", "tool_name": "generate_image", "tool_call_id": "bad"}]
    guard = ImageArgumentCorrection(blocks)
    result = rejected(spec, args)
    guard.observe([(call(args), result, True, result.display["text"])])
    return guard, blocks


@pytest.mark.parametrize("change", [
    {"prompt": "rewritten"}, {"resolution": "2K"},
    {"references": [{"asset_id": "new", "role": "subject"}]},
])
def test_correction_cannot_change_meaning_or_add_analysis_images(enabled, change):
    guard, _ = guard_for(enabled, {"prompt": "  exact\n", "size": "1K", "format": "PNG"})
    assert guard.start_round(1)
    args = {"mode": "text_to_image", "prompt": "  exact\n", "resolution": "1K", "output_format": "png", **change}
    ready, refused = guard.filter_calls([call(args, "fix")])
    assert not ready and len(refused) == 1


def test_reference_order_and_role_are_protected(enabled):
    refs = [{"asset_id": "a", "role": "subject"}, {"asset_id": "b", "role": "composition"}]
    guard, _ = guard_for(enabled, {"prompt": "exact", "mode": "image_to_image", "references": refs, "format": "PNG"})
    guard.start_round(1)
    good = {"prompt": "exact", "mode": "image_to_image", "references": refs, "output_format": "png"}
    assert guard.filter_calls([call(good)])[0]
    assert guard.filter_calls([call({**good, "references": list(reversed(refs))})])[1]


def test_checkpoint_restores_one_allowance_and_siblings_cannot_be_resent(enabled):
    guard, blocks = guard_for(enabled, {"prompt": "exact", "format": "PNG"})
    checkpoint = _build_replay_context([], blocks, 0, next_model_round=1)
    restored = ImageArgumentCorrection(checkpoint["content_blocks"])
    assert restored.start_round(1)
    good = call({"mode": "text_to_image", "prompt": "exact", "output_format": "png"}, "fix")
    sibling = call({"mode": "text_to_image", "prompt": "already accepted"}, "sibling")
    ready, refused = restored.filter_calls([good, sibling, {**good, "id": "random-variant"}])
    assert ready == [good] and len(refused) == 2
    restored.observe([(good, AgentResult(summary="submitted", metadata={"accepted": True}), False, "submitted")])
    again = ImageArgumentCorrection(deepcopy(checkpoint["content_blocks"]))
    assert not again.start_round(2)
    assert again.filter_calls([good])[1]
    assert restored.summary()["correction_rounds"] == 1


async def run_actor(monkeypatch, enabled, corrected, *, auto_skill=False, replay_blocks=None, model_round=0,
                    checkpoint=None, first_raw=None):
    source = Source([item(tools=("generate_image",))], body="Prepare one sample and preserve the exact prompt.")
    runtime = actor(checkpoint or AsyncMock(return_value={"outcome": "saved"}))
    runtime.skill_runtime = state(source, platform_tool_names={"generate_image"})
    await runtime.skill_runtime.initialize()
    calls = []
    if auto_skill:
        calls.append({"id": "activate", "name": "activate_skill", "arguments": activate()})
    if replay_blocks is None:
        calls.append(call({"prompt": "  exact\n", "model_name": "invented", "size": "1K", "format": "PNG"}))
        if first_raw is not None:
            calls[-1]["arguments"] = first_raw
    calls.append(call(corrected, "fix"))
    received = []
    async def stream_chat(**kwargs):
        received.append(deepcopy(kwargs))
        next_call = calls.pop(0)
        yield SimpleNamespace(content=None, thinking_content=None, finish_reason="tool_calls", credits_consumed=None,
            prompt_tokens=10, completion_tokens=4,
            tool_calls=[SimpleNamespace(index=0, id=next_call["id"], name=next_call["name"], arguments_delta=next_call["arguments"])])
    prepared = SimpleNamespace(execution_context=catalog_context("o1", "auto"),
        adapter=SimpleNamespace(stream_chat=stream_chat, estimate_cost_unified=lambda **_: SimpleNamespace(estimated_credits=0.25)),
        core_tools=[enabled.to_schema()], permission=PermissionMode(mode="auto"),
        tool_context=ToolLoopContext(org_id="o1", agent_domain="general"), messages=[], stream_kwargs={},
        budget=ExecutionBudget(max_turns=8, max_wall_time=60))
    exe = MockHandlerExecutor(user_id="user-1", org_id="o1", conversation_id="conv-1", agent_domain="general", permission_mode="auto", tool_entrypoint="model")
    exe.handler.return_value = AgentResult(summary="图片任务已接受 task_id=child", metadata={"accepted": True, "task_id": "child"})
    monkeypatch.setattr("services.tool_executor.ToolExecutor", lambda **kwargs: exe)
    async def project(self, tc, result, ctx):
        return tc, result, result.is_failure, result.display["text"]
    monkeypatch.setattr(ChatToolResultMixin, "_process_tool_result", project)
    monkeypatch.setattr("services.handlers.chat.execution_engine.compact_tool_context", AsyncMock())
    class Handler(ChatToolMixin):
        org_id = "o1"
        db = exe.db
        _pending_emit_payloads = []
        def _get_conv_source(self, _): return "web"
        def _extract_user_image_urls(self, _): return []
    handler = Handler()
    blocks = deepcopy(replay_blocks) if replay_blocks is not None else []
    totals = StreamTotals()
    await _run_loop(handler=handler, request=_request(), prepared=prepared,
        cancellation_event=runtime.cancellation_event, sink=CollectingExecutionSink(), totals=totals,
        blocks=blocks, runtime=runtime, model_round=model_round)
    return exe, prepared, blocks, totals, received, source


async def test_actual_actor_automatically_activates_corrects_once_and_accepts_one(monkeypatch, enabled):
    good = {"prompt": "  exact\n", "mode": "text_to_image", "resolution": "1K", "output_format": "png"}
    exe, prepared, blocks, totals, requests, source = await run_actor(monkeypatch, enabled, good, auto_skill=True)
    assert len(requests) == 3  # activation, bad arguments, one correction
    source.load.assert_awaited_once()
    exe.handler.assert_awaited_once_with("generate_image", good)
    assert any(b.get("type") == "skill_step" and b.get("status") == "completed" for b in blocks)
    actual_schema = next(t for t in requests[-1]["tools"] if t["function"]["name"] == "generate_image")
    assert actual_schema == enabled.to_schema()
    feedback = json.dumps(requests[-1]["messages"], ensure_ascii=False)
    assert "output_format" in feedback and "not_accepted" in feedback
    metrics = totals.usage["image_argument_metrics"]
    assert metrics["invalid_initial_calls"] == metrics["corrected_calls"] == metrics["correction_rounds"] == 1
    assert metrics["prompt_tokens"] == 10 and metrics["completion_tokens"] == 4
    assert metrics["estimated_chat_credits"] == 0.25 and "child" in totals.text


@pytest.mark.parametrize("args", [
    {"prompt": "  exact\n", "mode": "text_to_image", "size": "1K", "format": "PNG"},
    {"prompt": "changed", "mode": "text_to_image", "output_format": "png"},
])
async def test_actual_actor_stops_second_error_without_accepting(monkeypatch, enabled, args):
    exe, _, _, totals, requests, _ = await run_actor(monkeypatch, enabled, args)
    assert len(requests) == 2
    exe.handler.assert_not_awaited()
    assert "自动纠正已停止" in totals.text
    assert '"parameters"' not in totals.text  # full schema stays in tool feedback, not the final user explanation
    assert totals.usage["image_argument_metrics"]["unresolved_calls"] == 1


async def test_actual_actor_resumes_pending_correction_without_new_allowance(monkeypatch, enabled):
    _, blocks = guard_for(enabled, {"prompt": "  exact\n", "size": "1K", "format": "PNG"})
    good = {"prompt": "  exact\n", "mode": "text_to_image", "resolution": "1K", "output_format": "png"}
    exe, _, final, totals, requests, _ = await run_actor(monkeypatch, enabled, good, replay_blocks=blocks, model_round=1)
    assert len(requests) == 1
    exe.handler.assert_awaited_once()
    assert totals.usage["image_argument_metrics"]["correction_rounds"] == 1
    assert ImageArgumentCorrection(final).repair["done"]


def test_metrics_are_bounded_facts_and_do_not_expose_inputs():
    summary = summarize_metrics([{"metrics": {
        "version": 1, "initial_calls": 2, "invalid_initial_calls": 1, "corrected_calls": 1,
        "unresolved_calls": 0, "correction_rounds": 1, "prompt_tokens": 100, "completion_tokens": 20,
        "estimated_chat_credits": 0.5, "prompt": "private", "references": ["private"],
    }}, {"metrics": None}])
    assert summary["initial_error_rate"] == 0.5 and summary["correction_success_rate"] == 1
    assert summary["estimated_chat_credits"] == 0.5 and "private" not in json.dumps(summary)


@pytest.mark.parametrize("raw", ["[1]", "{broken", "null", '"prompt"',
    '{"prompt":"p","resolution":NaN}', '{"prompt":"p","resolution":1e999}',
    '{"prompt":"p","prompt":"changed"}'])
async def test_actual_actor_unprotectable_json_stops_without_retry(monkeypatch, enabled, raw):
    exe, _, _, totals, requests, _ = await run_actor(monkeypatch, enabled,
        {"mode": "text_to_image", "prompt": "guessed"}, first_raw=raw)
    assert len(requests) == 1
    exe.handler.assert_not_awaited()
    assert "自动纠正已停止" in totals.text
    assert totals.usage["image_argument_metrics"]["invalid_initial_calls"] == 1
    assert totals.usage["image_argument_metrics"]["correction_rounds"] == 0


async def test_dispatch_checkpoint_failure_prevents_business_io(monkeypatch, enabled):
    _, blocks = guard_for(enabled, {"prompt": "  exact\n", "format": "PNG"})
    async def fail_before_io(point, payload):
        facts = [b.get(KEY, {}) for b in payload["content_blocks"]]
        if any(f.get("repair", {}).get("dispatch_reserved") for f in facts):
            raise RuntimeError("checkpoint unavailable")
        return {"outcome": "saved"}
    business = AsyncMock()
    # The entrypoint is real; the paid boundary remains a double.
    original = MockHandlerExecutor
    def executor(**kwargs):
        exe = original(**kwargs)
        exe.handler = business
        return exe
    monkeypatch.setattr(__name__ + ".MockHandlerExecutor", executor)
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await run_actor(monkeypatch, enabled, {"mode": "text_to_image", "prompt": "  exact\n", "output_format": "png"},
            replay_blocks=blocks, model_round=1, checkpoint=fail_before_io)
    business.assert_not_awaited()


async def test_accept_then_checkpoint_failure_recovers_without_model_or_resubmission(monkeypatch, enabled):
    from services.conversation_commands import SafePoint
    saved = []
    business_calls = []
    async def checkpoint(point, payload):
        repairs = [b.get(KEY, {}).get("repair", {}) for b in payload["content_blocks"]]
        if point == SafePoint.AFTER_TOOL and any(r.get("done") for r in repairs):
            raise RuntimeError("after acceptance crash")
        saved.append(deepcopy(payload))
        return {"outcome": "saved"}
    # Capture the real shared-runtime handler to count IO across the crash.
    original = MockHandlerExecutor
    def executor(**kwargs):
        exe = original(**kwargs)
        business_calls.append(exe.handler)
        return exe
    monkeypatch.setattr(__name__ + ".MockHandlerExecutor", executor)
    good = {"mode": "text_to_image", "prompt": "  exact\n", "resolution": "1K", "output_format": "png"}
    with pytest.raises(RuntimeError, match="after acceptance crash"):
        await run_actor(monkeypatch, enabled, good, checkpoint=checkpoint)
    assert business_calls[0].await_count == 1
    frozen = saved[-1]
    assert any(b.get(KEY, {}).get("repair", {}).get("dispatch_reserved") for b in frozen["content_blocks"])
    _, _, _, totals, requests, _ = await run_actor(monkeypatch, enabled, {**good, "variant_id": "new"},
        replay_blocks=frozen["content_blocks"], model_round=frozen["next_model_round"])
    assert not requests and business_calls[-1].await_count == 0
    assert "不会自动重新提交" in totals.text
    assert totals.usage["image_argument_metrics"]["correction_rounds"] == 1


async def test_completed_correction_checkpoint_delivers_original_ack_and_metrics(monkeypatch, enabled):
    good = {"mode": "text_to_image", "prompt": "  exact\n", "resolution": "1K", "output_format": "png"}
    _, _, blocks, _, _, _ = await run_actor(monkeypatch, enabled, good)
    exe, _, _, totals, requests, _ = await run_actor(monkeypatch, enabled, {**good, "prompt": "new"},
        replay_blocks=blocks, model_round=2)
    assert not requests
    exe.handler.assert_not_awaited()
    assert "child" in totals.text
    assert totals.usage["image_argument_metrics"]["corrected_calls"] == 1


def test_correction_preserves_uncertain_notice(enabled):
    guard, blocks = guard_for(enabled, {"prompt": "exact", "format": "PNG"})
    guard.start_round(1)
    good = call({"mode": "text_to_image", "prompt": "exact", "output_format": "png"}, "fix")
    blocks.append({"tool_call_id": "fix"})
    from services.tools.result import UncertainToolInvocationError
    ctx = context(permission_mode="auto", feature_flags={"chat_image_async_enabled": True})
    decision = MockHandlerExecutor().tool_runtime.policy.decide("generate_image", ctx, json.loads(good["arguments"]))
    result = ToolResult.from_exception(UncertainToolInvocationError("response lost"), call=ToolCall("fix", "generate_image", {}),
        context=ctx, decision=decision, handler_started=True)
    guard.observe([(good, result, True, result.display["text"])])
    assert "不要重复执行" in guard.stop_message
    assert guard.summary()["corrected_calls"] == 0


@pytest.mark.parametrize("outcome", ["disabled", "ignored"])
async def test_unconfirmed_checkpoint_prevents_correction_io(monkeypatch, enabled, outcome):
    _, blocks = guard_for(enabled, {"prompt": "  exact\n", "size": "1K", "format": "PNG"})
    dispatch = AsyncMock()
    monkeypatch.setattr(ChatToolMixin, "_execute_tool_calls", dispatch)
    async def checkpoint(point, payload):
        return {"outcome": outcome}
    with pytest.raises(RuntimeError, match="ACTOR_TOOL_DISPATCH_CHECKPOINT_NOT_SAVED"):
        await run_actor(monkeypatch, enabled, {"mode": "text_to_image", "prompt": "  exact\n", "resolution": "1K", "output_format": "png"},
            checkpoint=checkpoint, replay_blocks=blocks, model_round=1)
    dispatch.assert_not_awaited()


async def test_missing_checkpoint_callback_cannot_claim_dispatch_persisted():
    from services.conversation_commands import SafePoint
    with pytest.raises(RuntimeError, match="ACTOR_TOOL_DISPATCH_CHECKPOINT_UNAVAILABLE"):
        await actor().safe_point(SafePoint.BEFORE_TOOL, replay_payload={"content_blocks": []})


async def test_reserved_model_round_is_not_repeated_after_restart(monkeypatch, enabled):
    guard, blocks = guard_for(enabled, {"prompt": "  exact\n", "size": "1K", "format": "PNG"})
    assert guard.start_round(1)
    # BEFORE_MODEL already committed the allowance, but the process died
    # before a tool dispatch snapshot. Even the correction model is not retried.
    exe, _, _, totals, requests, _ = await run_actor(monkeypatch, enabled,
        {"mode": "text_to_image", "prompt": "  exact\n", "resolution": "1K", "output_format": "png"},
        replay_blocks=blocks, model_round=1)
    assert not requests
    exe.handler.assert_not_awaited()
    assert "纠错已尝试" in totals.text
    assert totals.usage["image_argument_metrics"]["estimated_chat_credits"] is None


@pytest.mark.parametrize("field,value", [("mask", "mask-ref"), ("weight", 0.8),
    ("image_urls", ["reference-url"]), ("num_images", 2), ("prompts", ["other"]), ("unknown_spec", "wide")])
def test_unknown_generation_intent_is_not_discarded_for_correction(enabled, field, value):
    guard, _ = guard_for(enabled, {"prompt": "exact", "mode": "text_to_image", field: value})
    assert guard.repair is None and guard.stop_message
    assert guard.summary()["invalid_initial_calls"] == 1


def test_invalid_cost_estimate_does_not_break_snapshot_or_allow_resubmission(enabled):
    guard, blocks = guard_for(enabled, {"prompt": "exact", "format": "PNG"})
    guard.start_round(1)
    guard.record_usage({}, {"prompt_tokens": 10, "completion_tokens": 4},
        SimpleNamespace(estimate_cost_unified=lambda **_: SimpleNamespace(estimated_credits=float("nan"))))
    summary = guard.summary()
    assert summary["estimated_chat_credits"] is None and summary["prompt_tokens"] == 10
    json.dumps(_build_replay_context([], blocks, 0), allow_nan=False)
    assert summarize_metrics([{"metrics": summary}])["cost_unknown_rounds"] == 1
