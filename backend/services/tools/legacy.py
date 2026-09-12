"""Compatibility catalog API and presentation adapter; no independent policy or definitions."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from .context import ToolContext
from .registry import ToolRegistry
from .spec import Exposure, ToolAvailability, ToolSpec


def __getattr__(name: str):
    """Preserve old diagnostic imports without restoring metadata ownership."""
    if name == "legacy_policy_rules":
        from .legacy_policy import legacy_policy_rules
        return legacy_policy_rules
    if name not in {"_INTERNAL_SOURCES", "_COMPATIBILITY", "_EFFECTS"}:
        raise AttributeError(name)
    from .catalog import build_tool_catalog
    specs = build_tool_catalog().specs()
    if name == "_COMPATIBILITY":
        return {s.name: s.compatibility_notes for s in specs if s.compatibility_notes}
    if name == "_EFFECTS":
        # Original diagnostic subset, not a source used by execution.
        selected = {"code_execute", "file_analyze", "restore_file", "manage_scheduled_task",
                    "fetch_all_pages", "get_conversation_context"}
        return {s.name: s.effects for s in specs if s.name in selected}
    return {s.name: ("config.erp_tools.build_fetch_all_pages_tool" if s.name == "fetch_all_pages"
                     else "services.agent.conversation_tool_mixin.ConversationToolMixin")
            for s in specs if s.exposure is Exposure.LEGACY_INTERNAL}


class LegacyAdvertisement:
    """Reuse current core/plan display rules, then intersect in the Registry.

    ERP consumers can supply their existing initial tool selection explicitly.
    Discovered names affect presentation only; even arbitrary names cannot grant
    access. Scheduled/preflight callers must supply their frozen display names.
    """

    def __init__(self, initial_names: Iterable[str] | None = None) -> None:
        self.initial_names = None if initial_names is None else frozenset(initial_names)

    def names(self, context: ToolContext, discovered_names: frozenset[str]) -> frozenset[str]:
        from config.chat_tools import get_tools_for_mode

        if self.initial_names is not None:
            initial = self.initial_names
        elif context.execution_mode == "interactive" and context.agent_domain == "general":
            initial = frozenset(
                tool["function"]["name"]
                for tool in get_tools_for_mode(context.permission_mode, context.org_id)
            )
        else:
            initial = frozenset()
        # Current scheduled/preflight loops do not dynamically expand tools.
        if context.execution_mode != "interactive":
            return initial
        return initial | discovered_names


def build_legacy_catalog() -> ToolRegistry:
    """Compatibility name for the complete Spec-owned catalog."""
    from .catalog import build_tool_catalog
    return build_tool_catalog()


def validate_legacy_coverage(
    registry: ToolRegistry, *, public_schemas: Iterable[Mapping], handler_names: Iterable[str],
) -> tuple[str, ...]:
    """Compare a full (organization) directory by name AND entire schema.

    Caller supplies actual legacy definitions/handler keys. This makes drift and
    missing bindings testable without creating a business executor at import time.
    """
    issues: list[str] = []
    schemas = list(public_schemas)
    names = [schema.get("function", {}).get("name") for schema in schemas]
    for name, count in Counter(names).items():
        if count > 1:
            issues.append(f"duplicate_schema:{name}")
    public_names = set(names)
    specs = {spec.name: spec for spec in registry.specs()}
    declared_public = {name for name, spec in specs.items() if spec.exposure is Exposure.PUBLIC}
    for name in sorted(public_names - declared_public, key=str):
        issues.append(f"missing_public_spec:{name}")
    for name in sorted(declared_public - public_names):
        issues.append(f"unexpected_public_spec:{name}")
    for schema, name in zip(schemas, names):
        if name in specs and specs[name].to_schema() != schema:
            issues.append(f"schema_mismatch:{name}")
    handlers = set(handler_names)
    for name, spec in specs.items():
        if spec.handler_key not in handlers:
            issues.append(f"missing_handler:{name}:{spec.handler_key}")
    bound_handlers = {spec.handler_key for spec in specs.values()}
    for name in sorted(handlers - bound_handlers):
        issues.append(f"missing_handler_spec:{name}")
    return tuple(issues)
