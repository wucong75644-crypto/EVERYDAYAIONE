"""Block 01: isolated catalog contracts, scope filtering and policy handoff."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from config.chat_tools import get_chat_tools, get_tools_for_mode
from services.agent.tool_args_validator import validate_tool_args
from services.tools import (
    Exposure, LegacyAdvertisement, ToolAccessDecision, ToolAvailability, ToolContext,
    ToolRegistry, build_legacy_catalog, validate_legacy_coverage,
)


@pytest.fixture(scope="module")
def registry():
    return build_legacy_catalog()


def context(**changes):
    values = dict(
        actor_user_id="actor-a", workspace_owner_id="actor-a", org_id="org-a",
        context_scope="user", personal_context_allowed=True, agent_domain="general",
        permission_mode="ask", execution_mode="interactive",
        feature_flags=dict(file_workspace_enabled=True, sandbox_enabled=True, crawler_enabled=True),
    )
    values.update(changes)
    return ToolContext(**values)


class StubPolicy:
    """Test-only decision producer, NOT a second implementation of ToolPolicy."""

    def __init__(self, decide=None):
        self.calls = []
        self.decide = decide or (lambda spec, ctx: True)

    def resolve_access(self, spec, ctx):
        self.calls.append((spec, ctx))
        return ToolAccessDecision(self.decide(spec, ctx), "test_policy")


def resolve(registry, ctx=None, policy=None, discovered=(), advertisement=None):
    return registry.resolve(
        ctx or context(), policy=policy or StubPolicy(),
        advertisement=advertisement or LegacyAdvertisement(), discovered_names=discovered,
    )


def test_catalog_matches_actual_full_directory_and_handler_bindings(registry):
    from services.tool_executor import ToolExecutor
    executor = ToolExecutor(None, "actor-a", "conversation-a", "org-a")
    assert validate_legacy_coverage(
        registry, public_schemas=get_chat_tools("org-a"), handler_names=executor._handlers,
    ) == ()
    assert {s.name for s in registry.specs() if s.definition_kind == "explicit"} == {
        "search_knowledge", "file_search", "file_delete",
    }
    assert {s.name for s in registry.specs() if s.exposure is Exposure.LEGACY_INTERNAL} == {
        "fetch_all_pages", "get_conversation_context",
    }
    assert all(callable(executor._handlers[s.handler_key]) for s in registry.specs())


@pytest.mark.parametrize("org", [None, "org-a"])
def test_every_legacy_definition_readable_by_name_and_complete_schema(registry, org):
    old = get_chat_tools(org)
    for schema in old:
        spec = registry.require(schema["function"]["name"])
        assert spec.to_schema() == schema, spec.name
    public_names = {s.name for s in registry.specs() if s.exposure is Exposure.PUBLIC}
    assert public_names == {t["function"]["name"] for t in get_chat_tools("org-a")}


def test_coverage_detects_duplicate_missing_extra_and_field_drift(registry):
    schemas = get_chat_tools("org-a")
    names = [s.handler_key for s in registry.specs()]
    changed = deepcopy(schemas)
    changed[0]["function"]["parameters"]["properties"]["action"]["description"] = "changed"
    changed.append(deepcopy(changed[0]))
    changed.append({"type": "function", "function": {"name": "new_public", "parameters": {}}})
    changed = [t for t in changed if t["function"]["name"] != "web_search"]
    issues = validate_legacy_coverage(
        registry, public_schemas=changed,
        handler_names=[name for name in names if name != "file_search"] + ["new_internal"],
    )
    assert "duplicate_schema:erp_info_query" in issues
    assert "schema_mismatch:erp_info_query" in issues
    assert "missing_public_spec:new_public" in issues
    assert "unexpected_public_spec:web_search" in issues
    assert "missing_handler:file_search:file_search" in issues
    assert "missing_handler_spec:new_internal" in issues
    reduced = ToolRegistry(s for s in registry.specs() if s.name != "file_delete")
    assert "missing_public_spec:file_delete" in validate_legacy_coverage(
        reduced, public_schemas=schemas, handler_names=names,
    )


def test_duplicate_registration_and_unknown_tool_fail_explicitly(registry):
    with pytest.raises(ValueError, match="Duplicate tool registration: search_knowledge"):
        ToolRegistry([registry.require("search_knowledge")] * 2)
    with pytest.raises(TypeError, match="validated ToolSpec"):
        ToolRegistry([{}])
    with pytest.raises(KeyError, match="Unknown tool"):
        registry.require("route_to_chat")
    assert registry.get("missing") is None


@pytest.mark.parametrize("change", [
    {"name": ""}, {"handler_key": ""}, {"schema": None}, {"domain": ""},
    {"availability": None}, {"risk_level": ""}, {"parallelizable": None},
    {"cacheable": None}, {"effects": ()}, {"executor_type": ""}, {"source": ""},
    {"exposure": None}, {"schema": {}},
])
def test_required_spec_fields_are_validated(registry, change):
    with pytest.raises(ValueError):
        replace(registry.require("search_knowledge"), **change)


def test_original_schema_required_and_name_validation(registry):
    original = registry.require("search_knowledge")
    for field, value in (("name", "wrong"), ("parameters", {"type": "object", "properties": {}, "required": ["missing"]})):
        schema = original.to_schema()
        schema["function"][field] = value
        with pytest.raises(ValueError, match="schema"):
            replace(original, schema=schema)


def test_schema_context_and_result_snapshots_cannot_leak_mutations(registry):
    original = registry.require("search_knowledge")
    raw = original.to_schema()
    spec = replace(original, schema=raw)
    raw["function"]["parameters"]["properties"]["query"]["type"] = "integer"
    assert spec.to_schema() == original.to_schema()
    output = spec.to_schema()
    output["function"]["name"] = "changed"
    assert spec.name == "search_knowledge"
    with pytest.raises(TypeError):
        spec.schema["function"]["name"] = "changed"
    with pytest.raises(FrozenInstanceError):
        spec.name = "changed"
    flags = {"file_workspace_enabled": True}
    scope = {"file_search"}
    snapshot = {"org": "org-a", "nested": {"tools": ["file_search"]}}
    manifest = [{"file_id": "fid_12345678"}]
    ctx = context(feature_flags=flags, authorized_tool_names=scope,
                  authorization_snapshot=snapshot, resource_manifest=manifest)
    flags["file_workspace_enabled"] = False
    scope.add("file_delete")
    snapshot["nested"]["tools"].append("file_delete")
    manifest[0]["file_id"] = "other"
    assert ctx.feature_flags["file_workspace_enabled"] is True
    assert ctx.authorized_tool_names == {"file_search"}
    assert ctx.authorization_snapshot["nested"]["tools"] == ("file_search",)
    assert ctx.resource_manifest[0]["file_id"] == "fid_12345678"
    result = resolve(registry, ctx)
    with pytest.raises(TypeError):
        result.allowed["file_delete"] = registry.require("file_delete")
    result.advertised_schemas()[0]["function"]["name"] = "changed"
    assert "file_search" in resolve(registry, ctx).advertised


def test_three_representative_parameter_contracts_field_by_field(registry):
    expected = {
        "search_knowledge": ({"query": "string"}, ["query"]),
        "file_search": ({"path": "string", "keyword": "string", "file_pattern": "string", "scope": "string"}, []),
        "file_delete": ({"file_ids": "array", "files": "array", "resource_refs": "array"}, []),
    }
    old = {t["function"]["name"]: t for t in get_chat_tools("org-a")}
    for name, (properties, required) in expected.items():
        new = registry.require(name).to_schema()
        assert new == old[name]
        parameters = new["function"]["parameters"]
        assert parameters.get("required", []) == required
        assert {key: value["type"] for key, value in parameters["properties"].items()} == properties
        for key in parameters["properties"]:
            assert parameters["properties"][key] == old[name]["function"]["parameters"]["properties"][key]
    assert registry.require("file_search").to_schema()["function"]["parameters"]["properties"]["scope"]["enum"] == ["current", "workspace"]
    delete = registry.require("file_delete").to_schema()["function"]["parameters"]["properties"]
    assert delete["file_ids"]["items"] == {"type": "string", "pattern": "^fid_[a-z0-9]{8}$"}
    assert delete["files"]["items"] == {"type": "string"}


@pytest.mark.parametrize("name,args", [
    ("search_knowledge", {"query": "退货流程"}), ("search_knowledge", {}),
    ("file_search", {}), ("file_search", {"path": "reports", "keyword": "销售", "file_pattern": "*.csv", "scope": "workspace"}),
    ("file_search", {"scope": "current"}),
    ("file_delete", {"files": ["report.csv"]}), ("file_delete", {"file_ids": ["fid_12345678"]}),
    ("file_delete", {"file_ids": ["fid_12345678"], "files": ["other.csv"]}),
    ("file_delete", {"files": '["report.csv"]'}),
    ("erp_agent", {"query": "订单统计"}), ("file_analyze", {"path": "report.csv"}),
])
def test_parameter_reading_and_legacy_aliases_unchanged(registry, name, args):
    old = validate_tool_args(name, deepcopy(args), get_chat_tools("org-a"))
    new = validate_tool_args(name, deepcopy(args), [registry.require(name).to_schema()])
    assert new == old
    if name == "erp_agent":
        assert new == ({"task": "订单统计"}, None)


def test_internal_existing_schema_is_preserved_without_new_schema(registry):
    from config.erp_tools import build_fetch_all_pages_tool
    assert registry.require("fetch_all_pages").to_schema() == build_fetch_all_pages_tool()
    assert registry.require("get_conversation_context").schema is None


@pytest.mark.parametrize("org,scope,personal,domain,name,allowed,reason", [
    (None, "user", True, "general", "search_knowledge", True, None),
    (None, "user", True, "general", "file_search", True, None),
    (None, "user", True, "general", "manage_scheduled_task", False, "organization_required"),
    (None, "user", True, "erp", "erp_trade_query", False, "organization_required"),
    ("org-a", "user", True, "erp", "erp_trade_query", True, None),
    ("org-a", "user", True, "general", "erp_trade_query", False, "domain_mismatch"),
    ("org-a", "user", True, "erp", "file_search", False, "domain_mismatch"),
    ("org-a", "user", True, "general", "manage_scheduled_task", True, None),
    ("org-a", "channel", False, "general", "manage_scheduled_task", False, "personal_context_required"),
    ("org-a", "channel", False, "general", "file_search", True, None),
    ("org-a", "channel", False, "general", "search_knowledge", True, None),
    ("org-a", "channel", False, "erp", "erp_trade_query", True, None),
    ("org-a", "user", False, "general", "manage_scheduled_task", False, "personal_context_required"),
    ("org-a", "user", True, "erp", "code_execute", True, None),
    ("org-a", "user", True, "general", "code_execute", True, None),
])
def test_scope_and_domain_allow_deny_matrix(registry, org, scope, personal, domain, name, allowed, reason):
    ctx = context(org_id=org, context_scope=scope, personal_context_allowed=personal,
                  workspace_owner_id="group-a" if scope == "channel" else "actor-a", agent_domain=domain)
    result = resolve(registry, ctx)
    assert (name in result.allowed) is allowed
    assert result.denied.get(name) == reason


@pytest.mark.parametrize("name,flag", [("file_search", "file_workspace_enabled"), ("code_execute", "sandbox_enabled"), ("social_crawler", "crawler_enabled")])
@pytest.mark.parametrize("enabled", [False, None, True])
def test_feature_snapshots_are_required_and_isolated(registry, name, flag, enabled):
    result = resolve(registry, context(feature_flags={} if enabled is None else {flag: enabled}))
    assert (name in result.allowed) is (enabled is True)


@pytest.mark.parametrize("change", [
    {"actor_user_id": ""}, {"workspace_owner_id": ""}, {"org_id": ""},
    {"workspace_owner_id": "other-user"}, {"context_scope": "channel"},
    {"agent_domain": "unknown"}, {"permission_mode": "unknown"}, {"execution_mode": "auto"},
    {"entrypoint": "unknown"}, {"feature_flags": {"sandbox_enabled": "true"}},
    {"authorized_tool_names": "file_delete"},
])
def test_untrusted_or_inconsistent_context_rejected(change):
    with pytest.raises(ValueError):
        context(**change)


@pytest.mark.parametrize("names", [None, frozenset(), frozenset({"file_search"}), frozenset({"file_delete", "made_up"})])
def test_authorization_names_are_only_a_restriction(registry, names):
    ctx = context(authorized_tool_names=names)
    result = resolve(registry, ctx, discovered={"file_delete", "made_up"})
    if names is not None:
        assert set(result.allowed) <= names
    assert "made_up" not in result.allowed
    denied = resolve(registry, ctx, policy=StubPolicy(lambda spec, ctx: False), discovered={"file_delete"})
    assert not denied.allowed and not denied.advertised


def test_policy_required_and_failures_never_fall_back(registry):
    with pytest.raises(TypeError):
        registry.resolve(context(), advertisement=LegacyAdvertisement())
    policy = Mock()
    policy.resolve_access.side_effect = RuntimeError("permission service unavailable")
    with pytest.raises(RuntimeError, match="permission service unavailable"):
        resolve(registry, policy=policy)
    policy.resolve_access.side_effect = None
    policy.resolve_access.return_value = True
    with pytest.raises(TypeError, match="ToolAccessDecision"):
        resolve(registry, policy=policy)


@pytest.mark.parametrize("permission", ["ask", "auto", "plan"])
@pytest.mark.parametrize("execution", ["interactive", "scheduled", "preflight"])
def test_mode_and_authorization_decisions_have_one_policy_owner(registry, permission, execution):
    ctx = context(permission_mode=permission, execution_mode=execution,
                  authorization_snapshot={"test_grant": "allow-file-search"},
                  conversation_id="c1", task_id="t1", call_id="call1")
    policy = StubPolicy(lambda spec, current: (
        spec.name == "file_search" and current.permission_mode == permission
        and current.execution_mode == execution
        and current.authorization_snapshot.get("test_grant") == "allow-file-search"
    ))
    result = resolve(registry, ctx, policy=policy, discovered={"file_delete"})
    assert set(result.allowed) == {"file_search"}
    assert all(received is ctx for _, received in policy.calls)
    assert result.denied["file_delete"] == "test_policy"
    assert not resolve(registry, replace(ctx, authorization_snapshot={}), policy=policy).allowed


def test_sequential_requests_do_not_reuse_user_org_workspace_or_mode(registry):
    policy = StubPolicy(lambda spec, ctx: ctx.authorization_snapshot.get("subject") == (
        f"{ctx.actor_user_id}/{ctx.org_id}/{ctx.workspace_owner_id}/{ctx.permission_mode}"
    ))
    first = context(authorization_snapshot={"subject": "actor-a/org-a/actor-a/ask"})
    assert "file_search" in resolve(registry, first, policy).allowed
    second = context(actor_user_id="actor-b", workspace_owner_id="group-b", context_scope="channel",
                     personal_context_allowed=False, org_id="org-b", permission_mode="auto",
                     authorization_snapshot=first.authorization_snapshot)
    assert not resolve(registry, second, policy).allowed
    third = replace(second, authorization_snapshot={"subject": "actor-b/org-b/group-b/auto"})
    assert "file_search" in resolve(registry, third, policy).allowed
    assert "manage_scheduled_task" not in resolve(registry, third, policy).allowed
    assert "manage_scheduled_task" in resolve(registry, first, policy).allowed


@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
def test_advertisement_reuses_current_core_rules(registry, mode):
    result = resolve(registry, context(permission_mode=mode))
    assert result.advertised_schemas() == get_tools_for_mode(mode, "org-a")
    assert set(result.advertised) <= set(result.allowed)
    # Block 01 retains old plan display facts; Block 02 will decide permission.
    assert ("erp_agent" in result.advertised) is (mode != "plan")


def test_discovery_cannot_expand_permission_or_expose_internal_tools(registry):
    discovered = {s.name for s in registry.specs()} | {"invented_tool", "route_to_chat"}
    policy = StubPolicy(lambda spec, ctx: spec.name != "generate_image")
    ctx = context(authorized_tool_names={"file_search", "generate_image", "code_execute", "erp_trade_query", "get_conversation_context"})
    result = resolve(registry, ctx, policy, discovered)
    assert set(result.advertised) == {"file_search", "code_execute"}
    assert "erp_trade_query" not in result.allowed
    assert "get_conversation_context" not in result.allowed
    assert "generate_image" not in result.allowed
    assert "generate_video" not in resolve(registry).advertised
    assert "generate_video" in resolve(registry, discovered={"generate_video"}).advertised


@pytest.mark.parametrize("name,domain,org", [("get_conversation_context", "general", None), ("fetch_all_pages", "erp", "org-a")])
def test_handler_only_entry_boundary(registry, name, domain, org):
    ctx = context(agent_domain=domain, org_id=org)
    assert name not in resolve(registry, ctx, discovered={name}).allowed
    internal = replace(ctx, entrypoint="legacy_internal")
    result = resolve(registry, internal, discovered={name})
    assert name in result.allowed
    assert name not in result.advertised
    if name == "fetch_all_pages":
        assert name not in resolve(registry, replace(internal, org_id=None)).allowed
    else:
        group = replace(internal, context_scope="channel", workspace_owner_id="group-a", personal_context_allowed=False)
        assert name not in resolve(registry, group).allowed


@pytest.mark.parametrize("execution", ["scheduled", "preflight"])
def test_frozen_scenarios_do_not_discover_more_tools(registry, execution):
    result = resolve(registry, context(execution_mode=execution), discovered={"generate_video"},
                     advertisement=LegacyAdvertisement({"file_search"}))
    assert set(result.advertised) == {"file_search"}


def test_risk_parallel_cache_and_effects_are_independent(registry):
    from config.chat_tools import get_safety_level, is_concurrency_safe
    from services.agent.tool_result_cache import ToolResultCache
    for spec in registry.specs():
        assert spec.risk_level == get_safety_level(spec.name).value
        assert spec.parallelizable == is_concurrency_safe(spec.name)
        assert spec.cacheable == ToolResultCache.is_cacheable(spec.name)
    code = registry.require("code_execute")
    assert code.cacheable and code.parallelizable and "kernel_state" in code.effects
    restore = registry.require("restore_file")
    assert restore.risk_level == "safe" and not restore.parallelizable
    assert "workspace_write" in restore.effects
    spec = replace(registry.require("file_search"), cacheable=False, parallelizable=True, risk_level="dangerous")
    assert not spec.cacheable and spec.parallelizable and spec.effects == ("file_index",)


def test_catalog_factories_remain_independent_of_production_runtime():
    # 04 deliberately imports services.tools in runtime consumers. Catalog
    # factories must remain independent to prevent initialization recursion.
    backend = Path(__file__).resolve().parents[1]
    for path in (backend / "config").rglob("*.py"):
        assert "from services.tools" not in path.read_text()
    legacy = (backend / "services/tools/legacy.py").read_text()
    assert "from services.agent.tool_executor import" not in legacy


def test_partial_legacy_validation_directory_is_preserved_separately(registry):
    from config.agent_tools import TOOL_SCHEMAS
    for spec in registry.specs():
        assert spec.to_legacy_validation_schema() == TOOL_SCHEMAS.get(spec.name), spec.name
    internal = registry.require("get_conversation_context")
    assert internal.to_legacy_validation_schema() == {
        "required": [], "properties": {"limit": {"type": "integer"}},
    }
    assert internal.to_schema() is None
    # Preserve the pre-existing difference; do not turn old validator 'files'
    # required into a new model schema requirement that rejects legal file_ids.
    delete = registry.require("file_delete")
    assert delete.to_legacy_validation_schema()["required"] == []
    assert "required" not in delete.to_schema()["function"]["parameters"]


def test_missing_domain_is_reported_during_catalog_build(monkeypatch):
    from config.tool_domains import TOOL_DOMAINS
    monkeypatch.delitem(TOOL_DOMAINS, "search_knowledge")
    with pytest.raises(ValueError, match="missing domain: search_knowledge"):
        build_legacy_catalog()


def test_every_public_tool_parameter_reader_matches_original(registry):
    def sample(prop):
        if "enum" in prop:
            return prop["enum"][0]
        if prop.get("type") == "object":
            return {name: sample(value) for name, value in prop.get("properties", {}).items()}
        if prop.get("type") == "array":
            return [sample(prop.get("items", {}))]
        return {"integer": 1, "number": 1.0, "boolean": True}.get(prop.get("type"), "fixture")
    for original in get_chat_tools("org-a"):
        name = original["function"]["name"]
        params = original["function"]["parameters"]
        args = {name: sample(prop) for name, prop in params["properties"].items()}
        new = registry.require(name).to_schema()
        assert validate_tool_args(name, deepcopy(args), [new]) == validate_tool_args(name, deepcopy(args), [original]), name
        assert validate_tool_args(name, {}, [new]) == validate_tool_args(name, {}, [original]), name


def test_mode_changes_recompute_policy_access_not_just_advertisement(registry):
    policy = StubPolicy(lambda spec, ctx: (ctx.permission_mode, ctx.execution_mode) == ("ask", "interactive"))
    first = context()
    assert "file_delete" in resolve(registry, first, policy).allowed
    assert not resolve(registry, replace(first, permission_mode="plan"), policy).allowed
    assert not resolve(registry, replace(first, execution_mode="preflight"), policy).allowed
    assert not resolve(registry, replace(first, execution_mode="scheduled"), policy).allowed
    assert "file_delete" in resolve(registry, first, policy).allowed


def test_erp_initial_and_dynamic_selection_is_filtered_by_allowed(registry):
    from config.phase_tools import build_domain_tools
    initial = {tool["function"]["name"] for tool in build_domain_tools("erp")}
    selected = LegacyAdvertisement(initial)
    ctx = context(agent_domain="erp", authorized_tool_names={"erp_trade_query", "code_execute"})
    result = resolve(registry, ctx, discovered={"erp_trade_query", "erp_execute", "file_search"}, advertisement=selected)
    assert set(result.advertised) == {"erp_trade_query", "code_execute"}
    assert "fetch_all_pages" not in result.advertised
    assert "route_to_chat" not in result.advertised
