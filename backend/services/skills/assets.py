"""Revision-owned text assets and an explicit server template vocabulary."""

import base64
import binascii
import hashlib
import hmac
from typing import Annotated, Literal

from pydantic import Field, model_serializer, model_validator

from services.skills.contracts import Contract, Sha256, SkillError, SkillKey

MAX_ASSETS = 16
MAX_ASSET_BYTES = 65_536
MAX_PACKAGE_ASSET_BYTES = 262_144
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_SOURCE_BYTES = 8 * 1024 * 1024
AssetKind = Literal['reference', 'template', 'example_input', 'example_output']
AssetFormat = Literal['md', 'txt', 'json', 'csv']
SourceFormat = Literal['md', 'txt', 'json', 'csv', 'docx', 'pdf', 'xlsx']
VariableName = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,31}$')]
SERVER_VALUE_TYPES = {
    'actor_user_id': 'string', 'org_id': 'string', 'conversation_scope': 'string',
    'agent_domain': 'string', 'execution_mode': 'string', 'is_channel': 'boolean',
}


class TemplateVariable(Contract):
    type: Literal['string', 'boolean']
    source: Literal['actor_user_id', 'org_id', 'conversation_scope', 'agent_domain',
                    'execution_mode', 'is_channel']

    @model_validator(mode='after')
    def source_type(self):
        if SERVER_VALUE_TYPES[self.source] != self.type:
            raise ValueError('SKILL_TEMPLATE_SOURCE_TYPE_INVALID')
        return self


class AssetSummary(Contract):
    id: SkillKey
    name: str = Field(min_length=1, max_length=120, pattern=r'\S')
    kind: AssetKind
    summary: str = Field(min_length=1, max_length=500, pattern=r'\S')
    format: AssetFormat


class AssetSource(Contract):
    format: SourceFormat
    path: str
    sha256: Sha256
    bytes: int = Field(strict=True, ge=1, le=MAX_SOURCE_BYTES)


class AssetSourceDraft(Contract):
    format: SourceFormat
    base64: str = Field(min_length=4, max_length=4 * ((MAX_SOURCE_BYTES + 2) // 3))

    def raw(self) -> bytes:
        try:
            raw = base64.b64decode(self.base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('SKILL_ASSET_SOURCE_INVALID') from None
        if not 1 <= len(raw) <= MAX_SOURCE_BYTES:
            raise ValueError('SKILL_ASSET_SOURCE_INVALID')
        return raw

    @model_validator(mode='after')
    def bounded_file(self):
        self.raw()
        return self

    @classmethod
    def from_bytes(cls, format: SourceFormat, raw: bytes):
        return cls(format=format, base64=base64.b64encode(raw).decode('ascii'))


class SkillAsset(AssetSummary):
    path: str
    sha256: Sha256
    bytes: int = Field(strict=True, ge=1, le=MAX_ASSET_BYTES)
    source: AssetSource | None = None

    @model_validator(mode='after')
    def controlled_path(self):
        # Canonical flat names also exclude traversal, encoded paths and scripts.
        if self.path != f'assets/{self.id}.{self.format}':
            raise ValueError('SKILL_ASSET_PATH_INVALID')
        if self.source and self.source.path != f'assets/{self.id}.original.{self.source.format}':
            raise ValueError('SKILL_ASSET_PATH_INVALID')
        return self


class AssetDraft(AssetSummary):
    content: str = Field(min_length=1, max_length=MAX_ASSET_BYTES)
    source: AssetSourceDraft | None = None

    @model_serializer(mode='wrap')
    def omit_absent_source(self, handler):
        # Existing text-only drafts remain readable by the previous release.
        result = handler(self)
        if self.source is None:
            result.pop('source', None)
        return result

    @model_validator(mode='after')
    def bounded_text(self):
        if len(self.content.encode('utf-8')) > MAX_ASSET_BYTES or '\x00' in self.content:
            raise ValueError('SKILL_ASSET_CONTENT_INVALID')
        return self

    def manifest(self) -> SkillAsset:
        raw = self.content.encode('utf-8')
        source = None
        if self.source:
            original = self.source.raw()
            source = AssetSource(format=self.source.format,
                path=f'assets/{self.id}.original.{self.source.format}', bytes=len(original),
                sha256=hashlib.sha256(original).hexdigest())
        return SkillAsset(**self.model_dump(exclude={'content', 'source'}), source=source,
                          path=f'assets/{self.id}.{self.format}', bytes=len(raw),
                          sha256=hashlib.sha256(raw).hexdigest())


class SkillResources(Contract):
    assets: tuple[SkillAsset, ...] = Field(default=(), max_length=MAX_ASSETS)
    template_variables: dict[VariableName, TemplateVariable] = Field(default_factory=dict, max_length=16)

    @model_validator(mode='after')
    def unique_and_bounded(self):
        if len({a.id for a in self.assets}) != len(self.assets):
            raise ValueError('SKILL_ASSET_DUPLICATE')
        if sum(a.bytes for a in self.assets) > MAX_PACKAGE_ASSET_BYTES:
            raise ValueError('SKILL_ASSET_PACKAGE_BUDGET_EXCEEDED')
        if sum(a.source.bytes for a in self.assets if a.source) > MAX_PACKAGE_SOURCE_BYTES:
            raise ValueError('SKILL_ASSET_SOURCE_BUDGET_EXCEEDED')
        return self


def public_assets(resources: SkillResources) -> list[dict]:
    return [a.model_dump(include=set(AssetSummary.model_fields)) | {'bytes': a.bytes}
            | ({'file_format': a.source.format, 'file_bytes': a.source.bytes} if a.source else {})
            for a in resources.assets]


def verify_source(source: AssetSource, raw: bytes) -> bytes:
    if len(raw) != source.bytes or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), source.sha256):
        raise SkillError('SKILL_ASSET_HASH_MISMATCH')
    return raw


def verify_asset(asset: SkillAsset, raw: bytes) -> str:
    if len(raw) != asset.bytes or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), asset.sha256):
        raise SkillError('SKILL_ASSET_HASH_MISMATCH')
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise SkillError('SKILL_ASSET_UTF8_INVALID') from None
    if '\x00' in content:
        raise SkillError('SKILL_ASSET_CONTENT_INVALID')
    return content
