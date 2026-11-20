"""Admin inputs never carry a storage path, authority, revision or review hash."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator
import yaml

from services.skills.contracts import Contract, PublishRevision, SkillCatalogMetadata, SkillError, SkillKey
from services.skills.storage import SkillStorage
from services.skills.assets import AssetDraft, MAX_ASSETS, SkillResources, TemplateVariable, VariableName


class DraftContent(Contract):
    description: str = Field(default='', max_length=2000)
    body: str = Field(default='', max_length=1_000_000)
    catalog_metadata: SkillCatalogMetadata = Field(default_factory=SkillCatalogMetadata)
    assets: tuple[AssetDraft, ...] = Field(default=(), max_length=MAX_ASSETS)
    template_variables: dict[VariableName, TemplateVariable] = Field(default_factory=dict, max_length=16)

    @model_validator(mode='after')
    def valid_resources(self):
        SkillResources(assets=tuple(a.manifest() for a in self.assets),
                       template_variables=self.template_variables)
        return self


class RevisionContent(Contract):
    description: str
    body: str
    catalog_metadata: SkillCatalogMetadata
    asset_summaries: list[dict] = Field(default_factory=list)
    template_variables: dict[VariableName, TemplateVariable] = Field(default_factory=dict)


class CreateSkill(Contract):
    skill_key: SkillKey
    content: DraftContent = Field(default_factory=DraftContent)


def new_draft_content(content: DraftContent) -> DraftContent:
    """Creation default only: never reinterpret saved drafts or revisions."""
    metadata = content.catalog_metadata
    if not {'tool_policy', 'allowed_tool_names', 'required_capabilities', 'allowed_capabilities'} & metadata.model_fields_set:
        return content.model_copy(update={
            'catalog_metadata': metadata.model_copy(update={'tool_policy': 'platform'}),
        })
    return content


class ExpectedVersion(Contract):
    expected_version: Annotated[int, Field(strict=True, ge=0)]


class SaveDraft(ExpectedVersion):
    content: DraftContent


class TransitionDraft(ExpectedVersion):
    action: Literal['start_draft', 'submit', 'approve', 'reject', 'publish', 'publish_private', 'deprecate', 'disable', 'enable']


def confirm_model_selection(content: DraftContent, allowed: bool) -> DraftContent:
    metadata = content.catalog_metadata
    if allowed:
        from services.skills.workflow_contracts import validate_protocols
        contract = validate_protocols(metadata.coordination)
        if contract and contract.kind == 'workflow':
            if (metadata.task_stage is None or not any(
                    'plan' in claim.actions and claim.method in {'image.prompt', 'ecommerce.inline'}
                    for claim in contract.claims)):
                raise SkillError('SKILL_AUTOMATIC_HANDOFF_UNAVAILABLE')
        if 'interactive' not in metadata.execution_modes or not content.description.strip():
            raise SkillError('SKILL_MODEL_SELECTION_UNAVAILABLE')
    return content.model_copy(update={'catalog_metadata': metadata.model_copy(update={
        'model_selectable': allowed,
    })})


def reviewed_document(package, revision: str, content: DraftContent):
    contract = content.catalog_metadata.coordination
    if contract and package.skill_key in contract.requires:
        raise SkillError('SKILL_DEPENDENCY_INVALID')
    metadata = {
        'skill_key': package.skill_key, 'revision': revision, 'description': content.description,
        'catalog': content.catalog_metadata.model_dump(mode='json', exclude_unset=True),
    }
    if content.assets:
        metadata['assets'] = [a.manifest().model_dump(exclude_none=True) for a in content.assets]
    if content.template_variables:
        metadata['template_variables'] = {k: v.model_dump() for k, v in content.template_variables.items()}
    raw = ('---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
           + '---\n' + content.body).encode('utf-8')
    publication = PublishRevision(revision=revision,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        body_sha256=hashlib.sha256(content.body.encode('utf-8')).hexdigest())
    validated = SkillStorage.validate_bytes(package, publication, raw)
    from services.skills.renderer import validate_resource_templates
    validate_resource_templates(validated, {a.id: a.content for a in content.assets})
    return publication, raw
