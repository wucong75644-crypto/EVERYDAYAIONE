"""Admin inputs never carry a storage path, authority, revision or review hash."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field
import yaml

from services.skills.contracts import Contract, PublishRevision, SkillCatalogMetadata, SkillKey
from services.skills.storage import SkillStorage


class DraftContent(Contract):
    description: str = Field(default='', max_length=2000)
    body: str = Field(default='', max_length=1_000_000)
    catalog_metadata: SkillCatalogMetadata = Field(default_factory=SkillCatalogMetadata)


class CreateSkill(Contract):
    skill_key: SkillKey
    content: DraftContent = Field(default_factory=DraftContent)


class ExpectedVersion(Contract):
    expected_version: Annotated[int, Field(strict=True, ge=0)]


class SaveDraft(ExpectedVersion):
    content: DraftContent


class TransitionDraft(ExpectedVersion):
    action: Literal['start_draft', 'submit', 'approve', 'reject', 'publish', 'deprecate', 'disable', 'enable']


def reviewed_document(package, revision: str, content: DraftContent):
    metadata = {
        'skill_key': package.skill_key, 'revision': revision, 'description': content.description,
        'catalog': content.catalog_metadata.model_dump(mode='json', exclude_unset=True),
    }
    raw = ('---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
           + '---\n' + content.body).encode('utf-8')
    publication = PublishRevision(revision=revision,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        body_sha256=hashlib.sha256(content.body.encode('utf-8')).hexdigest())
    SkillStorage.validate_bytes(package, publication, raw)
    return publication, raw
