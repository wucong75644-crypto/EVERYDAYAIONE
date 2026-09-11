"""Compatibility policy descriptor helper. Execution only uses the Spec in ToolPolicy."""
from dataclasses import replace
from .catalog import definition_registry
from .spec import ToolPolicyRules

_RULES = {
    s.name: (s.policy_rules.operation, s.policy_rules.plan_allowed, s.policy_rules.action_rule)
    for s in definition_registry().specs()
}


def legacy_policy_rules(name: str, scheduled_names: frozenset[str]) -> ToolPolicyRules:
    spec = definition_registry().get(name)
    if spec is None:
        raise ValueError(f"Legacy tool missing policy review: {name}")
    # Preserve the old helper's caller-selected descriptive modes; it is not an
    # authorization source. Runtime always reads the registered Spec rules.
    modes = ("interactive",)
    if name in scheduled_names:
        modes += ("scheduled",)
        if spec.policy_rules.operation in {"read", "analysis"}:
            modes += ("preflight",)
    return replace(spec.policy_rules, execution_modes=modes)
