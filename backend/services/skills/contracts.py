"""Immutable control-plane contracts; skill content is never executable here."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal, TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

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


CatalogText = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
ConversationScope = Literal["user", "channel"]
AgentDomain = Literal["general", "erp"]
ExecutionMode = Literal["interactive", "scheduled", "preflight"]


class SkillCatalogMetadata(Contract):
    """Published frontmatter `catalog`; immutable with its revision.

    Missing declarations allow no tools or model selection. Empty actor/permission
    restrictions mean all authorized members of the assigned organization.
    """

    name: CatalogText | None = None
    triggers: tuple[CatalogText, ...] = Field(default=(), max_length=32)
    model_selectable: StrictBool = False
    conversation_scopes: tuple[ConversationScope, ...] = ("user",)
    agent_domains: tuple[AgentDomain, ...] = ("general",)
    execution_modes: tuple[ExecutionMode, ...] = ("interactive",)
    actor_user_ids: tuple[UUID, ...] = ()
    required_permissions: tuple[CatalogText, ...] = ()
    required_feature_flags: tuple[CatalogText, ...] = ()
    allowed_tool_names: tuple[CatalogText, ...] = ()


class PackageCreate(Contract):
    skill_key: SkillKey
    source: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    scope_kind: Literal["platform", "org"]
    org_id: UUID | None = None

    @model_validator(mode="after")
    def validate_owner(self):
        if (self.scope_kind == "org") != (self.org_id is not None):
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
    id: UUID
    package_id: UUID
    nas_path: str  # Canonical relative SKILL.md path under SKILL_STORAGE_ROOT.
    summary: str
    catalog_metadata: SkillCatalogMetadata = Field(default_factory=SkillCatalogMetadata)
    status: Literal["published", "deprecated", "disabled", "retired"]
    created_at: datetime


class SkillAssignment(Contract):
    org_id: UUID
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
    owner = "platform" if package.org_id is None else f"org/{package.org_id}"
    return f"{owner}/{package.skill_key}/{revision}/SKILL.md"
