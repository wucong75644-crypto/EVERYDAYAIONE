"""Read existing schema factories/metadata without changing their ownership.

All legacy imports are lazy. In particular common_tools builds its ERPAgent
summary at runtime; nothing in the old import graph imports services.tools.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from .context import ToolContext
from .registry import ToolRegistry
from .spec import Exposure, ToolAvailability, ToolSpec


# The two handlers absent from get_chat_tools. fetch_all_pages already has an
# internal factory; get_conversation_context has only a partial validation schema.
_INTERNAL_SOURCES = {
    "fetch_all_pages": "config.erp_tools.build_fetch_all_pages_tool",
    "get_conversation_context": "services.agent.conversation_tool_mixin.ConversationToolMixin",
}
_COMPATIBILITY = {
    "erp_agent": ("query -> task in existing validator and handler; task takes precedence",),
    "erp_analyze": ("query fallback exists in handler only; validator still uses public task schema",),
    "file_analyze": ("file_id preferred; legacy path and scope retained",),
    "file_search": ("scope defaults to current; workspace explicitly selects workspace search",),
    "file_delete": (
        "file_ids and legacy files retained; handler accepts string or list",
        "when both are given, resolved file_ids are appended to files; do not replace this behavior",
    ),
    "local_data": ("handler extra_fields falls back to fields; no new public field is added",),
    "code_execute": ("legacy cache eligibility retained independently; kernel state is not read-only",),
    "restore_file": ("legacy safe risk and serial scheduling retained despite workspace writes",),
}
# Effects are independent observations, not derived from risk/concurrency/cache.
# Unreviewed legacy business effects remain explicitly unknown for later batches.
_EFFECTS = {
    "code_execute": ("kernel_state", "workspace_artifacts"),
    "file_analyze": ("workspace_artifacts", "file_index"),
    "restore_file": ("workspace_write", "deletion_record"),
    "manage_scheduled_task": ("proposal_or_form",),
    "fetch_all_pages": ("workspace_artifacts", "file_index"),
    "get_conversation_context": ("none",),
}


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
    """Build 3 explicit specs plus adapters for every existing public/internal tool.

    No request identities, settings reads, DB clients, or ToolExecutors are held.
    Factory output remains authoritative, including every description/parameter.
    """
    from config.chat_tools import get_safety_level, is_concurrency_safe
    from config.agent_tools import TOOL_SCHEMAS
    from config.code_tools import build_code_tools
    from config.common_tools import build_common_tools
    from config.crawler_tools import build_crawler_tools
    from config.erp_tools import build_erp_tools, build_fetch_all_pages_tool
    from config.file_tools import build_file_tools
    from config.tool_domains import TOOL_DOMAINS
    from services.agent.tool_result_cache import ToolResultCache

    registry = ToolRegistry()
    families = (
        ("config.erp_tools.build_erp_tools", build_erp_tools(), True, ()),
        ("config.crawler_tools.build_crawler_tools", build_crawler_tools(), False, ("crawler_enabled",)),
        ("config.file_tools.build_file_tools", build_file_tools(), False, ("file_workspace_enabled",)),
        ("config.code_tools.build_code_tools", build_code_tools(include_workspace=True), False, ("sandbox_enabled",)),
        ("config.common_tools.build_common_tools", build_common_tools(), False, ()),
    )
    explicit = {
        "search_knowledge": ("safe", True, True, ("none",)),
        "file_search": ("safe", True, True, ("file_index",)),
        "file_delete": ("dangerous", False, False, ("workspace_delete", "deletion_record")),
    }
    for source, schemas, requires_org, flags in families:
        for schema in schemas:
            name = schema["function"]["name"]
            if name not in TOOL_DOMAINS:
                raise ValueError(f"Legacy tool missing domain: {name}")
            if name in explicit:
                risk, parallel, cacheable, effects = explicit[name]
            else:
                risk = get_safety_level(name).value
                parallel = is_concurrency_safe(name)
                cacheable = ToolResultCache.is_cacheable(name)
                effects = _EFFECTS.get(name, ("unknown",))
            registry.register(ToolSpec(
                name=name, schema=schema, domain=TOOL_DOMAINS[name].value,
                availability=ToolAvailability(
                    requires_org=requires_org or name == "manage_scheduled_task",
                    requires_personal_context=name == "manage_scheduled_task",
                    feature_flags=flags,
                ),
                risk_level=risk, parallelizable=parallel, cacheable=cacheable,
                effects=effects, executor_type="legacy", handler_key=name,
                exposure=Exposure.PUBLIC, source=source,
                definition_kind="explicit" if name in explicit else "legacy",
                compatibility_notes=_COMPATIBILITY.get(name, ()),
                legacy_validation_schema=TOOL_SCHEMAS.get(name),
            ))
    for name, source in _INTERNAL_SOURCES.items():
        registry.register(ToolSpec(
            name=name,
            schema=build_fetch_all_pages_tool() if name == "fetch_all_pages" else None,
            domain=TOOL_DOMAINS[name].value if name == "fetch_all_pages" else "general",
            availability=ToolAvailability(
                requires_org=name == "fetch_all_pages",
                requires_personal_context=name == "get_conversation_context",
            ),
            risk_level=get_safety_level(name).value,
            parallelizable=is_concurrency_safe(name),
            cacheable=ToolResultCache.is_cacheable(name), effects=_EFFECTS[name],
            executor_type="legacy", handler_key=name, exposure=Exposure.LEGACY_INTERNAL,
            source=source,
            legacy_validation_schema=TOOL_SCHEMAS.get(name),
        ))
    return registry


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
