"""Mandatory session activation, per-Turn snapshots and no model persistence."""

import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.conversation_commands import CommandType, ConversationCommand, SafePoint
from services.conversation_state import ConversationPauseRequested
from services.handlers.chat.execution_engine import execute_chat
from services.skills.contracts import SkillError
from services.skills.runtime import SkillBindingError, SkillReplayError
from services.skills.runtime_source import ActorSkillSource
from tests.test_chat_execution_engine import _request
from tests.test_skill_manual_selection import execution, SELECTION  # noqa: F401
from tests.test_skill_runtime import Source, activate, item, state
from tests.test_skill_runtime_actor import actor, handler
from tests.test_skill_runtime_source import source_context


async def test_session_then_manual_then_model_only_intersect(execution):
    source, model, _ = execution
    source.session_bindings.return_value = [
        item("fixed", tools=("file_search", "web_search"), model_selectable=False),
        item("second", tools=("file_search", "file_delete"), tool_policy="restricted"),
    ]
    result = await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION), runtime=actor())
    assert [call.args[0].skill_key for call in source.load.await_args_list] == ["fixed", "second", "report"]
    assert [b["step_id"] for b in result.content_blocks if b["type"] == "skill_step"] == [
        "session-skill:fixed", "session-skill:second", "manual-skill",
    ]
    assert {t["function"]["name"] for t in model.await_args.args[1]} == {"file_search", "activate_skill"}
    skill_state = state(Source([item("model", tools=("web_search",))]))
    skill_state.source.session_bindings.return_value = source.session_bindings.return_value
    await skill_state.initialize()
    for key in skill_state.session_skill_ids:
        assert (await skill_state.activate_session(key))["ok"]
    assert (await skill_state.activate(activate("model")))["ok"]
    assert skill_state.effective_allowed_tool_names == set()


async def test_new_turn_uses_fixed_revision_and_never_changes_manual_or_model_scope(execution):
    source, model, _ = execution
    source.session_bindings.return_value = [item(revision="v1", model_selectable=False)]
    source.discover.return_value = [item(revision="v2")]
    first = actor()
    await execute_chat(handler=handler(), request=_request(), runtime=first)
    assert first.skill_runtime.active["report"].revision == "v1"
    result = await first.skill_runtime.activate_manual(SELECTION.model_copy(update={"revision": "v2"}))
    assert result["code"] == "SKILL_SESSION_REVISION_LOCKED"
    assert (await first.skill_runtime.activate(json.dumps({"skill_id": "report", "scope": "session"})))["ok"] is False
    assert (await first.skill_runtime.activate(activate()))["code"] == "SKILL_NOT_AVAILABLE"
    assert first.skill_runtime.checkpoint()["session_skill_ids"] == ["report"]
    # Removing a binding affects a later Turn only.
    source.session_bindings.return_value = []
    next_turn = actor()
    await execute_chat(handler=handler(), request=_request(), runtime=next_turn)
    assert not next_turn.skill_runtime.active
    assert first.skill_runtime.active["report"].revision == "v1"
    assert len(source.load.await_args_list) == 1


@pytest.mark.parametrize("failure", ["SKILL_ACCESS_DENIED", "SKILL_PINNED_REVISION_UNAVAILABLE",
                                    "SKILL_ASSET_BUDGET_EXCEEDED"])
async def test_binding_failure_never_falls_back_to_unrestricted_chat(execution, failure):
    source, model, _ = execution
    source.session_bindings.return_value = [item("fixed")]
    source.load.side_effect = SkillError(failure)
    with pytest.raises(SkillBindingError, match="会话固定"):
        await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION), runtime=actor())
    model.assert_not_awaited()


@pytest.mark.parametrize("stage", ["session_bindings", "discover"])
async def test_binding_discovery_failure_cannot_be_swallowed_by_manual_fallback(execution, stage):
    source, model, _ = execution
    source.session_bindings.return_value = [item("fixed")]
    getattr(source, stage).side_effect = RuntimeError("private/nas database")
    with pytest.raises(SkillBindingError, match="无法加载会话固定"):
        await execute_chat(handler=handler(), request=replace(_request(), selected_skill=SELECTION), runtime=actor())
    model.assert_not_awaited()


async def test_pause_mid_binding_list_resume_ignores_deleted_and_added_bindings(execution):
    source, model, _ = execution
    source.session_bindings.return_value = [item("first", model_selectable=False), item("second")]
    checkpoints, first = [], actor()
    async def save(point, payload):
        checkpoints.append(copy.deepcopy(payload))
        if point is SafePoint.AFTER_SKILL_ACTIVATION:
            first.push(ConversationCommand("pause", CommandType.PAUSE, "conv-1", "task-1", "turn-1"))
        return {"outcome": "saved"}
    first._replay_checkpoint_callback = save
    with pytest.raises(ConversationPauseRequested):
        await execute_chat(handler=handler(), request=_request(), runtime=first)
    model.assert_not_awaited()
    saved = checkpoints[-1]
    assert [a["skill_key"] for a in saved["skill_runtime"]["active"]] == ["first"]
    assert saved["skill_runtime"]["session_skill_ids"] == ["first", "second"]
    source.session_bindings.return_value = [item("new-binding", revision="v2")]
    source.discover.side_effect = AssertionError("resume must not query latest")
    resumed = actor()
    result = await execute_chat(handler=handler(), request=replace(_request(), replay_context=saved), runtime=resumed)
    assert list(resumed.skill_runtime.active) == ["first", "second"]
    assert [b["step_id"] for b in result.content_blocks if b["type"] == "skill_step"] == [
        "session-skill:first", "session-skill:second",
    ]
    source.session_bindings.assert_awaited_once()
    assert model.await_args.kwargs["model_round"] == 0


@pytest.mark.parametrize("bound", [False, True])
async def test_pause_before_first_activation_freezes_even_empty_session_configuration(execution, bound):
    source, model, _ = execution
    source.session_bindings.return_value = [item("fixed", revision="v1")] if bound else []
    checkpoints, first = [], actor()
    async def save(point, payload):
        checkpoints.append(copy.deepcopy(payload))
        first.push(ConversationCommand("pause", CommandType.PAUSE, "conv-1", "task-1", "turn-1"))
        return {"outcome": "saved"}
    first._replay_checkpoint_callback = save
    with pytest.raises(ConversationPauseRequested):
        await execute_chat(handler=handler(), request=_request(), runtime=first)
    source.load.assert_not_awaited()
    model.assert_not_awaited()
    source.session_bindings.return_value = [] if bound else [item("new-binding")]
    source.discover.return_value = [item("fixed", revision="v2")]
    resumed = actor()
    await execute_chat(handler=handler(), request=replace(_request(), replay_context=checkpoints[-1]), runtime=resumed)
    assert list(resumed.skill_runtime.active) == (["fixed"] if bound else [])
    if bound:
        assert resumed.skill_runtime.active["fixed"].revision == "v1"
    source.session_bindings.assert_awaited_once()


async def test_resume_rechecks_revocation_and_preserves_old_ceiling():
    source = Source(body="Fixed instructions")
    source.session_bindings.return_value = [item("fixed", tools=("file_search", "web_search"))]
    original = state(source, authorized_tool_names={"file_search"})
    await original.initialize()
    await original.activate_session("fixed")
    saved = original.checkpoint()
    source.session_bindings.side_effect = AssertionError("must not reread binding")
    restored = state(source, authorized_tool_names={"file_search", "web_search"})
    await restored.initialize(saved)
    assert restored.effective_allowed_tool_names == {"file_search"}
    source.load.side_effect = SkillError("SKILL_ACCESS_DENIED")
    with pytest.raises(SkillReplayError, match="ACCESS_DENIED"):
        await state(source).initialize(saved)
    assert saved == original.checkpoint()


async def test_old_checkpoint_does_not_pick_up_new_session_configuration():
    source = Source()
    original = state(source)
    await original.initialize()
    checkpoint = original.checkpoint()
    assert "session_skill_ids" not in checkpoint
    source.session_bindings.side_effect = AssertionError("old checkpoint is isolated")
    restored = state(source)
    await restored.initialize(checkpoint)
    assert restored.checkpoint() == checkpoint


async def test_legacy_actor_checkpoint_without_skill_state_never_reads_bindings(execution):
    source, model, _ = execution
    source.session_bindings.side_effect = AssertionError("legacy Turn cannot inherit bindings")
    legacy = {"messages": [], "content_blocks": [], "turn_index": 1, "next_model_round": 1}
    runtime = actor()
    await execute_chat(handler=handler(), request=replace(_request(), replay_context=legacy), runtime=runtime)
    assert not runtime.skill_runtime.session_skill_ids
    source.session_bindings.assert_not_awaited()
    model.assert_awaited_once()


async def test_disabled_runtime_cannot_resume_pending_session_bindings(execution):
    from services.skills.runtime import create_skill_runtime
    source, model, settings = execution
    source.session_bindings.return_value = [item("fixed")]
    skills = state(source)
    await skills.initialize()
    checkpoint = skills.checkpoint()
    assert not checkpoint["active"]
    settings.skill_runtime_enabled = False
    with pytest.raises(SkillReplayError, match="RUNTIME_DISABLED"):
        await create_skill_runtime(handler=handler(), context=object(), runtime=actor(),
                                   replay_context={"skill_runtime": checkpoint})
    model.assert_not_awaited()


@pytest.mark.parametrize("mode", ["scheduled", "preflight"])
async def test_scheduled_sources_never_read_session_bindings(mode):
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=object())),
                              replace(source_context(), execution_mode=mode), object())
    source._resolution_context = AsyncMock(side_effect=AssertionError("no session IO"))
    assert await source.session_bindings() == []
    source._resolution_context.assert_not_awaited()


async def test_actor_binding_reader_uses_only_trusted_conversation(monkeypatch):
    from services.skills.binding_repository import SkillBindingRepository
    repository = Mock()
    candidate = item()
    repository.bindings.return_value = [candidate.model_dump() | {"id": "binding"}]
    repository.candidate = SkillBindingRepository.candidate
    factory = Mock(return_value=repository)
    monkeypatch.setattr("services.skills.runtime_source.SkillBindingRepository", factory)
    context = source_context()
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=object())), context, object())
    source._resolution_context = AsyncMock()
    assert await source.session_bindings() == [candidate]
    repository.bindings.assert_called_once_with(context.conversation_id)
    assert factory.call_args.args[1].access_kind.value == "projection"
