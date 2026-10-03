"""Summary-only resolution from server-authorized facts. No storage or tools IO."""

from collections.abc import Iterable
from typing import Literal
from uuid import UUID
from pydantic import model_serializer

from services.skills.contracts import (
    AgentDomain, Contract, ConversationScope, ExecutionMode, RevisionKey,
    SkillCapabilityStatus, SkillCatalogMetadata, SkillKey, SkillTaskMode,
)


class SkillSummary(Contract):
    """The complete public allowlist; never serialize a package/revision row."""

    skill_id: SkillKey
    name: str
    revision: RevisionKey
    description: str
    triggers: tuple[str, ...]
    source: Literal["platform", "org"]
    model_selectable: bool
    task_modes: tuple[SkillTaskMode, ...] = ('smart',)
    capability_status: tuple[SkillCapabilityStatus, ...] = ()

    @model_serializer(mode="wrap")
    def omit_empty_capability_status(self, handler):
        result = handler(self)
        if not self.capability_status:
            result.pop("capability_status", None)
        return result


class SkillResolutionContext(Contract):
    """Internal snapshot, not an HTTP input or an authentication mechanism."""

    actor_user_id: UUID
    org_id: UUID | None
    conversation_scope: ConversationScope
    agent_domain: AgentDomain
    execution_mode: ExecutionMode
    task_mode: SkillTaskMode = 'smart'
    permissions: frozenset[str] = frozenset()
    enabled_feature_flags: frozenset[str] = frozenset()
    # Production adapters pass the current organization's capability set.
    # None preserves scope-only callers and older test fixtures.
    available_capabilities: frozenset[str] | None = None


class SkillCandidate(Contract):
    """Repository projection: no paths, hashes, body, or free-form package source."""

    package_id: UUID
    skill_key: SkillKey
    package_org_id: UUID | None
    assignment_org_id: UUID
    priority: int
    revision: RevisionKey
    description: str
    scope_kind: Literal["platform", "org"]
    catalog_metadata: SkillCatalogMetadata


class SkillResolver:
    def resolve(
        self, context: SkillResolutionContext, candidates: Iterable[SkillCandidate],
    ) -> list[SkillSummary]:
        return [self.summary(context, c) for c in self.select(context, candidates)]

    @staticmethod
    def summary(context: SkillResolutionContext, candidate: SkillCandidate) -> SkillSummary:
        metadata = candidate.catalog_metadata
        required = set(metadata.required_capabilities)
        capabilities = sorted(required | set(metadata.allowed_capabilities))
        available = context.available_capabilities
        return SkillSummary(
            skill_id=candidate.skill_key, name=metadata.name or candidate.skill_key,
            revision=candidate.revision, description=candidate.description,
            triggers=metadata.triggers, source=candidate.scope_kind,
            model_selectable=metadata.model_selectable, task_modes=metadata.task_modes,
            capability_status=tuple(SkillCapabilityStatus(
                capability=capability, required=capability in required,
                available=(available is None or capability in available),
            ) for capability in capabilities),
        )

    def select(
        self, context: SkillResolutionContext, candidates: Iterable[SkillCandidate],
    ) -> list[SkillCandidate]:
        """Resolve identities from server-owned scope and permissions."""
        if "skill_catalog_enabled" not in context.enabled_feature_flags or context.org_id is None:
            return []
        eligible = []
        for candidate in candidates:
            metadata = candidate.catalog_metadata
            if candidate.assignment_org_id != context.org_id:
                continue
            if candidate.scope_kind == "org" and candidate.package_org_id != context.org_id:
                continue
            if candidate.scope_kind == "platform" and candidate.package_org_id is not None:
                continue
            if (context.conversation_scope not in metadata.conversation_scopes
                    or context.task_mode not in metadata.task_modes
                    or context.agent_domain not in metadata.agent_domains
                    or context.execution_mode not in metadata.execution_modes
                    or (metadata.actor_user_ids and context.actor_user_id not in metadata.actor_user_ids)
                    or not set(metadata.required_permissions) <= context.permissions
                    or not set(metadata.required_feature_flags) <= context.enabled_feature_flags):
                continue
            eligible.append(candidate)
        # Explicit assignment priority wins; organization wins ties. Display names
        # never determine identity, and no package/file is modified by resolution.
        eligible.sort(key=lambda c: (-c.priority, c.scope_kind != "org", c.skill_key, str(c.package_id)))
        visible = {}
        for candidate in eligible:
            if candidate.skill_key in visible:
                continue
            visible[candidate.skill_key] = candidate
        return list(visible.values())


def effective_allowed_tool_names(
    platform_tool_names: Iterable[str], authorized_tool_names: Iterable[str] | None,
    skill_allowed_tool_names: Iterable[str] | None,
) -> frozenset[str]:
    """Pure narrowing only. Missing authorization/declaration means the empty set.

    This is not a ToolPolicy decision, execution grant, or wildcard expansion.
    """
    def names(values):
        if values is None:
            return frozenset()
        if isinstance(values, (str, bytes)):
            raise ValueError("Tool names must be a collection")
        result = frozenset(values)
        if any(not isinstance(name, str) or not name.strip() for name in result):
            raise ValueError("Invalid tool name")
        return result

    return names(platform_tool_names) & names(authorized_tool_names) & names(skill_allowed_tool_names)


def skill_tool_ceiling(metadata: SkillCatalogMetadata, platform_tool_names, current_ceiling, *, registry=None,
                       available_tool_names=None):
    """Select the revision's contract, then narrow the existing host ceiling."""
    from services.skills.contracts import SkillError
    if metadata.required_capabilities or metadata.allowed_capabilities:
        if registry is None:
            from services.tools.catalog import build_capability_catalog
            registry = build_capability_catalog()
        try:
            required = registry.capability_tools(metadata.required_capabilities)
            allowed = registry.capability_tools(metadata.allowed_capabilities or metadata.required_capabilities)
        except ValueError:
            raise SkillError("SKILL_UNKNOWN_CAPABILITY") from None
        # A capability declaration may replace the legacy name declaration, but
        # simultaneous declarations intersect; neither can expand the host scope.
        declared = allowed
        if metadata.allowed_tool_names:
            declared &= frozenset(metadata.allowed_tool_names)
        ceiling = effective_allowed_tool_names(platform_tool_names, current_ceiling, declared)
        if available_tool_names is not None:
            ceiling &= effective_allowed_tool_names(platform_tool_names, available_tool_names, platform_tool_names)
        if not required <= ceiling:
            raise SkillError("SKILL_REQUIRED_CAPABILITY_UNAVAILABLE")
        return ceiling
    declared = platform_tool_names if metadata.tool_policy == 'platform' else metadata.allowed_tool_names
    return effective_allowed_tool_names(platform_tool_names, current_ceiling, declared)
