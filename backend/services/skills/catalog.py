"""Feature-gated internal control plane. No public API or execution integration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from services.skills.contracts import (
    PackageCreate, PublishRevision, SkillAssignment, SkillError, SkillPackage,
    SkillRevision, ValidatedSkill,
)
from services.skills.repository import SkillRepository
from services.skills.storage import SkillStorage

if TYPE_CHECKING:
    from core.config import Settings


class SkillCatalog:
    def __init__(self, repository: SkillRepository, settings: Settings):
        self._repository = repository
        self._settings = settings

    def _require_enabled(self):
        if self._settings.skill_catalog_enabled is not True:
            raise SkillError("SKILL_CATALOG_DISABLED")

    def _storage(self) -> SkillStorage:
        return SkillStorage(self._settings.skill_storage_root,
                            workspace_root=self._settings.file_workspace_root)

    def create_package(self, package: PackageCreate) -> SkillPackage:
        self._require_enabled()
        return self._repository.create_package(package)

    def list_packages(self) -> list[SkillPackage]:
        self._require_enabled()
        return self._repository.list_packages()

    def publish_revision(self, package_id: UUID, publication: PublishRevision) -> SkillRevision:
        self._require_enabled()
        # Check authority before reading a package, including platform packages.
        package = self._repository.get_owned_package(package_id)
        validated = self._storage().validate(package, publication)
        return self._repository.publish_revision(package_id, validated)

    def set_assignment(
        self, package_id: UUID, revision_id: UUID, *, enabled: bool = False, priority: int = 0,
    ) -> SkillAssignment:
        self._require_enabled()
        return self._repository.set_assignment(package_id, revision_id, enabled=enabled, priority=priority)

    def retire_revision(self, package_id: UUID, revision_id: UUID) -> SkillRevision:
        self._require_enabled()
        return self._repository.retire_revision(package_id, revision_id)

    def enabled_revisions(self) -> list[SkillRevision]:
        self._require_enabled()
        return self._repository.enabled_revisions()

    def read_assigned_skill(self, package_id: UUID) -> ValidatedSkill:
        self._require_enabled()
        revisions = self._repository.enabled_revisions(package_id)
        if not revisions:
            raise SkillError("SKILL_ASSIGNMENT_DISABLED_OR_UNAVAILABLE")
        revision = revisions[0]
        package = self._repository.get_package(package_id)
        publication = PublishRevision(revision=revision.revision,
            content_sha256=revision.content_sha256, body_sha256=revision.body_sha256)
        return self._storage().validate(package, publication, nas_path=revision.nas_path)
