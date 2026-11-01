"""Inspect the existing registry; never registers tools or grants execution."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityAdmission:
    tool_name: str
    action_rule: str | None
    modes: tuple[str, ...]
    effects: tuple[str, ...]
    replay_requirement: str
    risk: str
    parallelizable: bool
    status: str
    reason: str


def inventory(registry, *, verified_actions=frozenset()):
    """Admission is explicit per (name, action-rule), not inferred from tool names.

    Entries stay on their original runtime path until an adapter's evidence,
    scope, confirmation, and recovery have actually been verified.
    """
    result = []
    for spec in registry.specs():
        key = (spec.name, spec.policy_rules.action_rule)
        verified = key in verified_actions
        result.append(CapabilityAdmission(
            tool_name=spec.name, action_rule=spec.policy_rules.action_rule,
            modes=spec.policy_rules.execution_modes, effects=spec.effects,
            replay_requirement=spec.replay_requirement, risk=spec.risk_level,
            parallelizable=spec.parallelizable,
            status='verified' if verified else 'legacy_only',
            reason='adapter_verified' if verified else 'adapter_not_verified',
        ))
    return tuple(result)
