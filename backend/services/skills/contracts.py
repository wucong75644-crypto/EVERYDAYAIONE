"""Immutable control-plane contracts; skill content is never executable here."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


SkillKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
RevisionKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SkillError(ValueError):
    """Stable, secret-free error code for an internal control-plane caller."""


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


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
    status: Literal["published", "retired"]
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


def revision_path(package: SkillPackage | PackageCreate, revision: str) -> str:
    """Ownership is part of the storage namespace, never a caller-supplied path."""
    owner = "platform" if package.org_id is None else f"org/{package.org_id}"
    return f"{owner}/{package.skill_key}/{revision}/SKILL.md"
