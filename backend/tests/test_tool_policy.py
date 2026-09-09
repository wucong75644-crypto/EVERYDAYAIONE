"""Block 02 acceptance: pure decisions only; no real dangerous operations."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from services.kuaimai.registry import TOOL_REGISTRIES
from services.tools import (
    LegacyAdvertisement, ToolCall, ToolConfirmation, ToolContext, ToolPolicy,
    ToolPolicyRules, ToolRegistry, build_legacy_catalog,
)


@pytest.fixture(scope="module")
def registry():
    return build_legacy_catalog()


def context(**changes):
    values = dict(
        actor_user_id="actor", workspace_owner_id="actor", org_id="org",
        context_scope="user", personal_context_allowed=True, agent_domain="general",
        permission_mode="ask", execution_mode="interactive", conversation_id="conversation",
        call_id="call", confirmation_available=True,
        feature_flags=dict(file_workspace_enabled=True, sandbox_enabled=True, crawler_enabled=True),
    )
    values.update(changes)
    return ToolContext(**values)


# Each tuple is an explicit ask / auto / plan expectation, not computed by Policy.
# A=allow, C=require_confirmation, D=deny. Noninteractive rows have a valid scope.
MATRIX = [
    ("file_search", {}, ("AAA", "AAA", "AAA")),
    ("restore_file", {}, ("AAD", "AAD", "DDD")),
    ("file_delete", {"file_ids": ["f1"]}, ("CCD", "DDD", "DDD")),
    ("image_agent", {}, ("AAD", "AAD", "DDD")),
    ("manage_scheduled_task", {"action": "create"}, ("AAA", "AAA", "DDD")),
    ("manage_scheduled_task", {"action": "list"}, ("AAA", "AAA", "DDD")),
    ("code_execute", {"code": "1+1"}, ("AAA", "AAA", "AAA")),
    ("erp_agent", {"task": "read orders"}, ("AAD", "AAD", "AAD")),
    ("social_crawler", {}, ("AAD", "AAD", "AAD")),
]
OUTCOMES = {"A": "allow", "C": "require_confirmation", "D": "deny"}
MATRIX_CASES = [
    pytest.param(name, args, scenario, mode, OUTCOMES[rows[si][mi]],
                 id=f"{name}-{args.get('action', 'default')}-{scenario}-{mode}")
    for name, args, rows in MATRIX
    for si, scenario in enumerate(("interactive", "scheduled", "preflight"))
    for mi, mode in enumerate(("ask", "auto", "plan"))
]


@pytest.mark.parametrize("name,args,scenario,mode,expected", MATRIX_CASES)
def test_mode_authorization_matrix(registry, name, args, scenario, mode, expected):
    ctx = context(permission_mode=mode, execution_mode=scenario)
    if scenario != "interactive":
        ctx = replace(ctx, task_id="task", authorized_tool_names={name},
                      authorization_snapshot={"allowed_tools": [name], "version": 1})
    decision = ToolPolicy(registry).decide(name, ctx, args)
    assert decision.outcome == expected
    assert decision.reason
    # The same name-level fact source feeds both allowed and advertised.
    resolved = registry.resolve(ctx, policy=ToolPolicy(registry), advertisement=LegacyAdvertisement([name]))
    assert (name in resolved.allowed) == (expected != "deny")
    assert (name in resolved.advertised) == (expected != "deny")


@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
@pytest.mark.parametrize("scenario", ["scheduled", "preflight"])
@pytest.mark.parametrize("override,reason", [
    ({"task_id": None}, "execution_authorization_required"),
    ({"authorized_tool_names": None}, "execution_authorization_required"),
    ({"authorized_tool_names": set()}, "outside_authorized_scope"),
    ({"authorization_snapshot": {}}, "execution_authorization_required"),
    ({"authorization_snapshot": {"allowed_tools": "file_search"}}, "execution_authorization_required"),
    ({"authorization_snapshot": {"allowed_tools": [1]}}, "execution_authorization_required"),
    ({"authorization_snapshot": {"allowed_tools": []}}, "outside_execution_authorization"),
    ({"authorization_snapshot": {"allowed_tools": ["code_execute"]}}, "outside_execution_authorization"),
])
def test_noninteractive_requires_both_trusted_scope_bounds(registry, mode, scenario, override, reason):
    ctx = context(permission_mode=mode, execution_mode=scenario, task_id="task",
                  authorized_tool_names={"file_search"}, authorization_snapshot={"allowed_tools": ["file_search"]})
    decision = ToolPolicy(registry).decide("file_search", replace(ctx, **override), {})
    assert (decision.outcome, decision.reason) == ("deny", reason)


@pytest.mark.parametrize("action", ["create", "update", "pause", "resume", "delete", "list"])
@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
def test_task_actions_only_propose_without_second_confirmation(registry, action, mode):
    decision = ToolPolicy(registry).decide(
        "manage_scheduled_task", context(permission_mode=mode, confirmation_available=False), {"action": action},
    )
    assert decision.outcome == "allow"
    assert decision.operation == ("read" if action == "list" else "proposal")
    assert decision.confirmation_binding is None


@pytest.mark.parametrize("name", ["code_execute", "image_agent", "generate_image", "generate_video"])
def test_confirm_risk_is_resource_notice_not_approval(registry, name):
    decision = ToolPolicy(registry).decide(name, context(confirmation_available=False), {})
    assert (decision.outcome, decision.reason) == ("allow", "resource_notice")


def approved(policy, ctx, name="file_delete", args=None):
    request = policy.decide(name, ctx, args or {"file_ids": ["f1"]})
    assert request.outcome == "require_confirmation"
    return ToolConfirmation(request.confirmation_binding, "approved")


@pytest.mark.parametrize("mode", ["ask", "auto"])
def test_danger_confirmation_matrix(registry, mode):
    policy, ctx, args = ToolPolicy(registry), context(permission_mode=mode), {"file_ids": ["f1"]}
    receipt = approved(policy, ctx)
    assert policy.decide("file_delete", ctx, args, confirmation=receipt).outcome == "allow"
    for supplied, expected, reason in [
        (None, "require_confirmation", "dangerous_confirmation_required"),
        (replace(receipt, status="rejected"), "deny", "confirmation_rejected"),
        ({"approved": True}, "deny", "invalid_confirmation"),
        (True, "deny", "invalid_confirmation"),
    ]:
        decision = policy.decide("file_delete", ctx, args, confirmation=supplied)
        assert (decision.outcome, decision.reason) == (expected, reason)
    # Missing/failed UI facilities cannot be supplied as implicit approval.
    for supplied in (None, receipt):
        decision = policy.decide("file_delete", replace(ctx, confirmation_available=False), args, confirmation=supplied)
        assert (decision.outcome, decision.reason) == ("deny", "confirmation_unavailable")


@pytest.mark.parametrize("changes", [
    {"call_id": "another-call"}, {"conversation_id": "another-conversation"}, {"task_id": "another-task"},
    {"actor_user_id": "other", "workspace_owner_id": "other"}, {"org_id": "other-org"},
    {"workspace_owner_id": "channel", "context_scope": "channel", "personal_context_allowed": False},
    {"permission_mode": "auto"}, {"entrypoint": "legacy_internal"},
    {"authorized_tool_names": {"file_delete"}}, {"authorization_snapshot": {"revision": 2}},
    {"resource_manifest": [{"id": "other-file"}]},
    {"feature_flags": {"file_workspace_enabled": True, "sandbox_enabled": False}},
])
def test_confirmation_cannot_cross_call_user_parameters_or_scope(registry, changes):
    policy, ctx = ToolPolicy(registry), context()
    receipt = approved(policy, ctx)
    decision = policy.decide("file_delete", replace(ctx, **changes), {"file_ids": ["f1"]}, confirmation=receipt)
    assert (decision.outcome, decision.reason) == ("deny", "confirmation_binding_mismatch")


def test_confirmation_parameters_are_canonical_and_snapshot_is_immutable(registry):
    policy, ctx = ToolPolicy(registry), context()
    args = {"file_ids": ["f1"], "nested": {"b": 2, "a": 1}}
    receipt = approved(policy, ctx, args=args)
    reordered = {"nested": {"a": 1, "b": 2}, "file_ids": ["f1"]}
    assert policy.decide("file_delete", ctx, reordered, confirmation=receipt).outcome == "allow"
    args["file_ids"].append("f2")
    assert policy.decide("file_delete", ctx, args, confirmation=receipt).reason == "confirmation_binding_mismatch"
    with pytest.raises(FrozenInstanceError):
        receipt.status = "approved"
    assert policy.decide("trigger_erp_sync", context(agent_domain="erp"), {}, confirmation=receipt).outcome == "deny"


@pytest.mark.parametrize("changes,reason", [
    ({"call_id": None}, "confirmation_scope_required"),
    ({"conversation_id": None}, "confirmation_scope_required"),
    ({"permission_mode": "plan"}, "plan_forbidden"),
    ({"feature_flags": {}}, "feature_unavailable:file_workspace_enabled"),
    ({"authorized_tool_names": set()}, "outside_authorized_scope"),
])
def test_current_access_rechecked_even_with_approval(registry, changes, reason):
    policy, ctx = ToolPolicy(registry), context()
    decision = policy.decide("file_delete", replace(ctx, **changes), {"file_ids": ["f1"]}, confirmation=approved(policy, ctx))
    assert (decision.outcome, decision.reason) == ("deny", reason)


@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
def test_model_json_cannot_override_trusted_context_or_confirm(registry, mode):
    ctx = context(permission_mode=mode, confirmation_available=False, authorized_tool_names={"file_delete"})
    forged = {
        "file_ids": ["f1"], "permission_mode": "auto", "execution_mode": "interactive",
        "actor_user_id": "boss", "workspace_owner_id": "boss", "org_id": "other",
        "authorized_tool_names": ["file_delete", "image_agent"], "confirmation_available": True,
        "confirmation": {"status": "approved"}, "approved": True,
        "authorization_snapshot": {"allowed_tools": ["file_delete"]},
    }
    assert ToolPolicy(registry).decide("file_delete", ctx, forged).outcome == "deny"
    assert ToolPolicy(registry).decide("image_agent", ctx, forged).reason == "outside_authorized_scope"
    assert ctx.actor_user_id == "actor" and ctx.permission_mode == mode


ERP_CASES = [
    pytest.param(name, action, entry.is_write, id=f"{name}-{action}")
    for name, entries in TOOL_REGISTRIES.items() if name != "erp_execute"
    for action, entry in entries.items()
]


@pytest.mark.parametrize("name,action,is_write", ERP_CASES)
def test_every_erp_query_action_uses_api_entry_write_fact(registry, name, action, is_write):
    policy, ctx = ToolPolicy(registry), context(agent_domain="erp")
    for args in ({"action": action}, {"action": action, "params": {}}):
        decision = policy.decide(name, ctx, args)
        assert decision.outcome == ("deny" if is_write else "allow")
        if is_write:
            assert (decision.reason, decision.risk_level) == ("erp_query_write_forbidden", "dangerous")
        else:
            assert decision.operation == "read"


@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
def test_erp_unknown_and_write_actions_never_default_allow(registry, mode):
    policy, ctx = ToolPolicy(registry), context(agent_domain="erp", permission_mode=mode)
    for name, args in [
        ("not_a_tool", {}), ("erp_trade_query", {"action": "delete_unknown"}),
        ("erp_trade_query", {"action": ["order_list"]}),
        ("erp_execute", {"category": "unknown", "action": "cancel"}),
        ("erp_execute", {"category": "trade", "action": "unknown_write"}),
        ("erp_execute", {"category": "trade", "action": "order_list"}),
    ]:
        assert policy.decide(name, ctx, args).outcome == "deny"


def test_action_fact_changes_are_used_without_updating_policy_list(registry, monkeypatch):
    entries = TOOL_REGISTRIES["erp_trade_query"]
    action, entry = next((a, e) for a, e in entries.items() if not e.is_write)
    monkeypatch.setitem(entries, action, replace(entry, is_write=True))
    decision = ToolPolicy(registry).decide("erp_trade_query", context(agent_domain="erp"), {"action": action})
    assert decision.reason == "erp_query_write_forbidden"


def test_erp_execute_category_and_known_writes_require_bound_confirmation(registry):
    from services.kuaimai import registry as erp
    categories = {"basic": erp.BASIC_REGISTRY, "product": erp.PRODUCT_REGISTRY,
                  "trade": erp.TRADE_REGISTRY, "aftersales": erp.AFTERSALES_REGISTRY,
                  "warehouse": erp.WAREHOUSE_REGISTRY, "purchase": erp.PURCHASE_REGISTRY,
                  "distribution": erp.DISTRIBUTION_REGISTRY}
    policy, ctx = ToolPolicy(registry), context(agent_domain="erp", permission_mode="auto")
    for category, entries in categories.items():
        for action, entry in entries.items():
            args = {"category": category, "action": action, "params": {"target": "one"}}
            decision = policy.decide("erp_execute", ctx, args)
            assert decision.outcome == ("require_confirmation" if entry.is_write else "deny"), (category, action)
            if entry.is_write:
                receipt = ToolConfirmation(decision.confirmation_binding, "approved")
                assert policy.decide("erp_execute", ctx, args, confirmation=receipt).outcome == "allow"
                changed = {**args, "params": {"target": "two"}}
                assert policy.decide("erp_execute", ctx, changed, confirmation=receipt).outcome == "deny"
    wrong = policy.decide("erp_execute", ctx, {"category": "trade", "action": "warehouse_add"})
    assert wrong.outcome == "deny"


def test_fetch_all_pages_retains_read_only_action_guard(registry):
    policy, ctx = ToolPolicy(registry), context(agent_domain="erp", entrypoint="legacy_internal")
    for name, entries in TOOL_REGISTRIES.items():
        for action, entry in entries.items():
            decision = policy.decide("fetch_all_pages", ctx, {"tool": name, "action": action})
            assert decision.outcome == ("deny" if entry.is_write else "allow")
    assert policy.decide("fetch_all_pages", ctx, {"tool": "unknown", "action": "x"}).outcome == "deny"


@pytest.mark.parametrize("action", [None, "execute", "purge", [], "DELETE"])
def test_unknown_task_action_is_denied(registry, action):
    assert ToolPolicy(registry).decide("manage_scheduled_task", context(), {"action": action}).outcome == "deny"


def custom(registry, name="test_tool", **changes):
    base = registry.require("file_search")
    schema = base.to_schema()
    schema["function"]["name"] = name
    return replace(base, name=name, schema=schema, handler_key=name, **changes)


@pytest.mark.parametrize("kind", ["explicit", "legacy"])
@pytest.mark.parametrize("name,args,scenario,mode,expected", MATRIX_CASES)
def test_equivalent_new_and_legacy_specs_share_all_matrix_rows(registry, kind, name, args, scenario, mode, expected):
    spec = replace(registry.require(name), definition_kind=kind)
    policy = ToolPolicy(ToolRegistry([spec]))
    ctx = context(execution_mode=scenario, permission_mode=mode, task_id="task",
                  authorized_tool_names={name}, authorization_snapshot={"allowed_tools": [name]})
    assert policy.decide(name, ctx, args).outcome == expected


@pytest.mark.parametrize("scenario", ["interactive", "scheduled", "preflight"])
def test_no_mode_can_override_reviewed_operation_or_danger(registry, scenario):
    for operation, risk, expected in [("business_write", "safe", "deny"), ("generation", "confirm", "deny"),
                                       ("read", "dangerous", "deny"), ("unknown", "safe", "deny")]:
        spec = custom(registry, risk_level=risk, policy_rules=ToolPolicyRules(
            operation=operation, plan_allowed=True, execution_modes=("interactive", "scheduled", "preflight")))
        ctx = context(permission_mode="plan", execution_mode=scenario, task_id="task",
                      authorized_tool_names={spec.name}, authorization_snapshot={"allowed_tools": [spec.name]})
        assert ToolPolicy(ToolRegistry([spec])).decide(spec.name, ctx, {}).outcome == expected
    danger = custom(registry, risk_level="dangerous", policy_rules=ToolPolicyRules(
        operation="business_write", execution_modes=("interactive", "scheduled", "preflight")))
    ctx = context(execution_mode=scenario, task_id="task", authorized_tool_names={danger.name},
                  authorization_snapshot={"allowed_tools": [danger.name]})
    assert ToolPolicy(ToolRegistry([danger])).decide(danger.name, ctx, {}).outcome == (
        "require_confirmation" if scenario == "interactive" else "deny")


def test_business_permission_snapshot_is_explicit_and_fail_closed(registry):
    spec = custom(registry, policy_rules=ToolPolicyRules(operation="read", plan_allowed=True,
                                                        required_permissions=("order.view",)))
    policy = ToolPolicy(ToolRegistry([spec]))
    for perms, expected in [({}, "deny"), ({"order.view": False}, "deny"), ({"order.view": "true"}, "deny"),
                            ({"order.view": True}, "allow")]:
        ctx = context(authorization_snapshot={"permissions": perms})
        assert policy.decide(spec.name, ctx, {"permissions": {"order.view": True}}).outcome == expected


@pytest.mark.parametrize("changes,reason", [
    ({"org_id": None}, "organization_required"),
    ({"agent_domain": "erp"}, "domain_mismatch"),
    ({"workspace_owner_id": "channel", "context_scope": "channel", "personal_context_allowed": False}, "personal_context_required"),
    ({"authorized_tool_names": set()}, "outside_authorized_scope"),
])
def test_registry_and_policy_share_all_unavailability_facts(registry, changes, reason):
    ctx, policy = context(**changes), ToolPolicy(registry)
    resolution = registry.resolve(ctx, policy=policy, advertisement=LegacyAdvertisement(["manage_scheduled_task"]))
    decision = policy.decide("manage_scheduled_task", ctx, {"action": "list", "org_id": "forged"})
    assert resolution.denied["manage_scheduled_task"] == decision.reason == reason
    assert decision.outcome == "deny"


def test_batch_order_and_write_barriers_even_if_write_declares_parallel(registry):
    read = custom(registry, name="read", parallelizable=True, cacheable=False)
    write = custom(registry, name="looks_like_read", risk_level="safe", parallelizable=True,
                   policy_rules=ToolPolicyRules(operation="business_write"))
    policy = ToolPolicy(ToolRegistry([read, write]))
    calls = [ToolCall(call_id, name, {}) for call_id, name in
             [("A", "read"), ("B", "read"), ("C", "looks_like_read"), ("D", "read"), ("E", "looks_like_read")]]
    batches = policy.plan_batches(calls, context())
    assert [[p.call.call_id for p in batch] for batch in batches] == [["A", "B"], ["C"], ["D"], ["E"]]
    assert all(p.decision.outcome == "allow" for batch in batches for p in batch)
    assert [p.call for batch in batches for p in batch] == calls


def test_nonparallel_pending_denied_unknown_and_generation_are_barriers(registry):
    serial = custom(registry, name="read_serial", parallelizable=False)
    policy = ToolPolicy(ToolRegistry([*registry.specs(), serial]))
    for barrier, args, expected in [("read_serial", {}, "allow"), ("file_delete", {}, "require_confirmation"),
                                    ("unknown", {}, "deny"), ("image_agent", {}, "allow"),
                                    ("manage_scheduled_task", {"action": "delete"}, "allow")]:
        calls = [ToolCall("A", "file_search", {}), ToolCall("B", barrier, args), ToolCall("C", "file_search", {})]
        batches = policy.plan_batches(calls, context())
        assert [[p.call.call_id for p in b] for b in batches] == [["A"], ["B"], ["C"]]
        assert batches[1][0].decision.outcome == expected
    assert policy.plan_batches([], context()) == ()
    with pytest.raises(ValueError, match="Duplicate"):
        policy.plan_batches([ToolCall("same", "file_search", {})] * 2, context())


def test_batches_bind_each_confirmation_and_freeze_arguments(registry):
    policy, ctx = ToolPolicy(registry), context()
    args = {"file_ids": ["one"]}
    call = ToolCall("delete-1", "file_delete", args)
    args["file_ids"].append("two")
    first = policy.plan_batches([call], ctx)[0][0]
    receipt = ToolConfirmation(first.decision.confirmation_binding, "approved")
    result = policy.plan_batches([call], ctx, confirmations={"delete-1": receipt})[0][0]
    assert result.decision.outcome == "allow"
    assert result.call.arguments["file_ids"] == ("one",)
    another = replace(call, call_id="delete-2")
    assert policy.plan_batches([another], ctx, confirmations={"delete-2": receipt})[0][0].decision.outcome == "deny"


@pytest.mark.parametrize("parallel", [True, False])
@pytest.mark.parametrize("cacheable", [True, False])
def test_cache_effects_replay_are_never_inferred_from_concurrency(registry, parallel, cacheable):
    spec = custom(registry, parallelizable=parallel, cacheable=cacheable, effects=("kernel_state",),
                  replay_requirement="record_required", policy_rules=ToolPolicyRules(operation="analysis", plan_allowed=True))
    decision = ToolPolicy(ToolRegistry([spec])).decide(spec.name, context(), {})
    assert decision.parallelizable is parallel
    assert decision.cacheable is cacheable
    assert decision.effects == ("kernel_state",)
    assert decision.replay_requirement == "record_required"
    assert registry.require("restore_file").risk_level == "safe"
    assert registry.require("restore_file").replay_requirement == "unspecified"
    assert registry.require("code_execute").cacheable is True


@pytest.mark.parametrize("args", [[], None, "{}", {"x": float("nan")}, {1: "invalid-key"}, {"x": object()}])
def test_invalid_normalized_arguments_fail_closed(registry, args):
    decision = ToolPolicy(registry).decide("file_delete", context(), args)
    assert (decision.outcome, decision.reason) == ("deny", "invalid_normalized_arguments")


def test_policy_never_executes_ws_handler_retry_or_swallows_errors(registry, monkeypatch):
    from services.agent.tool_executor import ToolExecutor
    from services.kuaimai.dispatcher import ErpDispatcher
    from services.agent.tool_result_cache import ToolResultCache
    traps = [Mock(side_effect=AssertionError("business/UI/cache must not run")) for _ in range(3)]
    monkeypatch.setattr(ToolExecutor, "execute", traps[0])
    monkeypatch.setattr(ErpDispatcher, "execute", traps[1])
    monkeypatch.setattr(ToolResultCache, "get", traps[2])
    ctx = context(budget=Mock(), cancellation=Mock())
    policy = ToolPolicy(registry)
    policy.plan_batches([ToolCall("r", "file_search", {}), ToolCall("w", "file_delete", {})], ctx)
    for trap in traps:
        trap.assert_not_called()
    assert ctx.budget.mock_calls == [] and ctx.cancellation.mock_calls == []
    failure = Mock(side_effect=RuntimeError("snapshot failure"))
    monkeypatch.setattr(policy, "resolve_access", failure)
    with pytest.raises(RuntimeError, match="snapshot failure"):
        policy.decide("file_search", ctx, {})
    failure.assert_called_once()
    root = Path(__file__).parents[1]
    for source in (root / "services/tools/policy.py", root / "services/tools/action_rules.py"):
        text = source.read_text()
        assert "async def " not in text and "import asyncio" not in text
        assert "websocket" not in text.lower() and "ToolExecutor" not in text
    # Registry test also verifies the entire production tree has no reverse import.
    for source in [root / "services/agent/tool_executor.py", root / "services/agent/tool_loop_executor.py",
                   root / "services/handlers/chat_tool_mixin.py", root / "services/handlers/chat_tool_helpers.py"]:
        assert "services.tools" not in source.read_text()


@pytest.mark.parametrize("values", [{"operation": "invented"}, {"plan_allowed": 1}, {"execution_modes": "scheduled"},
                                    {"action_rule": "handler"}, {"required_permissions": "order.view"}])
def test_invalid_policy_declarations_are_rejected(values):
    with pytest.raises(ValueError):
        ToolPolicyRules(**values)


def test_unreviewed_spec_and_dangerous_action_fail_closed(registry):
    spec = custom(registry, policy_rules=ToolPolicyRules())
    assert ToolPolicy(ToolRegistry([spec])).decide(spec.name, context(), {}).reason == "unreviewed_operation"
    schema = spec.to_schema()
    schema["function"]["parameters"]["properties"]["action"] = {"type": "string"}
    spec = replace(spec, schema=schema, risk_level="dangerous", policy_rules=ToolPolicyRules(operation="business_write"))
    policy = ToolPolicy(ToolRegistry([spec]))
    assert policy.decide(spec.name, context(), {"action": "anything"}).reason == "unreviewed_dangerous_action"


@pytest.mark.parametrize("name,domain", [("generate_image", "general"), ("generate_video", "general"),
                                          ("erp_trade_query", "erp"), ("erp_execute", "erp")])
@pytest.mark.parametrize("scenario", ["scheduled", "preflight"])
def test_authorized_names_do_not_expand_existing_scheduled_capabilities(registry, name, domain, scenario):
    ctx = context(agent_domain=domain, execution_mode=scenario, task_id="task",
                  authorized_tool_names={name}, authorization_snapshot={"allowed_tools": [name]})
    decision = ToolPolicy(registry).decide(name, ctx, {"action": "order_list"})
    assert (decision.outcome, decision.reason) == ("deny", "execution_mode_forbidden")


def test_erp_query_write_is_denied_even_with_confirmation(registry):
    action = next(a for a, e in TOOL_REGISTRIES["erp_trade_query"].items() if e.is_write)
    policy, ctx = ToolPolicy(registry), context(agent_domain="erp", permission_mode="auto")
    args = {"category": "trade", "action": action, "params": {}}
    receipt = approved(policy, ctx, "erp_execute", args)
    decision = policy.decide("erp_trade_query", ctx, args, confirmation=receipt)
    assert (decision.outcome, decision.reason) == ("deny", "erp_query_write_forbidden")


def test_same_dangerous_spec_new_and_legacy_have_identical_confirmed_decisions(registry):
    spec, ctx, args = registry.require("file_delete"), context(), {"files": ["resolved-target"]}
    policies = [ToolPolicy(ToolRegistry([replace(spec, definition_kind=kind)])) for kind in ("explicit", "legacy")]
    receipt = approved(policies[0], ctx, args=args)
    assert policies[0].decide(spec.name, ctx, args, confirmation=receipt) == policies[1].decide(
        spec.name, ctx, args, confirmation=receipt)
    assert policies[1].decide(spec.name, ctx, args, confirmation=receipt).outcome == "allow"
    changed_spec = replace(spec, replay_requirement="record_required")
    changed_policy = ToolPolicy(ToolRegistry([changed_spec]))
    assert changed_policy.decide(spec.name, ctx, args, confirmation=receipt).reason == "confirmation_binding_mismatch"


def test_unknown_action_in_custom_dangerous_enum_is_denied(registry):
    spec = custom(registry, risk_level="dangerous", policy_rules=ToolPolicyRules(operation="business_write"))
    schema = spec.to_schema()
    schema["function"]["parameters"]["properties"]["action"] = {"type": "string", "enum": ["write"]}
    policy = ToolPolicy(ToolRegistry([replace(spec, schema=schema)]))
    assert policy.decide(spec.name, context(), {"action": "write"}).outcome == "require_confirmation"
    assert policy.decide(spec.name, context(), {"action": "purge"}).reason == "unknown_action"
