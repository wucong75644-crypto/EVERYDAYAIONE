"""Pure call decisions and ordered batches. No IO, execution or confirmation UI.

Adapters must normalize/resolve dangerous targets BEFORE deciding, retain the
binding server-side, and pass only authenticated confirmation outcomes. A binding
is a comparison value, not a signed credential. Recheck current scope/permissions
after confirmation and before cache/replay/dispatch. Runtime owns cancellation,
confirmation lifetime/consumption and execution; this module owns none of them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from .action_rules import direct_task_management, resolve_action
from .context import ToolContext
from .registry import ToolAccessDecision, ToolRegistry
from .spec import ToolSpec, freeze, thaw


def _digest(value: Any) -> str:
    def check(item: Any) -> None:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                check(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                check(child)

    check(value)
    raw = json.dumps(thaw(freeze(value)), sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConfirmationBinding:
    tool_name: str
    call_id: str
    arguments_digest: str
    scope_digest: str
    spec_digest: str


@dataclass(frozen=True)
class ToolConfirmation:
    """Trusted adapter output, never deserialized from model/client tool args."""

    binding: ConfirmationBinding
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.binding, ConfirmationBinding) or self.status not in {"approved", "rejected"}:
            raise ValueError("Invalid trusted confirmation")


@dataclass(frozen=True)
class ToolDecision:
    outcome: str
    reason: str
    risk_level: str
    operation: str
    parallelizable: bool
    cacheable: bool
    effects: tuple[str, ...]
    replay_requirement: str
    confirmation_binding: ConfirmationBinding | None = None


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.call_id, self.name)):
            raise ValueError("ToolCall requires call_id and name")
        if not isinstance(self.arguments, Mapping):
            raise ValueError("ToolCall requires normalized object arguments")
        _digest(self.arguments)
        object.__setattr__(self, "arguments", freeze(self.arguments))


@dataclass(frozen=True)
class PlannedToolCall:
    call: ToolCall
    decision: ToolDecision


class ToolPolicy:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _mode_reason(spec: ToolSpec, context: ToolContext, operation: str, risk: str) -> str | None:
        rules = spec.policy_rules
        if operation == "unknown":
            return "unreviewed_operation"
        if context.permission_mode == "plan" and (
            not rules.plan_allowed or operation in {"business_write", "generation"} or risk == "dangerous"
        ):
            return "plan_forbidden"
        if context.execution_mode not in rules.execution_modes:
            return "execution_mode_forbidden"
        if context.execution_mode == "preflight" and (
            operation not in {"read", "analysis"} or risk == "dangerous"
        ):
            return "preflight_forbidden"
        # Existing scheduled snapshots contain names, not dangerous target grants.
        # Supporting unattended dangerous writes requires a separately reviewed contract.
        if context.execution_mode == "scheduled" and risk == "dangerous":
            return "scheduled_dangerous_forbidden"
        return None

    def resolve_access(self, spec: ToolSpec, context: ToolContext) -> ToolAccessDecision:
        reason = self._mode_reason(spec, context, spec.policy_rules.operation, spec.risk_level)
        if reason is not None:
            return ToolAccessDecision(False, reason)
        snapshot = context.authorization_snapshot
        if snapshot.get("access_denied_reason"):
            return ToolAccessDecision(False, str(snapshot["access_denied_reason"]))
        if snapshot and snapshot.get("version", 1) != 1:
            return ToolAccessDecision(False, "execution_authorization_version_invalid")
        if "allowed_tools" in snapshot:
            names = snapshot["allowed_tools"]
            if (not isinstance(names, (list, tuple))
                    or any(not isinstance(name, str) or not name for name in names)):
                return ToolAccessDecision(False, "execution_authorization_required")
            if spec.name not in names:
                return ToolAccessDecision(False, "outside_execution_authorization")
        if context.execution_mode != "interactive":
            names = snapshot.get("allowed_tools")
            if (context.task_id is None or context.authorized_tool_names is None
                    or not isinstance(names, (list, tuple))
                    or any(not isinstance(name, str) or not name for name in names)):
                return ToolAccessDecision(False, "execution_authorization_required")
            if spec.name not in names:
                return ToolAccessDecision(False, "outside_execution_authorization")
        permissions = snapshot.get("permissions", {})
        for code in spec.policy_rules.required_permissions:
            if not isinstance(permissions, Mapping) or permissions.get(code) is not True:
                return ToolAccessDecision(False, f"business_permission_required:{code}")
        return ToolAccessDecision(True, "eligible")

    @staticmethod
    def _binding(spec: ToolSpec, context: ToolContext, arguments_digest: str) -> ConfirmationBinding | None:
        if context.call_id is None or (context.conversation_id is None and context.task_id is None):
            return None
        scope = {
            name: getattr(context, name) for name in (
                "actor_user_id", "workspace_owner_id", "org_id", "context_scope",
                "personal_context_allowed", "agent_domain", "permission_mode", "execution_mode",
                "entrypoint", "conversation_id", "task_id", "authorization_snapshot",
                "feature_flags", "resource_manifest", "resource_versions", "resource_access",
            )
        }
        scope["authorized_tool_names"] = (
            None if context.authorized_tool_names is None else sorted(context.authorized_tool_names)
        )
        rules = spec.policy_rules
        definition = {
            "schema": spec.schema, "handler": spec.handler_key, "executor": spec.executor_type,
            "risk": spec.risk_level, "effects": spec.effects, "replay": spec.replay_requirement,
            "operation": rules.operation, "plan": rules.plan_allowed, "modes": rules.execution_modes,
            "action_rule": rules.action_rule, "permissions": rules.required_permissions,
        }
        return ConfirmationBinding(spec.name, context.call_id, arguments_digest, _digest(scope), _digest(definition))

    def decide(
        self, name: str, context: ToolContext, arguments: Mapping[str, Any], *,
        confirmation: ToolConfirmation | None = None,
    ) -> ToolDecision:
        """Resolve the canonical Spec; arbitrary supplied Specs cannot grant access.

        Names in allowed/advertised, task_id, auto, JSON approval fields and cache
        eligibility are never approvals. Exceptions propagate without a fallback.
        """
        spec = self.registry.get(name)
        access = self.registry.check_access(name, context, policy=self)
        operation = spec.policy_rules.operation if spec else "unknown"
        risk = spec.risk_level if spec else "unknown"

        def result(outcome: str, reason: str, binding: ConfirmationBinding | None = None) -> ToolDecision:
            parallel = bool(
                spec and outcome == "allow" and spec.parallelizable
                and operation in {"read", "analysis"} and risk != "dangerous"
            )
            return ToolDecision(
                outcome, reason, risk, operation, parallel,
                bool(spec and spec.cacheable and not (
                    spec.policy_rules.action_rule == "scheduled_task" and direct_task_management(context)
                )), spec.effects if spec else ("unknown",),
                spec.replay_requirement if spec else "unspecified", binding,
            )

        if not access.allowed:
            return result("deny", access.reason)
        try:
            if not isinstance(arguments, Mapping):
                raise ValueError("Normalized arguments must be an object")
            arguments_digest = _digest(arguments)
        except (TypeError, ValueError):
            return result("deny", "invalid_normalized_arguments")
        facts = resolve_action(spec, arguments, context=context)
        operation, risk = facts.operation, facts.risk_level
        if facts.denied_reason:
            return result("deny", facts.denied_reason)
        # A dangerous action-bearing Spec needs reviewed action facts or an enum.
        props = spec.schema["function"]["parameters"]["properties"] if spec.schema else {}
        if spec.policy_rules.action_rule is None and "action" in props:
            choices = props["action"].get("enum")
            if choices is not None and arguments.get("action") not in choices:
                return result("deny", "unknown_action")
            if choices is None and risk == "dangerous":
                return result("deny", "unreviewed_dangerous_action")
        reason = self._mode_reason(spec, context, operation, risk)
        if reason:
            return result("deny", reason)
        from .resource_access import FILE_ACTIONS, ResourceAccessBoundary
        if name in FILE_ACTIONS and context.resource_access:
            boundary = ResourceAccessBoundary.from_dict(context.resource_access)
            if boundary.unavailable_reason:
                return result("deny", boundary.unavailable_reason)
            if not boundary.permits(FILE_ACTIONS[name]):
                return result("deny", "RESOURCE_ACTION_DENIED")
        if risk != "dangerous":
            return result("allow", "resource_notice" if risk == "confirm" else "allowed")
        binding = self._binding(spec, context, arguments_digest)
        if binding is None:
            return result("deny", "confirmation_scope_required")
        if not context.confirmation_available:
            return result("deny", "confirmation_unavailable", binding)
        if confirmation is None:
            return result("require_confirmation", "dangerous_confirmation_required", binding)
        if not isinstance(confirmation, ToolConfirmation):
            return result("deny", "invalid_confirmation", binding)
        if confirmation.binding != binding:
            return result("deny", "confirmation_binding_mismatch", binding)
        if confirmation.status != "approved":
            return result("deny", "confirmation_rejected", binding)
        return result("allow", "confirmed", binding)

    def plan_batches(
        self, calls: Iterable[ToolCall], context: ToolContext, *,
        confirmations: Mapping[str, ToolConfirmation] | None = None,
    ) -> tuple[tuple[PlannedToolCall, ...], ...]:
        """Contiguous parallel calls only. Denied/pending calls remain barriers.

        The consumer must inspect every decision; batches are not permission to
        execute. No waiting, locks, tasks, cache reads or handler invocations occur.
        """
        calls = tuple(calls)
        if len({call.call_id for call in calls}) != len(calls):
            raise ValueError("Duplicate tool call_id")
        batches: list[tuple[PlannedToolCall, ...]] = []
        pending: list[PlannedToolCall] = []
        for call in calls:
            decision = self.decide(
                call.name, replace(context, call_id=call.call_id), call.arguments,
                confirmation=(confirmations or {}).get(call.call_id),
            )
            planned = PlannedToolCall(call, decision)
            if decision.parallelizable:
                pending.append(planned)
            else:
                if pending:
                    batches.append(tuple(pending))
                    pending = []
                batches.append((planned,))
        if pending:
            batches.append(tuple(pending))
        return tuple(batches)
