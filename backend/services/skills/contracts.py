"""Immutable control-plane contracts; skill content is never executable here."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal, TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_serializer, model_validator

if TYPE_CHECKING:
    from services.skills.assets import SkillResources


def _empty_resources():
    from services.skills.assets import SkillResources
    return SkillResources()


SkillKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
RevisionKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SkillError(ValueError):
    """Stable, secret-free error code for an internal control-plane caller."""


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


CapabilityKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")]
CatalogText = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
ConversationScope = Literal["user", "channel"]
AgentDomain = Literal["general", "erp"]
ExecutionMode = Literal["interactive", "scheduled", "preflight"]
SkillFileType = Literal["pdf", "docx", "xlsx", "csv", "pptx", "image", "text"]
SkillTaskMode = Literal['smart', 'image-i2i', 'image-t2i', 'image-ecom', 'video']


class SkillCapabilityStatus(Contract):
    capability: CapabilityKey
    required: StrictBool
    available: StrictBool


class SkillCatalogMetadata(Contract):
    """Published frontmatter `catalog`; immutable with its revision.

    Legacy metadata keeps its explicit tool ceiling. New authoring can opt into
    platform tools; this never grants access or overrides ToolPolicy.
    """

    name: CatalogText | None = None
    triggers: tuple[CatalogText, ...] = Field(default=(), max_length=32)
    model_selectable: StrictBool = False
    conversation_scopes: tuple[ConversationScope, ...] = ("user",)
    agent_domains: tuple[AgentDomain, ...] = ("general",)
    execution_modes: tuple[ExecutionMode, ...] = ("interactive",)
    task_modes: tuple[SkillTaskMode, ...] = Field(default=('smart',), min_length=1, max_length=5)
    actor_user_ids: tuple[UUID, ...] = ()
    required_permissions: tuple[CatalogText, ...] = ()
    required_feature_flags: tuple[CatalogText, ...] = ()
    allowed_tool_names: tuple[CatalogText, ...] = ()
    required_capabilities: tuple[CapabilityKey, ...] = Field(default=(), max_length=64)
    allowed_capabilities: tuple[CapabilityKey, ...] = Field(default=(), max_length=64)
    recommended_file_types: tuple[SkillFileType, ...] = Field(default=(), max_length=7)
    tool_policy: Literal['restricted', 'platform'] = 'restricted'

    @model_validator(mode='after')
    def unambiguous_tool_policy(self):
        if self.allowed_capabilities and not set(self.required_capabilities) <= set(self.allowed_capabilities):
            raise ValueError('SKILL_CAPABILITY_CONFLICT')
        if self.tool_policy == 'platform' and self.allowed_tool_names:
            raise ValueError('SKILL_TOOL_POLICY_CONFLICT')
        return self

    @model_serializer(mode='wrap')
    def preserve_legacy_metadata(self, handler):
        result = handler(self)
        for key in ('required_capabilities', 'allowed_capabilities'):
            if not getattr(self, key):
                result.pop(key, None)
        # Keep old review hashes and checkpoint metadata byte-compatible.
        if not self.recommended_file_types:
            result.pop('recommended_file_types', None)
        if self.task_modes == ('smart',):
            result.pop('task_modes', None)
        if self.tool_policy == 'restricted':
            result.pop('tool_policy', None)
        return result


class PackageCreate(Contract):
    skill_key: SkillKey
    source: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    scope_kind: Literal["platform", "org", "personal"]
    org_id: UUID | None = None
    owner_user_id: UUID | None = None

    @model_validator(mode="after")
    def validate_owner(self):
        if self.scope_kind == "org":
            valid = self.org_id is not None and self.owner_user_id is None
        elif self.scope_kind == "personal":
            valid = self.org_id is None and self.owner_user_id is not None
        else:
            valid = self.org_id is None and self.owner_user_id is None
        if not valid:
            raise ValueError("SKILL_OWNER_INVALID")
        return self


class SkillPackage(PackageCreate):
    id: UUID
    created_at: datetime


class PublishRevision(Contract):
    revision: RevisionKey
    content_sha256: Sha256  # Entire SKILL.md, including frontmatter, as stored.
    body_sha256: Sha256  # Exact UTF-8 bytes after the closing frontmatter line.


class SkillRevision(PublishRevision):
    reviewed: bool = False
    id: UUID
    package_id: UUID
    nas_path: str  # Canonical relative SKILL.md path under SKILL_STORAGE_ROOT.
    summary: str
    catalog_metadata: SkillCatalogMetadata = Field(default_factory=SkillCatalogMetadata)
    status: Literal["published", "deprecated", "disabled", "retired"]
    created_at: datetime


class SkillAssignment(Contract):
    org_id: UUID | None
    package_id: UUID
    revision_id: UUID
    enabled: bool
    priority: int
    updated_at: datetime


class ActivationAuditCreate(Contract):
    package_id: UUID
    revision_id: UUID
    outcome: Literal["activated", "skipped", "failed"]
    reason_code: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")]
    conversation_id: UUID | None = None
    turn_id: UUID | None = None


@dataclass(frozen=True)
class ValidatedSkill:
    nas_path: str
    skill_key: str
    revision: str
    content_sha256: str
    body_sha256: str
    summary: str
    body: str
    catalog_metadata: SkillCatalogMetadata = field(default_factory=SkillCatalogMetadata)
    resources: 'SkillResources' = field(default_factory=_empty_resources)


def revision_path(package: SkillPackage | PackageCreate, revision: str) -> str:
    """Ownership is part of the storage namespace, never a caller-supplied path."""
    owner = ("platform" if package.scope_kind == "platform" else
             f"personal/{package.owner_user_id}" if package.scope_kind == "personal" else
             f"org/{package.org_id}")
    return f"{owner}/{package.skill_key}/{revision}/SKILL.md"
