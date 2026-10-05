"""Chat image routing: actual Registry/Runtime/Actor, isolated business doubles.

These checks do not call a paid provider or prove database billing transactions.
"""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import Settings
from services.agent.agent_result import AgentResult
from services.agent.execution_budget import ExecutionBudget
from services.handlers.chat.execution_engine import _apply_skill_context, _run_loop
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.handlers.chat.stream_session import StreamTotals
from services.handlers.chat.tool_loop import prepare_tool_turn
from services.handlers.chat_tool_mixin import ChatToolMixin
from services.handlers.chat_tool_result_mixin import ChatToolResultMixin
from services.handlers.permission_mode import PermissionMode
from services.handlers.tool_loop_context import ToolLoopContext
from services.prompt_builder.layers.static_layer import StaticLayer
from services.tools import ToolCall, build_legacy_catalog
from services.tools.runtime_context import catalog_context
from tests.test_chat_execution_engine import _request
from tests.test_skill_runtime import Source, activate, item, state
from tests.test_skill_runtime_actor import actor, prepared
from tests.test_tool_execution import context, stack
from tests.tool_runtime_support import MockHandlerExecutor


@pytest.fixture
def settings(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql://invalid/test",
                        jwt_secret_key="isolated-test-key", chat_image_async_enabled=True,
                        chat_image_allowed_user_ids="", mcp_connectors_enabled=False)
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    return settings


@pytest.mark.parametrize("mode,enabled,personal,allowed,expected", [
    ("auto", True, True, None, True),
    ("ask", True, True, None, True),
    ("plan", True, True, None, False),
    ("auto", False, True, None, False),
    ("auto", True, False, None, False),
    ("auto", True, True, {"generate_image", "image_agent"}, True),
    ("auto", True, True, {"image_agent"}, False),
    ("auto", True, True, set(), False),
])
def test_initial_and_discovered_images_use_one_gated_entry(settings, mode, enabled, personal, allowed, expected):
    settings.chat_image_async_enabled = enabled
    exe = MockHandlerExecutor(agent_domain="general", permission_mode=mode, tool_entrypoint="model",
        personal_context_allowed=personal, allowed_tool_names=allowed)
    for discovered in ((), ("image_agent", "generate_image")):
        names = {t["function"]["name"] for t in exe.tool_runtime.advertised(discovered_names=discovered)}
        assert "image_agent" not in names
        assert ("generate_image" in names) is expected


def test_cohort_gate_does_not_fall_back_to_old_tool(settings):
    settings.chat_image_allowed_user_ids = "someone-else"
    exe = MockHandlerExecutor(agent_domain="general", tool_entrypoint="model")
    names = {t["function"]["name"] for t in exe.tool_runtime.advertised(discovered_names=("image_agent", "generate_image"))}
    assert not names & {"image_agent", "generate_image"}


@pytest.mark.parametrize("execution_mode", ["interactive", "scheduled", "preflight"])
async def test_historical_model_call_is_denied_before_identity_billing_or_replay(settings, execution_mode):
    exe = MockHandlerExecutor(agent_domain="general", tool_entrypoint="model", execution_mode=execution_mode,
        allowed_tool_names={"image_agent"}, tool_confirmer=AsyncMock(return_value=True))
    lifecycle = SimpleNamespace(replay=AsyncMock(), begin=AsyncMock(), complete=AsyncMock())
    result = await exe.tool_runtime.execute("image_agent", {"task": "白底蓝色机器人"},
                                            call_id="historical", lifecycle=lifecycle)
    assert result.status == "denied" and result.decision.reason == "legacy_internal_only"
    assert result.execution.status == "not_started" and not result.execution.handler_started
    assert not result.uncertainty_notice
    exe.handler.assert_not_awaited()
    exe.tool_confirmer.assert_not_awaited()
    assert not exe.db.reads
    for method in (lifecycle.replay, lifecycle.begin, lifecycle.complete):
        method.assert_not_awaited()


async def test_shared_execution_rejects_model_but_keeps_trusted_internal_binding(settings):
    service, _, business = stack("image_agent")
    call = ToolCall("old", "image_agent", {"task": "native ecommerce fixture"})
    ctx = context(entrypoint="model", permission_mode="auto", authorized_tool_names={"image_agent"})
    result = await service.execute(call, ctx)
    assert result.status == "denied" and result.decision.reason == "legacy_internal_only"
    business.assert_not_awaited()
    result = await service.execute(call, replace(ctx, entrypoint="legacy_internal"))
    assert not result.is_failure
    business.assert_awaited_once_with(call.arguments)


async def test_skill_cannot_restore_retired_chat_tool(settings):
    skills = state(Source([item(tools=("image_agent", "generate_image"))]),
                   platform_tool_names={"image_agent", "generate_image"})
    await skills.initialize()
    assert (await skills.activate(activate()))["ok"]
    p = prepared()
    _apply_skill_context(p, skills)
    tools = prepare_tool_turn(core_tools=p.core_tools, discovered_names=p.tool_context.discovered_tools,
        org_id="org-1", turn=0, messages=p.messages, tool_context=p.tool_context,
        permission=p.permission, execution_context=p.execution_context)
    assert {t["function"]["name"] for t in tools} == {"generate_image"}


def test_both_system_prompt_paths_direct_chat_to_single_image_tool(settings):
    from config.chat_tools import get_tool_system_prompt
    for prompt in (get_tool_system_prompt(), StaticLayer.render()):
        assert "image_agent" not in prompt
        assert "generate_image" in prompt and "task_id" in prompt


async def test_actor_auto_activates_skill_then_accepts_two_single_calls(monkeypatch, settings):
    """Scripted model checks real injection/execution; not a model-quality E2E."""
    from config.chat_tools import get_core_tools
    source = Source([item(tools=("generate_image",))], body="Keep each approved prompt exact. Generate two independent images.")
    runtime = actor(AsyncMock(return_value={"outcome": "saved"}))
    runtime.skill_runtime = state(source, platform_tool_names={"generate_image"})
    await runtime.skill_runtime.initialize()
    assert not runtime.skill_runtime.has_active_skills
    prompts = ("  皇家蓝机器人站立挥手，纯白背景。\n", "  皇家蓝机器人坐着读书，封面无字。\n")
    inputs = [{"mode": "text_to_image", "prompt": prompt, "resolution": "1K",
               "aspect_ratio": "1:1", "output_format": "png"} for prompt in prompts]
    rounds = [
        [("activate", "activate_skill", activate())],
        [(f"image-{i}", "generate_image", json.dumps(args, ensure_ascii=False)) for i, args in enumerate(inputs)],
        [],
    ]
    received = []
    async def stream_chat(**kwargs):
        received.append(deepcopy(kwargs))
        calls = rounds.pop(0)
        yield SimpleNamespace(content=None if calls else "已接受两个独立图片任务。", thinking_content=None,
            finish_reason="tool_calls" if calls else "stop", credits_consumed=None, prompt_tokens=10, completion_tokens=4,
            tool_calls=[SimpleNamespace(index=i, id=id, name=name, arguments_delta=args)
                        for i, (id, name, args) in enumerate(calls)])
    p = SimpleNamespace(execution_context=catalog_context("o1", "auto"),
        adapter=SimpleNamespace(stream_chat=stream_chat), core_tools=get_core_tools("o1"),
        permission=PermissionMode(mode="auto"), tool_context=ToolLoopContext(org_id="o1", agent_domain="general"),
        messages=[], stream_kwargs={}, budget=ExecutionBudget(max_turns=8, max_wall_time=60))
    exe = MockHandlerExecutor(user_id="user-1", org_id="o1", conversation_id="conv-1",
        agent_domain="general", permission_mode="auto", tool_entrypoint="model")
    async def accept(name, args):
        i = inputs.index(args)
        return AgentResult(summary=f"submitted child-{i}", metadata={"accepted": True, "task_id": f"child-{i}"})
    exe.handler.side_effect = accept
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
    blocks, totals = [], StreamTotals()
    await _run_loop(handler=Handler(), request=_request(), prepared=p, cancellation_event=runtime.cancellation_event,
        sink=CollectingExecutionSink(), totals=totals, blocks=blocks, runtime=runtime)
    assert rounds == [[]] and len(received) == 2
    assert totals.text == ""  # Child placeholders replace the redundant synthesis round.
    source.load.assert_awaited_once()
    assert exe.handler.await_count == 2
    assert [call.args for call in exe.handler.await_args_list] == [("generate_image", args) for args in inputs]
    for request in received:
        assert "image_agent" not in {t["function"]["name"] for t in request["tools"]}
    injected = json.dumps(received[1]["messages"], ensure_ascii=False)
    assert source.body in injected
    assert any(b.get("type") == "skill_step" and b.get("status") == "completed" for b in blocks)
    assert "child-0" in json.dumps(blocks) and "child-1" in json.dumps(blocks)
    assert build_legacy_catalog().require("generate_image").core
