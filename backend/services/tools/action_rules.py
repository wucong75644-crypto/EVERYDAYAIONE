"""Pure action lookup; ApiEntry.is_write remains the ERP risk authority."""

from dataclasses import dataclass
from typing import Any, Mapping

from .spec import ToolSpec


@dataclass(frozen=True)
class ActionFacts:
    operation: str
    risk_level: str
    denied_reason: str | None = None


def direct_task_management(context) -> bool:
    """Trusted interactive intent; model arguments cannot enable this path."""
    return bool(context is not None and context.entrypoint == "model"
                and context.execution_mode == "interactive"
                and context.permission_mode != "plan"
                and context.feature_flags.get("scheduled_task_direct_enabled") is True)


def resolve_action(spec: ToolSpec, arguments: Mapping[str, Any], *, context=None) -> ActionFacts:
    rule = spec.policy_rules.action_rule
    operation, risk = spec.policy_rules.operation, spec.risk_level
    if rule is None:
        return ActionFacts(operation, risk)
    action = arguments.get("action")
    if not isinstance(action, str) or not action:
        return ActionFacts(operation, risk, "unknown_action")
    if rule == "scheduled_task":
        # Compatibility callers keep proposal semantics; trusted chat may submit.
        operations = {
            "list": "read", "create": "proposal", "update": "proposal",
            "pause": "proposal", "resume": "proposal", "delete": "proposal",
        }
        if action not in operations:
            return ActionFacts(operation, risk, "unknown_action")
        if action != "list" and direct_task_management(context):
            return ActionFacts("business_write", risk)
        return ActionFacts(operations[action], risk)

    from services.kuaimai.registry import (
        AFTERSALES_REGISTRY, BASIC_REGISTRY, DISTRIBUTION_REGISTRY, PRODUCT_REGISTRY,
        PURCHASE_REGISTRY, TOOL_REGISTRIES, TRADE_REGISTRY, WAREHOUSE_REGISTRY,
    )

    if rule == "erp_write":
        # The category selects the same source registry as the legacy handler.
        categories = {
            "basic": BASIC_REGISTRY, "product": PRODUCT_REGISTRY,
            "trade": TRADE_REGISTRY, "aftersales": AFTERSALES_REGISTRY,
            "warehouse": WAREHOUSE_REGISTRY, "purchase": PURCHASE_REGISTRY,
            "distribution": DISTRIBUTION_REGISTRY,
        }
        category = arguments.get("category")
        registry = categories.get(category) if isinstance(category, str) else None
        if registry is None:
            return ActionFacts(operation, risk, "unknown_erp_category")
    else:
        target = arguments.get("tool") if rule == "erp_raw_read" else spec.handler_key
        registry = TOOL_REGISTRIES.get(target) if isinstance(target, str) else None
    entry = registry.get(action) if registry is not None else None
    if entry is None:
        return ActionFacts(operation, risk, "unknown_erp_action")
    if entry.is_write:
        if rule != "erp_write":
            return ActionFacts("business_write", "dangerous", "erp_query_write_forbidden")
        return ActionFacts("business_write", "dangerous")
    if rule == "erp_write":
        return ActionFacts("read", risk, "erp_execute_requires_write_action")
    return ActionFacts(operation, risk)
