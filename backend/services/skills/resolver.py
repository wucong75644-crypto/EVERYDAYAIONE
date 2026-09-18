"""Summary-only resolution from server-authorized facts. No storage or tools IO."""

from collections.abc import Iterable
from typing import Literal
from uuid import UUID

from services.skills.contracts import (
    AgentDomain, Contract, ConversationScope, ExecutionMode, RevisionKey,
    SkillCatalogMetadata, SkillKey,
)


class SkillSummary(Contract):
    """The complete public allowlist; never serialize a package/revision row."""

    name: str
    revision: RevisionKey
    description: str
    triggers: tuple[str, ...]
    source: Literal["platform", "org"]
    model_selectable: bool


class SkillResolutionContext(Contract):
    """Internal snapshot, not an HTTP input or an authentication mechanism."""

    actor_user_id: UUID
    org_id: UUID | None
    conversation_scope: ConversationScope
    agent_domain: AgentDomain
    execution_mode: ExecutionMode
    permissions: frozenset[str] = frozenset()
    enabled_feature_flags: frozenset[str] = frozenset()


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
        return [SkillSummary(
            name=c.catalog_metadata.name or c.skill_key, revision=c.revision,
            description=c.description, triggers=c.catalog_metadata.triggers,
            source=c.scope_kind, model_selectable=c.catalog_metadata.model_selectable,
        ) for c in self.select(context, candidates)]

    def select(
        self, context: SkillResolutionContext, candidates: Iterable[SkillCandidate],
    ) -> list[SkillCandidate]:
        """Internal identities for the Actor; public summaries remain unchanged."""
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
