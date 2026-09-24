"""Turn isolation, lazy pinned storage, bounded templates and strict replay."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.skills.contracts import SkillError, ValidatedSkill
from services.skills.assets import SkillResources
from services.skills.renderer import (
    MAX_ARGS_BYTES, MAX_BODY_BYTES, MAX_DIRECTORY_BYTES, MAX_RENDERED_BYTES,
    digest, render,
)
from services.skills.runtime import SkillReplayError, SkillRuntime, create_skill_runtime
from services.tools import ToolCall, ToolPolicy, build_legacy_catalog
from tests.test_skill_resolver import candidate
from tests.test_tool_execution import context, stack


def item(key="report", revision="v1", tools=("file_search",), **metadata):
    return candidate(skill_key=key, revision=revision, catalog_metadata={
        "model_selectable": True, "allowed_tool_names": tools, **metadata,
    })


class Source:
    def __init__(self, candidates=None, body="Read {{args.topic}}."):
        self.candidates = candidates if candidates is not None else [item()]
        self.body = body
        self.discover = AsyncMock(return_value=self.candidates)
        self.session_bindings = AsyncMock(return_value=[])
        self.load = AsyncMock(side_effect=self._load)

    async def _load(self, c, *, restoring=False):
        return ValidatedSkill("never-advertise/private/SKILL.md", c.skill_key, c.revision,
                              "1" * 64, digest(self.body), "summary", self.body, c.catalog_metadata,
                              SkillResources(template_variables={'topic': {'type': 'string', 'source': 'org_id'}})
                              if '{{args.topic}}' in self.body else SkillResources())


def state(source=None, **changes):
    return SkillRuntime(**(dict(
        turn_id="turn-1", source=source or Source(),
        platform_tool_names={"file_search", "file_delete", "web_search"},
        authorized_tool_names=None, cancellation_event=asyncio.Event(), template_context={"org_id": "orders"},
    ) | changes))


def activate(key="report", **args):
    return json.dumps({"skill_id": key, "args": args}, ensure_ascii=False)


async def test_body_is_loaded_only_for_explicit_available_activation():
    source = Source([item(), item("hidden", model_selectable=False)])
    runtime = state(source)
    await runtime.initialize()
    runtime.messages()
    runtime.checkpoint()
    source.load.assert_not_awaited()
    for key in ("missing", "hidden", "../report", "/server/secret"):
        assert (await runtime.activate(activate(key)))["code"] == "SKILL_NOT_AVAILABLE"
    source.load.assert_not_awaited()
    assert (await runtime.activate(activate()))["ok"]
    source.load.assert_awaited_once()
    text = json.dumps(runtime.messages(), ensure_ascii=False)
    assert "Read orders." in text and "never-advertise" not in text
    other_turn = state(source, turn_id="turn-2")
    await other_turn.initialize()
    assert not other_turn.has_active_skills


async def test_duplicate_activation_and_retry_after_replay_do_not_reinject_or_widen():
    source = Source()
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate()))["code"] == "SKILL_ACTIVATED"
    messages = []
    runtime.ensure_messages(messages)
    for _ in range(3):
        assert (await runtime.activate(activate()))["code"] == "SKILL_ALREADY_ACTIVE"
        runtime.ensure_messages(messages)
    assert len(messages) == 2 and source.load.await_count == 1
    assert (await runtime.activate(activate(topic="different")))["ok"] is False
    checkpoint = runtime.checkpoint()
    saved = checkpoint["active"][0]
    assert set(saved) == {"skill_key", "revision", "body_sha256", "rendered", "rendered_sha256",
                          "args_summary", "effective_allowed_tool_names", "asset_manifest_sha256", "loaded_asset_ids"}
    assert "orders" not in json.dumps(saved["args_summary"])
    restored = state(source)
    await restored.initialize(checkpoint)
    assert source.load.await_count == 2  # Revalidation is mandatory; rendering is not repeated.
    assert (await restored.activate(activate()))["code"] == "SKILL_ALREADY_ACTIVE"
    restored.ensure_messages(messages)
    assert source.load.await_count == 2 and len(messages) == 2
    assert restored.checkpoint() == checkpoint


async def test_multiple_skills_intersect_existing_authorization_and_never_expand():
    runtime = state(Source([
        item(tools=("file_search", "file_delete", "unknown", "*")),
        item("second", tools=("web_search", "file_delete")),
        item("empty", tools=()),
    ]), authorized_tool_names={"file_search", "web_search"})
    await runtime.initialize()
    await runtime.activate(activate())
    assert runtime.effective_allowed_tool_names == {"file_search"}
    await runtime.activate(activate("second"))
    assert runtime.effective_allowed_tool_names == set()
    await runtime.activate(activate("empty"))
    assert runtime.effective_allowed_tool_names == set()


async def test_skill_ceiling_is_enforced_by_dispatch_and_does_not_bypass_confirmation():
    runtime = state(Source([item(tools=("file_search", "file_delete"))]))
    await runtime.initialize()
    await runtime.activate(activate())
    ctx = context(authorized_tool_names=runtime.effective_allowed_tool_names)
    service, _, io = stack("web_search")
    result = await service.execute(ToolCall("call", "web_search", {}), ctx)
    assert result.decision.reason == "outside_authorized_scope"
    io.assert_not_awaited()
    policy = ToolPolicy(build_legacy_catalog())
    assert policy.decide("file_delete", ctx, {"file_ids": ["f1"]}).outcome == "require_confirmation"
    assert policy.decide("file_delete", replace(ctx, permission_mode="plan"), {}).outcome == "deny"


@pytest.mark.parametrize("body,args,code", [
    ("{{env.HOME}}", {}, "VARIABLE_FORBIDDEN"),
    ("{{args.topic.__class__}}", {}, "VARIABLE_FORBIDDEN"),
    ("{{server.path}}", {}, "VARIABLE_FORBIDDEN"),
    ("${API_TOKEN}", {}, "VARIABLE_FORBIDDEN"),
    ("{% include '/secret' %}", {}, "VARIABLE_FORBIDDEN"),
    ("{{args.topic}}", {}, "ARGS_MISMATCH"),
    ("none", {"path": "/server/secret"}, "ARGS_MISMATCH"),
    ("{{args.topic}}", {"topic": {"env": "TOKEN"}}, "ARGS_INVALID"),
    ("x" * (MAX_BODY_BYTES + 1), {}, "BODY_BUDGET_EXCEEDED"),
    ("{{args.topic}}", {"topic": "x" * MAX_ARGS_BYTES}, "ARGS_BUDGET_EXCEEDED"),
    ("{{args.topic}}" * 100, {"topic": "x" * 1000}, "RENDER_BUDGET_EXCEEDED"),
])
def test_controlled_templates_and_separate_budgets(body, args, code, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "must-never-appear")
    with pytest.raises(SkillError, match=code):
        render(body, args)


def test_substitutions_are_literals_and_do_not_expand_server_values(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "must-never-appear")
    assert render("{{args.topic}}", {"topic": "${API_TOKEN} {{env.HOME}}"}) == "${API_TOKEN} {{env.HOME}}"


async def test_directory_and_active_total_budgets():
    source = Source([item(f"skill{i}") for i in range(80)], body="x" * MAX_BODY_BYTES)
    runtime = state(source)
    await runtime.initialize()
    assert len(runtime.directory) <= 32
    assert len(runtime.messages()[0]["content"].encode()) <= MAX_DIRECTORY_BYTES
    for key in list(runtime.directory)[:3]:
        assert (await runtime.activate(activate(key)))["ok"]
    assert (await runtime.activate(activate(list(runtime.directory)[3])))["code"] == "SKILL_TURN_BUDGET_EXCEEDED"


async def test_cancel_before_and_during_load_never_activates():
    source = Source()
    runtime = state(source)
    await runtime.initialize()
    runtime.cancellation_event.set()
    with pytest.raises(asyncio.CancelledError):
        await runtime.activate(activate())
    source.load.assert_not_awaited()
    runtime.cancellation_event.clear()
    original = source._load

    async def cancel_during_read(c):
        runtime.cancellation_event.set()
        return await original(c)

    source.load.side_effect = cancel_during_read
    with pytest.raises(asyncio.CancelledError):
        await runtime.activate(activate())
    assert not runtime.active


async def test_restore_uses_pinned_revision_even_when_current_catalog_changes():
    source = Source()
    runtime = state(source)
    await runtime.initialize()
    await runtime.activate(activate())
    newer = Source([item(revision="v2")])
    restored = state(newer)
    await restored.initialize(runtime.checkpoint())
    newer.discover.assert_not_awaited()
    assert newer.load.await_args.args[0].revision == "v1"


@pytest.mark.parametrize("failure", ["missing", "hash", "render", "turn", "revision", "scope"])
async def test_unreplayable_checkpoint_stops_without_latest_fallback(failure):
    source = Source()
    runtime = state(source)
    await runtime.initialize()
    await runtime.activate(activate())
    checkpoint = runtime.checkpoint()
    if failure == "missing":
        source.load.side_effect = SkillError("SKILL_PINNED_REVISION_UNAVAILABLE")
    elif failure == "hash":
        source.body = "a different published body"
    elif failure == "render":
        checkpoint["active"][0]["rendered"] = "tampered"
    elif failure == "turn":
        checkpoint["turn_id"] = "another-turn"
    elif failure == "revision":
        checkpoint["active"][0]["revision"] = "v2"
    else:
        checkpoint["active"][0]["effective_allowed_tool_names"] = ["file_delete"]
    restored = state(source)
    with pytest.raises(SkillReplayError):
        await restored.initialize(checkpoint)
    assert not restored.active
    assert source.discover.await_count == 1


async def test_restore_does_not_expand_saved_ceiling_when_authority_grows():
    source = Source([item(tools=("file_search", "file_delete"))])
    runtime = state(source, authorized_tool_names={"file_search"})
    await runtime.initialize()
    await runtime.activate(activate())
    restored = state(source, authorized_tool_names={"file_search", "file_delete"})
    await restored.initialize(runtime.checkpoint())
    assert restored.effective_allowed_tool_names == {"file_search"}


async def test_repeated_restore_checkpoints_preserve_revoked_authorization():
    source = Source([item(tools=("file_search", "file_delete"))])
    original = state(source)
    await original.initialize()
    await original.activate(activate())
    restricted = state(source, authorized_tool_names={"file_search"})
    await restricted.initialize(original.checkpoint())
    resumed_again = state(source)
    await resumed_again.initialize(restricted.checkpoint())
    assert resumed_again.effective_allowed_tool_names == {"file_search"}


async def test_failed_activation_returns_secret_free_structured_reason():
    source = Source()
    runtime = state(source)
    await runtime.initialize()
    source.load.side_effect = OSError("/server/private/token=secret")
    assert await runtime.activate(activate()) == {"ok": False, "code": "SKILL_LOAD_UNAVAILABLE"}
    assert runtime.active == {}


async def test_model_args_are_rejected_and_missing_server_values_fail_closed():
    runtime = state(template_context={})
    await runtime.initialize()
    assert await runtime.activate(activate(topic="forged")) == {
        "ok": False, "code": "SKILL_TEMPLATE_ARGS_SERVER_ONLY",
    }
    assert await runtime.activate(activate()) == {
        "ok": False, "code": "SKILL_TEMPLATE_SERVER_VALUE_UNAVAILABLE",
    }
    assert not runtime.active
    runtime.template_context = {"org_id": "orders"}
    assert (await runtime.activate(activate()))["ok"]


@pytest.mark.parametrize("catalog,runtime_enabled", [(False, False), (True, False), (False, True)])
async def test_disabled_feature_never_constructs_source_or_loads_body(monkeypatch, catalog, runtime_enabled):
    from core.config import Settings
    assert Settings.model_fields["skill_runtime_enabled"].default is False
    monkeypatch.setattr("core.config.get_settings", lambda: SimpleNamespace(
        skill_catalog_enabled=catalog, skill_runtime_enabled=runtime_enabled,
    ))
    # Deliberately no db/context: these must not be inspected behind the gate.
    assert await create_skill_runtime(handler=object(), context=object(), runtime=object()) is None
    with pytest.raises(SkillReplayError, match="DISABLED"):
        await create_skill_runtime(handler=object(), context=object(), runtime=object(),
                                   replay_context={"skill_runtime": {"active": [{}]}})
