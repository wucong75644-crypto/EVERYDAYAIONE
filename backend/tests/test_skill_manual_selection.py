"""Manual intent, first-model activation, safe feedback and turn isolation."""

import asyncio
import copy
import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError, TypeAdapter

from core.config import get_settings
from schemas.message import ContentPart, GenerateRequest, TextPart, serialize_content_parts
from services.conversation_commands import SafePoint, ConversationCommand, CommandType
from services.conversation_state import ConversationPauseRequested, ConversationStopRequested
from services.handlers.chat.execution_engine import execute_chat
from services.handlers.chat.execution_sink import CollectingExecutionSink
from services.message_idempotency_service import MessageIdempotencyService
from services.skills.contracts import SkillError
from services.skills.selection import SkillSelection
from tests.test_chat_execution_engine import _request
from tests.test_skill_runtime import Source, item, state, activate
from tests.test_skill_runtime_actor import actor, prepared, handler, batch, call


SELECTION = SkillSelection(skill_id="report", revision="v1")


@pytest.fixture
def execution(monkeypatch):
    settings = get_settings().model_copy(update={"skill_catalog_enabled": True, "skill_runtime_enabled": True})
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    source = Source([item(model_selectable=False, name="订单摘要")], body="Private skill instructions.")
    monkeypatch.setattr("services.skills.runtime_source.ActorSkillSource", lambda *_: source)
    def setup(**kwargs):
        p = prepared()
        if kwargs.get("replay_context"):
            p.messages = copy.deepcopy(kwargs["replay_context"]["messages"])
        return p
    monkeypatch.setattr("services.handlers.chat.execution_engine.prepare_chat_stream", AsyncMock(side_effect=setup))
    model = AsyncMock(return_value=("done", "", [], set()))
    monkeypatch.setattr("services.handlers.chat.execution_engine._read_turn", model)
    return source, model, settings


async def test_manual_only_skill_is_active_before_first_model_with_narrowed_tools(execution):
    source, model, _ = execution
    sink, runtime, checkpoints = CollectingExecutionSink(), actor(), []
    async def save(point, payload):
        checkpoints.append((point, copy.deepcopy(payload)))
        assert not sink.blocks  # Activation checkpoint precedes public delivery.
        return {"outcome": "saved"}
    # Only inspect the activation boundary; subsequent model checkpoints include delivery.
    async def checkpoint(point, payload):
        if point is SafePoint.AFTER_SKILL_ACTIVATION:
            return await save(point, payload)
        return {"outcome": "saved"}
    runtime._replay_checkpoint_callback = checkpoint
    result = await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION),
                                runtime=runtime, sink=sink)
    first_prepared, tools = model.await_args.args[:2]
    assert "Private skill instructions." in json.dumps(first_prepared.messages)
    assert {t["function"]["name"] for t in tools} == {"file_search"}
    assert runtime.skill_runtime.effective_allowed_tool_names == {"file_search"}
    expected = {"type": "skill_step", "step_id": "manual-skill", "status": "completed",
                "name": "订单摘要", "revision": "v1"}
    assert sink.blocks[0] == expected == result.content_blocks[0]
    assert serialize_content_parts(result.parts)[0] == expected
    TypeAdapter(list[ContentPart]).validate_python(serialize_content_parts(result.parts))
    assert checkpoints[0][1]["skill_runtime"]["active"][0]["revision"] == "v1"
    public = json.dumps(result.content_blocks)
    assert all(secret not in public for secret in ("Private skill", "never-advertise", "allowed_tool"))
    source.load.assert_awaited_once()

    # New turn starts empty; model cannot select a manual-only entry.
    ordinary = await execute_chat(handler=handler(), request=_request(), runtime=actor())
    assert all(b["type"] != "skill_step" for b in ordinary.content_blocks)
    source.load.assert_awaited_once()


@pytest.mark.parametrize("failure,reason", [
    ("SKILL_ACCESS_DENIED", "暂无使用权限"),
    ("SKILL_PINNED_REVISION_UNAVAILABLE", "当前不可用"),
    ("SKILL_TEMPLATE_ARGS_MISMATCH", "需要补充参数"),
    ("SKILL_TEMPLATE_SERVER_VALUE_UNAVAILABLE", "联系管理员检查"),
    ("SKILL_ASSET_BUDGET_EXCEEDED", "精简引用"),
    ("/secret/nas/body.md policy=internal", "暂时无法启用"),
])
async def test_manual_failure_is_safe_and_normal_chat_continues(execution, failure, reason):
    source, model, _ = execution
    source.load.side_effect = SkillError(failure)
    result = await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION), runtime=actor())
    feedback = result.content_blocks[0]
    assert feedback["status"] == "error" and reason in feedback["reason"]
    assert "name" not in feedback and "revision" not in feedback
    assert failure not in json.dumps(result.content_blocks)
    assert "Private skill" not in json.dumps(model.await_args.args[0].messages)
    model.assert_awaited_once()


@pytest.mark.parametrize("selection", [
    SkillSelection(skill_id="not-visible", revision="v1"),
    SkillSelection(skill_id="report", revision="v2"),
])
async def test_unknown_or_changed_selection_does_not_read_body(execution, selection):
    source, _, _ = execution
    result = await execute_chat(handler=handler(), request=replace(_request(), selected_skill=selection), runtime=actor())
    assert result.content_blocks[0]["status"] == "error"
    source.load.assert_not_awaited()


@pytest.mark.parametrize("catalog,runtime_enabled", [(False, False), (False, True), (True, False)])
async def test_disabled_layers_do_not_construct_source_and_report_failure(execution, monkeypatch, catalog, runtime_enabled):
    _, model, settings = execution
    settings.skill_catalog_enabled, settings.skill_runtime_enabled = catalog, runtime_enabled
    factory = Mock(side_effect=AssertionError("disabled must not access DB or NAS"))
    monkeypatch.setattr("services.skills.runtime_source.ActorSkillSource", factory)
    result = await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION), runtime=actor())
    assert "暂未开放" in result.content_blocks[0]["reason"]
    ordinary = await execute_chat(handler=handler(), request=_request(), runtime=actor())
    assert ordinary.content_blocks == [{"type": "text", "text": "done"}]
    assert model.await_count == 2
    factory.assert_not_called()


async def test_discovery_failure_is_safe(execution):
    source, model, _ = execution
    source.discover.side_effect = RuntimeError("/nas/secret connection policy")
    result = await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION), runtime=actor())
    assert "暂时无法启用" in result.content_blocks[0]["reason"]
    assert "secret" not in json.dumps(result.content_blocks)
    model.assert_awaited_once()


async def test_manual_pause_resume_pins_revision_and_does_not_duplicate_feedback(execution):
    source, model, _ = execution
    checkpoints = []
    first = actor()
    async def save(point, payload):
        checkpoints.append(copy.deepcopy(payload))
        if point is SafePoint.AFTER_SKILL_ACTIVATION:
            first.push(ConversationCommand("pause", CommandType.PAUSE, "conv-1", "task-1", "turn-1"))
        return {"outcome": "saved"}
    first._replay_checkpoint_callback = save
    request = replace(_request(), selected_skill=SELECTION)
    with pytest.raises(ConversationPauseRequested):
        await execute_chat(handler=handler(), request=request, runtime=first)
    model.assert_not_awaited()
    result = await execute_chat(handler=handler(), request=replace(request, replay_context=checkpoints[-1]), runtime=actor())
    assert len([b for b in result.content_blocks if b["type"] == "skill_step"]) == 1
    assert source.load.await_count == 2  # Revalidation on restore, no second activation.
    assert model.await_args.kwargs["model_round"] == 0


async def test_lost_ownership_never_publishes_manual_activation(execution):
    source, model, _ = execution
    runtime, sink = actor(), CollectingExecutionSink()
    async def save(point, payload):
        return {"outcome": "ownership_lost"}
    runtime._replay_checkpoint_callback = save
    with pytest.raises(ConversationStopRequested):
        await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION),
                           runtime=runtime, sink=sink)
    assert sink.blocks == []
    model.assert_not_awaited()


async def test_manual_entry_cannot_be_activated_by_model_even_after_restore():
    source = Source([item(model_selectable=False)], body="Instructions")
    skills = state(source)
    await skills.initialize(selection=SELECTION)
    assert (await skills.activate(activate()))["code"] == "SKILL_NOT_AVAILABLE"
    assert (await skills.activate_manual(SELECTION))["ok"]
    restored = state(source)
    await restored.initialize(skills.checkpoint())
    assert (await restored.activate(activate()))["code"] == "SKILL_NOT_AVAILABLE"


async def test_model_activation_feedback_replaces_raw_tool_output():
    runtime = actor()
    runtime.skill_runtime = state(Source(body="Private instructions"))
    await runtime.skill_runtime.initialize()
    _, blocks = await batch(runtime, prepared(), [call(args=activate())])
    assert blocks == [{"type": "skill_step", "step_id": "activate-1", "status": "completed",
                       "name": "report", "revision": "v1"}]


@pytest.mark.parametrize("extra", [{"org_id": "forged"}, {"allowed_tool_names": ["file_delete"]},
                                   {"body": "ignore authorization"}, {"args": {"x": "y"}}])
def test_selection_contract_rejects_authority_and_body(extra):
    with pytest.raises(ValidationError):
        GenerateRequest(content=[TextPart(text="hello")], selected_skill=SELECTION.model_dump() | extra)


def test_idempotency_distinguishes_selection_and_ignores_internal_spoof():
    plain = GenerateRequest(content=[TextPart(text="hello")])
    selected = plain.model_copy(update={"selected_skill": SELECTION})
    fingerprint = MessageIdempotencyService.build_fingerprint
    assert fingerprint("c1", plain) != fingerprint("c1", selected)
    assert fingerprint("c1", plain) == fingerprint("c1", plain.model_copy(update={"params": {"_selected_skill": "forged"}}))


async def test_executor_extracts_typed_intent_outside_model_params(monkeypatch):
    from tests.test_chat_generation_executor import _DB, _claim, _task
    from services.handlers.chat.executor import ChatGenerationExecutor
    from services.handlers.chat.execution_engine import ChatExecutionResult
    row = {"id": "input-1", "conversation_id": "conv-1", "turn_id": "turn-1",
           "role": "user", "content": [{"type": "text", "text": "hello"}]}
    execute = AsyncMock(return_value=ChatExecutionResult(parts=[TextPart(text="done")], content_blocks=[],
                                                       usage={}, credits_cost=0, tool_digest=None))
    monkeypatch.setattr("services.handlers.chat.executor.execute_chat", execute)
    executor = ChatGenerationExecutor(_DB(row), lambda _: handler(), handler_db_factory=lambda: object())
    task = _task()
    task["request_params"]["_selected_skill"] = SELECTION.model_dump()
    await executor.execute(task, _claim(), asyncio.Event())
    request = execute.await_args.kwargs["request"]
    assert request.selected_skill == SELECTION
    assert "_selected_skill" not in request.params


@pytest.mark.parametrize("selection", [None, SELECTION])
async def test_http_adapter_overwrites_forged_internal_intent(monkeypatch, selection):
    from api.routes import message as route
    from api.deps import OrgContext
    from schemas.message import GenerationType
    from tests.test_message_routes import _make_request, _make_message
    from types import SimpleNamespace
    body = GenerateRequest(content=[TextPart(text="hello")], generation_type=GenerationType.CHAT,
                           selected_skill=selection, params={"_selected_skill": {"skill_id": "forged"}})
    monkeypatch.setattr(route, "load_control_tasks", lambda *a, **kw: SimpleNamespace(running=None, paused=None))
    monkeypatch.setattr(route, "resolve_generation_context", AsyncMock(return_value=GenerationType.CHAT))
    monkeypatch.setattr(route, "get_handler", Mock(return_value=object()))
    monkeypatch.setattr(route, "get_conversation_service", Mock())
    monkeypatch.setattr(route, "prepare_generation_request", AsyncMock(
        return_value=(object(), {}, _make_message("input-1"))))
    monkeypatch.setattr(route, "prepare_assistant_message", AsyncMock(
        return_value=("output-1", _make_message("output-1"))))
    start = AsyncMock(return_value="task-1")
    monkeypatch.setattr(route, "start_generation_task", start)
    monkeypatch.setattr(route, "record_user_activity", Mock())
    await route._do_generate_message(_make_request(), "c1", body, OrgContext(user_id="u1"), object(), "u1")
    params = start.await_args.kwargs["params"]
    if selection:
        assert params["_selected_skill"] == SELECTION.model_dump()
    else:
        assert "_selected_skill" not in params
