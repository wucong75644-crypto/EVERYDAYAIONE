from __future__ import annotations

from dataclasses import replace

import pytest

from services.agent.runtime.agents import AgentDefinition
from services.agent.runtime.catalog import (
    EffectiveToolset, build_default_runtime_catalog, build_runtime_version_registry,
    RuntimeToolCatalog, restore_frozen_toolset,
)
from services.agent.runtime.catalog.types import RuntimeToolDefinition
from services.agent.runtime.context import build_runtime_context


def _definition() -> AgentDefinition:
    return AgentDefinition(
        canonical_key="everydayai-default", revision="v1",
        prompt_revision="agent-runtime-production-v1",
        requested_tool_groups=frozenset({"code"}),
    )


def _toolset() -> EffectiveToolset:
    catalog = build_default_runtime_catalog()
    return EffectiveToolset.build(
        agent=_definition(), catalog=catalog, scope="user", channel="web",
        entitled_groups=frozenset({"code"}),
        authorized_names=frozenset({"code_execute"}),
    )


def test_definition_and_catalog_hashes_are_deterministic() -> None:
    assert _definition().definition_hash == _definition().definition_hash
    left = build_default_runtime_catalog()
    right = build_default_runtime_catalog()
    assert left.revision == right.revision
    assert _toolset().toolset_hash == _toolset().toolset_hash


def test_catalog_hash_covers_all_execution_security_facts() -> None:
    base = next(iter(build_default_runtime_catalog().definitions()))
    changes = (
        {"canonical_name": "renamed"}, {"tool_group": "other"},
        {"schema": {"type": "object", "additionalProperties": False}},
        {"safety_level": "safe"}, {"executor_type": "other"},
        {"executor_revision": 2}, {"capability_requirements": frozenset()},
        {"allowed_scope_kinds": frozenset({"channel"})},
        {"allowed_channels": frozenset({"web"})}, {"side_effect": "none"},
        {"authorization_requirement": "none"},
        {"retry_semantics": "never"}, {"reconcile_semantics": "none"},
        {"cancel_semantics": "never"}, {"result_schema_revision": 2},
    )
    catalogs = {RuntimeToolCatalog([base]).revision}
    for change in changes:
        if "schema" in change:
            change = {**change, "schema_hash": ""}
        changed = replace(base, **change)
        catalogs.add(RuntimeToolCatalog([changed]).revision)
    assert len(catalogs) == len(changes) + 1


def test_frozen_toolset_restores_sql_fact_documents() -> None:
    versions = build_runtime_version_registry()
    definition, catalog = versions.resolve_for_agent("everydayai-default", "v1")
    toolset = EffectiveToolset.build(
        agent=definition, catalog=catalog, scope="user", channel="web",
        entitled_groups=frozenset({"code"}),
        authorized_names=frozenset({"code_execute"}),
    )
    restored = restore_frozen_toolset(
        {"canonical_key": definition.canonical_key, "revision": definition.revision,
         "prompt_revision": definition.prompt_revision,
         "requested_tool_groups": sorted(definition.requested_tool_groups),
         "model_policy": dict(definition.model_policy),
         "context_policy": dict(definition.context_policy),
         "channel_restrictions": sorted(definition.channel_restrictions),
         "system_prompt": definition.system_prompt,
         "definition_hash": definition.definition_hash},
        {"tools": [{
            "canonical_name": item.canonical_name, "tool_group": item.tool_group,
            "schema": item.schema, "safety_level": item.safety_level,
            "executor_type": item.executor_type,
            "executor_revision": item.executor_revision,
            "capability_requirements": sorted(item.capability_requirements),
            "side_effect": item.side_effect,
            "authorization_requirement": item.authorization_requirement,
            "retry_semantics": item.retry_semantics,
            "reconcile_semantics": item.reconcile_semantics,
            "cancel_semantics": item.cancel_semantics,
            "result_schema_revision": item.result_schema_revision,
            "allowed_scope_kinds": sorted(item.allowed_scope_kinds),
            "allowed_channels": sorted(item.allowed_channels),
            "schema_hash": item.schema_hash,
        } for item in catalog.definitions()]},
        {"scope_kind": "user", "channel": "web",
         "entitled_groups": ["code"], "tool_names": ["code_execute"]},
        catalog_revision=catalog.revision,
    )
    assert restored.toolset_hash == toolset.toolset_hash


def test_frozen_toolset_restores_legacy_sql_fact_documents() -> None:
    definition = _definition()
    tool = next(iter(build_default_runtime_catalog().definitions()))
    restored = restore_frozen_toolset(
        {
            "canonical_key": definition.canonical_key,
            "revision": definition.revision,
            "prompt_revision": definition.prompt_revision,
            "requested_tool_groups": sorted(definition.requested_tool_groups),
            "model_policy": dict(definition.model_policy),
            "context_policy": dict(definition.context_policy),
            "channel_restrictions": sorted(definition.channel_restrictions),
            "system_prompt": definition.system_prompt,
            "definition_hash": definition.definition_hash,
        },
        {"tools": [{
            "canonical_name": tool.canonical_name,
            "tool_group": tool.tool_group,
            "schema": tool.schema,
            "safety_level": tool.safety_level,
            "executor_type": tool.executor_type,
            "executor_revision": tool.executor_revision,
            "capability_requirements": sorted(tool.capability_requirements),
            "side_effect": tool.side_effect,
            "authorization_requirement": tool.authorization_requirement,
            "retry_semantics": tool.retry_semantics,
            "reconcile_semantics": tool.reconcile_semantics,
            "cancel_semantics": tool.cancel_semantics,
            "result_schema_revision": tool.result_schema_revision,
            "allowed_scope_kinds": sorted(tool.allowed_scope_kinds),
            "allowed_channels": sorted(tool.allowed_channels),
            "schema_hash": tool.schema_hash,
        }]},
        {
            "scope_kind": "user", "channel": "web",
            "entitled_groups": [], "tool_names": [],
        },
        catalog_revision="legacy-catalog-revision",
        effective_toolset_hash="a" * 64,
    )
    assert restored.catalog_revision == "legacy-catalog-revision"
    assert restored.toolset_hash == "a" * 64
    assert restored.definitions == ()


def test_definition_fact_drives_prompt_revision_and_system_message() -> None:
    from services.agent.runtime.production_model import _messages

    versions = build_runtime_version_registry()
    v1, _ = versions.resolve_for_agent("everydayai-default", "v1")
    v2, _ = versions.resolve_for_agent("everydayai-default", "v2")
    first = _messages([{"role": "user", "content": "hello"}], v1.system_prompt)
    second = _messages([{"role": "user", "content": "hello"}], v2.system_prompt)
    assert first[0] == {"role": "system", "content": v1.system_prompt}
    assert second[0] == {"role": "system", "content": v2.system_prompt}
    assert v1.prompt_revision != v2.prompt_revision
    assert first[0]["content"] != second[0]["content"]


def test_runtime_catalog_does_not_load_application_settings_for_code_tool(
    monkeypatch,
) -> None:
    import core.config as core_config

    def fail_if_loaded():
        raise PermissionError("application .env must stay inaccessible")

    monkeypatch.setenv("AGENT_RUNTIME_PROCESS_ROLE", "agent_runtime")
    monkeypatch.setattr(core_config, "get_settings", fail_if_loaded)
    versions = build_runtime_version_registry()
    definition, catalog = versions.resolve_for_agent(
        "everydayai-default", "v1",
    )

    assert definition.revision == "v1"
    assert catalog.resolve("code_execute").description


def test_effective_toolset_is_fail_closed_and_executor_backed() -> None:
    toolset = _toolset()
    assert [tool.canonical_name for tool in toolset.definitions] == ["code_execute"]
    with pytest.raises(ValueError, match="NOT_OFFERED"):
        toolset.validate_call("unknown_tool", {})
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        toolset.validate_call("code_execute", {"code": "print(1)"})
    toolset.validate_call("code_execute", {"code": "print(1)", "description": "test"})


def test_runtime_provider_projection_preserves_legacy_tool_descriptions() -> None:
    from config.chat_tools import get_chat_tools
    from services.agent.runtime.catalog.production_seed import build_seed_snapshot

    legacy = {
        item["function"]["name"]: item["function"].get("description", "")
        for item in get_chat_tools("audit-org")
    }
    snapshot = build_seed_snapshot(scope="channel")
    catalog = {
        item.canonical_name: item
        for item in snapshot.receipt.catalog.definitions()
    }
    projected = {
        item["function"]["name"]: item["function"]
        for item in snapshot.toolset.provider_tools()
    }
    assert set(legacy) <= set(catalog)
    for name, description in legacy.items():
        assert description
        assert catalog[name].description == description
        assert projected[name]["description"] == description


def test_runtime_first_schema_batch_preserves_legacy_parameters() -> None:
    from config.chat_tools import get_chat_tools
    from services.agent.runtime.catalog.legacy_contract import (
        LEGACY_SCHEMA_COMPATIBLE_TOOLS,
    )
    from services.agent.runtime.catalog.production_seed import build_seed_snapshot

    legacy = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in get_chat_tools("audit-org")
    }
    snapshot = build_seed_snapshot(scope="channel")
    projected = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in snapshot.toolset.provider_tools()
    }
    expected = LEGACY_SCHEMA_COMPATIBLE_TOOLS & legacy.keys()
    assert expected
    for name in expected:
        assert projected[name] == legacy[name]


def test_effective_toolset_validates_nested_schema_and_json_values() -> None:
    tool = RuntimeToolDefinition(
        canonical_name="nested", tool_group="code",
        schema={"type": "object", "required": ["mode", "items"],
                "additionalProperties": False,
                "properties": {"mode": {"type": "string", "enum": ["safe"]},
                                "items": {"type": "array", "items": {"type": "integer"}}}},
        safety_level="safe", executor_type="test", executor_revision=1,
        capability_requirements=frozenset(), side_effect="none",
        authorization_requirement="none", retry_semantics="safe",
        reconcile_semantics="none", cancel_semantics="none", result_schema_revision=1,
    )
    toolset = EffectiveToolset(definitions=(tool,), catalog_revision="catalog",
                               toolset_hash="toolset")
    toolset.validate_call("nested", {"mode": "safe", "items": [1, 2]})
    for invalid in (
        {"mode": "safe", "items": [1], "extra": True},
        {"mode": "unsafe", "items": [1]},
        {"mode": "safe", "items": [1.5]},
        {"mode": "safe", "items": [float("nan")]},
    ):
        with pytest.raises(ValueError, match="SCHEMA_INVALID"):
            toolset.validate_call("nested", invalid)


def test_context_is_anchor_bound_and_repeats_tool_call_and_result_once() -> None:
    toolset = _toolset()
    action = {
        "model_step_id": "step-1", "stable_tool_call_id": "call-1",
        "tool_name": "code_execute", "status": "completed", "arguments": {
            "code": "print(1)", "description": "test",
        }, "result": {"status": "success", "summary": "done",
                       "data": {"value": float("nan")}, "external_receipt": "hidden"},
    }
    first = build_runtime_context(
        run={"id": "run-1", "context_receipt": {
            "base_context_revision": "message:1", "through_message_id": "msg-1",
        }}, session={"id": "session-1"}, messages=[
            {"role": "user", "content": "hello"},
        ], actions=[action], toolset=toolset, model_step=2,
    )
    second = build_runtime_context(
        run={"id": "run-1", "context_receipt": {
            "base_context_revision": "message:1", "through_message_id": "msg-1",
        }}, session={"id": "session-1"}, messages=[
            {"role": "user", "content": "hello"},
        ], actions=[action], toolset=toolset, model_step=2,
    )
    messages, _ = first.plan.project()
    assert first.plan.plan_hash == second.plan.plan_hash
    assert [message["role"] for message in messages] == [
        "user", "assistant", "tool",
    ]
    assert '"status":"success"' in messages[-1]["content"]
    assert '"data":null' in messages[-1]["content"]
    assert "external_receipt" not in messages[-1]["content"]


def test_context_rejects_missing_anchor_and_unknown_action() -> None:
    with pytest.raises(RuntimeError, match="ANCHOR_MISSING"):
        build_runtime_context(
            run={"id": "run-1", "context_receipt": {}},
            session={"id": "session-1"}, messages=[], actions=[],
            toolset=_toolset(), model_step=1,
        )
    with pytest.raises(ValueError, match="NOT_OFFERED"):
        build_runtime_context(
            run={"id": "run-1", "context_receipt": {
                "base_context_revision": "message:1", "through_message_id": "msg-1",
            }}, session={"id": "session-1"}, messages=[], actions=[{
                "model_step_id": "step-1", "stable_tool_call_id": "call-1",
                "tool_name": "legacy_tool", "arguments": {},
            }], toolset=_toolset(), model_step=1,
        )
