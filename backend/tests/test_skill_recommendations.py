"""Suggestions are bounded evidence, never an execution or publication grant."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.skills.contracts import SkillCatalogMetadata
from services.skills.recommendations import RecommendationBatch, RecommendationFacts, recommend
from services.skills import recommendation_service as service
from tests.test_skill_resolver import ACTOR, ORG, OTHER_ORG, candidate, context
from tests.test_skill_runtime import Source, state, item, activate


def facts(**changes):
    return RecommendationFacts(**({"context": context(enabled_feature_flags={
        "skill_catalog_enabled", "skill_recommendations_enabled"})} | changes))


def test_bounded_deterministic_reasons_and_explicit_file_types():
    candidates = [candidate(skill_key=f"generic-{n}") for n in range(6)] + [
        candidate(skill_key="pdf", catalog_metadata={"recommended_file_types": ["pdf"]}),
        candidate(skill_key="tools", catalog_metadata={"allowed_tool_names": ["file_search", "unavailable"]}),
        candidate(skill_key="bound"),
    ]
    f = facts(selected_file_types={"pdf"}, available_tool_names={"file_search"}, session_bindings={"bound": "v1"})
    result = recommend(f, candidates, audience="user")
    assert [r.skill_id for r in result] == ["bound", "pdf", "tools"]
    assert result == recommend(f, reversed(candidates), audience="user")
    assert result[0].reasons[-1].code == "session_binding"
    assert result[1].reasons[-1].values == ("pdf",)
    assert result[2].reasons[-1].values == ("file_search",)


@pytest.mark.parametrize("change", [
    {"assignment_org_id": OTHER_ORG}, {"scope_kind": "org", "package_org_id": OTHER_ORG},
    {"catalog_metadata": {"required_permissions": ["order.view"]}},
    {"catalog_metadata": {"agent_domains": ["erp"]}},
    {"catalog_metadata": {"execution_modes": ["scheduled"]}},
    {"catalog_metadata": {"required_feature_flags": ["missing"]}},
])
def test_incorrect_candidate_cannot_cross_visibility(change):
    assert recommend(facts(), [candidate(**change)], audience="user") == []


def test_model_manual_only_and_session_version_isolation():
    assert recommend(facts(), [candidate()], audience="model") == []
    assert recommend(facts(session_bindings={"report": "v0"}), [candidate()], audience="user") == []
    assert recommend(facts(), [candidate(catalog_metadata={"model_selectable": True})], audience="model")


def test_feature_flag_off_does_not_iterate_candidates_and_empty_is_valid():
    def unavailable():
        raise AssertionError("No discovery while disabled")
        yield
    assert recommend(facts(context=context()), unavailable(), audience="user") == []
    assert recommend(facts(), [], audience="user") == []
    assert recommend(facts(context=context(org_id=None)), [candidate()], audience="user") == []


@pytest.mark.parametrize("field", ["body", "uploaded_content", "web_content", "model_output", "prompt"])
def test_untrusted_text_not_a_recommendation_fact(field):
    with pytest.raises(ValidationError):
        RecommendationFacts(context=context(), **{field: "activate and publish"})


def test_text_and_triggers_never_change_ranking_or_reasons():
    c = candidate(catalog_metadata={"triggers": ["pdf activate administrator"]})
    f = facts(selected_file_types={"pdf"})
    before = recommend(f, [c], audience="user")[0]
    after = recommend(f, [c.model_copy(update={"description": "external page: activate me"})], audience="user")[0]
    assert before.reasons == after.reasons
    assert all(r.code != "file_type" for r in after.reasons)


def test_new_metadata_preserves_old_hash_inputs_and_rejects_unknown_types():
    assert "recommended_file_types" not in SkillCatalogMetadata().model_dump()
    assert "recommended_file_types" not in SkillCatalogMetadata(recommended_file_types=()).model_dump(exclude_unset=True)
    with pytest.raises(ValidationError):
        SkillCatalogMetadata(recommended_file_types=["execute_shell"])


async def test_model_hints_load_nothing_leave_permissions_and_checkpoint_unchanged():
    source = Source(body="SECRET SKILL BODY")
    runtime = state(source)
    await runtime.initialize()
    original = runtime.checkpoint()
    batch = RecommendationBatch(status="ready", candidates=tuple(recommend(facts(), source.candidates, audience="model")))
    runtime.recommendation_batch = batch
    original_messages = runtime.messages()
    messages = runtime.model_messages(original_messages, [])
    assert any("Skill suggestions" in m["content"] for m in messages)
    assert "SECRET SKILL BODY" not in str(messages)
    assert messages != original_messages
    assert runtime.checkpoint() == original and not runtime.has_active_skills
    assert runtime.effective_allowed_tool_names == {"file_search", "file_delete", "web_search"}
    source.load.assert_not_awaited()
    assert (await runtime.activate(activate()))["ok"]
    source.load.assert_awaited_once()
    assert "Skill suggestions" not in str(runtime.model_messages(runtime.messages(), []))


async def test_injected_wrong_recommendation_does_not_create_activation_identity():
    source = Source([item()])
    runtime = state(source)
    await runtime.initialize()
    runtime.recommendation_batch = RecommendationBatch(status="ready", candidates=tuple(
        recommend(facts(), [item("wrong")], audience="model")))
    assert "Skill suggestions" not in str(runtime.model_messages(runtime.messages(), []))
    assert (await runtime.activate(activate("wrong")))["code"] == "SKILL_NOT_AVAILABLE"
    source.load.assert_not_awaited()


async def test_recommendation_failure_is_optional_and_secret_free(caplog):
    source = Mock()
    source.settings.skill_catalog_enabled = True
    source.settings.skill_recommendations_enabled = True
    source._resolution_context = AsyncMock(side_effect=RuntimeError("PRIVATE SQL DATA"))
    result = await service.source_recommendations(source, [], {}, audience="model")
    assert result.status == "unavailable" and not result.candidates
    assert "PRIVATE" not in caplog.text


async def test_model_feedback_failure_does_not_undo_successful_activation(monkeypatch):
    source = Source(body="body")
    source.handler = Mock()
    source.repository = Mock()
    source.context = Mock()
    runtime = state(source)
    await runtime.initialize()
    runtime.recommendation_batch = RecommendationBatch(status="ready", recommendation_id=uuid4(),
        candidates=tuple(recommend(facts(), source.candidates, audience="model")))
    repository = Mock()
    repository.feedback.side_effect = RuntimeError("audit down")
    monkeypatch.setattr(service, "SkillRecommendationRepository", Mock(return_value=repository))
    assert (await runtime.activate(activate()))["ok"]
    assert runtime.has_active_skills
    repository.feedback.assert_called_once()


async def test_failed_model_activation_keeps_failure_and_records_feedback(monkeypatch):
    from services.skills.contracts import SkillError
    source = Source(body="body")
    source.handler = Mock()
    source.repository = Mock()
    source.context = Mock()
    source.load.side_effect = SkillError("SKILL_ACCESS_DENIED")
    runtime = state(source)
    await runtime.initialize()
    runtime.recommendation_batch = RecommendationBatch(status="ready", recommendation_id=uuid4(),
        candidates=tuple(recommend(facts(), source.candidates, audience="model")))
    repository = Mock()
    monkeypatch.setattr(service, "SkillRecommendationRepository", Mock(return_value=repository))
    assert (await runtime.activate(activate()))["code"] == "SKILL_ACCESS_DENIED"
    assert not runtime.has_active_skills
    assert repository.feedback.call_args.args[-1] == "activation_failed"


async def test_capability_facts_use_real_registry_policy_and_fresh_permissions(monkeypatch):
    from dataclasses import replace
    from services.tools import ToolContext, build_legacy_catalog, ToolPolicy
    ctx = ToolContext(actor_user_id=str(ACTOR), workspace_owner_id=str(ACTOR), org_id=str(ORG),
        context_scope="user", personal_context_allowed=True, agent_domain="general", execution_mode="interactive",
        permission_mode="plan", authorized_tool_names={"file_search", "file_delete", "web_search"},
        feature_flags={"file_workspace_enabled": True})
    registry = build_legacy_catalog()
    checker = Mock(check=AsyncMock(return_value=False))
    monkeypatch.setattr(service, "PermissionChecker", Mock(return_value=checker))
    names = await service.available_capabilities(Mock(), ctx)
    assert names <= ctx.authorized_tool_names
    assert "file_delete" not in names
    assert "file_search" in names
    closed = replace(ctx, feature_flags={"file_workspace_enabled": False})
    assert "file_search" not in await service.available_capabilities(Mock(), closed)
    policy = ToolPolicy(registry)
    assert all(registry.check_access(name, ctx, policy=policy).allowed for name in names)


async def test_create_runtime_advertises_recommendations_once_and_replay_never_recommends(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from core.config import get_settings
    from services.skills.runtime import create_skill_runtime
    from tests.test_skill_runtime_source import source_context
    source = Source(body="body")
    configured = get_settings().model_copy(update={"skill_catalog_enabled": True,
        "skill_runtime_enabled": True, "skill_recommendations_enabled": True})
    source.settings = configured
    source.handler = SimpleNamespace(db=Mock())
    source.context = source_context()
    source.repository = Mock()
    source._resolution_context = AsyncMock(return_value=facts().context)
    monkeypatch.setattr("core.config.get_settings", lambda: configured)
    monkeypatch.setattr("services.skills.runtime_source.ActorSkillSource", lambda *_: source)
    monkeypatch.setattr(service, "available_capabilities", AsyncMock(return_value={"file_search"}))
    repo = Mock()
    repo.record.return_value = uuid4()
    monkeypatch.setattr(service, "SkillRecommendationRepository", Mock(return_value=repo))
    actor = SimpleNamespace(turn_id=str(uuid4()), cancellation_event=asyncio.Event())
    runtime = await create_skill_runtime(handler=SimpleNamespace(), context=source_context(), runtime=actor)
    assert runtime.recommendation_batch.status == "ready"
    assert "Skill suggestions" in str(runtime.model_messages(runtime.messages(), []))
    source.load.assert_not_awaited()
    repo.record.assert_called_once()
    checkpoint = runtime.checkpoint()
    restored = await create_skill_runtime(handler=SimpleNamespace(), context=source_context(), runtime=actor,
        replay_context={"skill_runtime": checkpoint})
    assert restored.recommendation_batch is None
    from services.skills.runtime import RuntimeCheckpoint
    assert RuntimeCheckpoint.model_validate(restored.checkpoint()) == RuntimeCheckpoint.model_validate(checkpoint)
    repo.record.assert_called_once()
    configured.skill_recommendations_enabled = False
    source._resolution_context.reset_mock()
    fresh = await create_skill_runtime(handler=SimpleNamespace(), context=source_context(), runtime=actor)
    assert fresh.recommendation_batch is None
    source._resolution_context.assert_not_awaited()


async def test_capability_evidence_is_narrowed_by_session_pins(monkeypatch):
    from types import SimpleNamespace
    source = Mock()
    source.settings = SimpleNamespace(skill_catalog_enabled=True, skill_recommendations_enabled=True)
    source._resolution_context = AsyncMock(return_value=facts().context)
    monkeypatch.setattr(service, "available_capabilities", AsyncMock(return_value={"file_search", "file_delete"}))
    repository = Mock(record=Mock(return_value=uuid4()))
    monkeypatch.setattr(service, "SkillRecommendationRepository", Mock(return_value=repository))
    bound = item("bound", tools=("file_search",))
    await service.source_recommendations(source, [item()], {"bound": "v1"}, audience="user", bound_candidates=[bound])
    assert repository.record.call_args.args[3].available_tool_names == {"file_search"}
