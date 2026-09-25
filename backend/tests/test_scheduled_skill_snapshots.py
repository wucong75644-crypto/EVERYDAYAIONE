"""Scheduled pins cannot inherit catalog, model selection, or later authority."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.skills.contracts import SkillError
from services.skills.runtime import SkillReplayError, create_skill_runtime
from services.skills.scheduled import ScheduledSkillPin, ScheduledSkillSnapshot, snapshot_ceiling
from tests.test_skill_runtime import Source, item, state, activate
from tests.test_tool_execution import context
from services.tools import ToolPolicy, build_legacy_catalog


def snapshot(source):
    return ScheduledSkillSnapshot(skills=tuple(ScheduledSkillPin(
        candidate=c, revision_id=uuid4(), content_sha256="1" * 64,
        body_sha256=__import__('hashlib').sha256(source.body.encode()).hexdigest(),
        allowed_tool_names={"file_search"},
    ) for c in source.candidates)).model_dump(mode="json")


async def test_scheduled_initialization_never_discovers_or_accepts_model_activation():
    source = Source([item(model_selectable=True, execution_modes=("scheduled",))], body="Locked method.")
    runtime = state(source, execution_mode="scheduled")
    await runtime.initialize_scheduled(snapshot(source))
    source.discover.assert_not_awaited()
    source.session_bindings.assert_not_awaited()
    assert runtime.active["report"].revision == "v1"
    assert runtime.effective_allowed_tool_names == {"file_search"}
    assert not runtime.allows_dynamic_activation
    assert (await runtime.activate(activate()))["code"] == "SKILL_SCHEDULED_ACTIVATION_FORBIDDEN"
    assert "Turn Skill catalog" not in str(runtime.messages())
    assert '"selection":"scheduled"' in str(runtime.messages())


async def test_actor_restore_uses_saved_pins_and_exact_render_without_catalog_or_binding_reads(monkeypatch):
    source = Source(body="Original {{args.topic}}.")
    runtime = state(source, execution_mode="scheduled", template_context={"org_id": "old-value"})
    pins = snapshot(source)
    await runtime.initialize_scheduled(pins)
    saved = runtime.checkpoint()
    source.candidates = [item(revision="v2")]
    recovered = state(source, execution_mode="scheduled", template_context={"org_id": "new-value"})
    await recovered.initialize_scheduled(pins, saved)
    assert recovered.active["report"].rendered == "Original old-value."
    assert recovered.checkpoint() == saved
    source.discover.assert_not_awaited()
    source.session_bindings.assert_not_awaited()
    reduced = state(source, execution_mode="scheduled", authorized_tool_names=[])
    await reduced.initialize_scheduled(pins, saved)
    assert reduced.effective_allowed_tool_names == set()
    with pytest.raises(SkillReplayError, match="CHECKPOINT_MISMATCH"):
        await state(source).initialize(saved)
    altered = deepcopy(pins)
    altered["skills"][0]["candidate"]["revision"] = "v2"
    with pytest.raises(SkillReplayError, match="CHECKPOINT_MISMATCH"):
        await state(source, execution_mode="scheduled").initialize_scheduled(altered, saved)


async def test_scheduled_disabled_feature_fails_closed_without_source_construction(monkeypatch):
    monkeypatch.setattr("core.config.get_settings", lambda: SimpleNamespace(skill_runtime_enabled=False, skill_catalog_enabled=True))
    ctx = context(execution_mode="scheduled", authorization_snapshot={"skill_revision_snapshot": snapshot(Source())})
    with pytest.raises(SkillReplayError, match="RUNTIME_DISABLED"):
        await create_skill_runtime(handler=object(), context=ctx, runtime=object())


async def test_actor_factory_restores_checkpoint_instead_of_new_task_configuration(monkeypatch):
    source = Source(body='original')
    pins = snapshot(source)
    original = state(source, execution_mode='scheduled')
    await original.initialize_scheduled(pins)
    saved = original.checkpoint()
    latest = snapshot(Source([item(revision='v2')]))
    monkeypatch.setattr('core.config.get_settings', lambda: SimpleNamespace(skill_runtime_enabled=True, skill_catalog_enabled=True))
    monkeypatch.setattr('services.skills.runtime_source.ActorSkillSource', lambda *args: source)
    constructed = []
    def build_source(handler, ctx, settings, snapshot):
        constructed.append(snapshot)
        return source
    monkeypatch.setattr('services.skills.scheduled.ScheduledSkillSource', build_source)
    host = SimpleNamespace(turn_id='turn-1', cancellation_event=__import__('asyncio').Event())
    ctx = context(execution_mode='scheduled', authorization_snapshot={'skill_revision_snapshot': latest})
    recovered = await create_skill_runtime(handler=object(), context=ctx, runtime=host,
        replay_context={'skill_runtime': saved})
    assert constructed == [pins]
    assert recovered.active['report'].revision == 'v1'
    source.discover.assert_not_awaited()


@pytest.mark.parametrize("mode", ["scheduled", "preflight"])
def test_dynamic_activation_and_dangerous_tools_never_dispatch_even_with_forged_allowlist(mode):
    registry = build_legacy_catalog()
    policy = ToolPolicy(registry)
    ctx = context(execution_mode=mode, authorized_tool_names={"activate_skill", "file_delete"},
                  authorization_snapshot={"version": 1, "allowed_tools": ["activate_skill", "file_delete"]})
    activation = policy.decide("activate_skill", ctx, {"skill_id": "report"})
    assert activation.outcome == "deny" and activation.reason == "SKILL_SCHEDULED_ACTIVATION_FORBIDDEN"
    assert policy.decide("file_delete", ctx, {"path": "test.txt"}).outcome == "deny"


async def test_create_edit_normalizer_rejects_client_snapshot_and_locks_server_result(monkeypatch):
    from services.changeset.contracts import ChangeSetContext, NormalizeRequest
    from services.scheduler.scheduled_task_change_adapter import ScheduledTaskChangeAdapter, ScheduledTaskChangeError
    adapter = ScheduledTaskChangeAdapter(object(), user_id="owner", org_id="org")
    base = dict(name="test", prompt="test", schedule_type="daily", time_str="09:00", push_target={"type": "web"})
    ctx = ChangeSetContext(id="change", org_id="org", resource_type="scheduled_task", resource_id="task",
        operation="create", base_revision="0", base_snapshot={}, proposed_snapshot=base, patch=(), diff={}, policy_snapshot={})
    pins = snapshot(Source())
    binder = AsyncMock(return_value=pins)
    monkeypatch.setattr("services.skills.scheduled.bind_scheduled_skills", binder)
    proposed = {**base, "skills": [{"skill_id": "report", "revision": "v1"}]}
    result = await adapter.normalize(NormalizeRequest(context=ctx, proposed_snapshot=proposed))
    assert result.proposed_snapshot["skill_revision_snapshot"] == pins
    assert "skills" not in result.proposed_snapshot
    assert binder.await_args.kwargs["owner"] == "owner"
    with pytest.raises(ScheduledTaskChangeError, match="服务端"):
        await adapter.normalize(NormalizeRequest(context=ctx, proposed_snapshot={**base, "skill_revision_snapshot": pins}))


def test_multiple_pins_and_new_platform_tools_cannot_widen_the_creation_ceiling():
    pins = snapshot(Source([item(), item("second")]))
    assert snapshot_ceiling(pins, {"file_search", "new_tool"}) == {"file_search"}
    assert snapshot_ceiling(pins, set()) == set()
    pins["skills"][1]["allowed_tool_names"] = []
    assert snapshot_ceiling(pins, {"file_search"}) == set()
