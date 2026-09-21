"""The manifest is authority; assets are bounded text and never executable."""
import hashlib
import os
from unittest.mock import Mock

from pydantic import ValidationError
import pytest
import yaml

from services.skills.assets import AssetDraft, TemplateVariable
from services.skills.authoring_contracts import DraftContent, reviewed_document
from services.skills.contracts import SkillError
from services.skills.renderer import MAX_RENDERED_BYTES, prepare_resources, render_resources
from tests.test_skill_storage import storage, PACKAGE  # noqa: F401


def attachment(id='guide', kind='reference', content='A private reference.', **changes):
    return AssetDraft(id=id, name='说明', kind=kind, summary='附件用途', format='md', content=content, **changes)


def document(*assets, body='Read [[asset:guide]].', variables=None):
    return DraftContent(description='说明', body=body, assets=assets,
        template_variables=variables or {}, catalog_metadata={'model_selectable': True})


def publish(storage, content=None, revision='v1', package=PACKAGE):
    content = content or document(attachment())
    publication, raw = reviewed_document(package, revision, content)
    return storage.publish(package, publication, raw,
                           assets={a.id: a.content.encode('utf-8') for a in content.assets})


@pytest.mark.parametrize('path', ['../outside.txt', '/etc/passwd', 'assets/../guide.md',
    'assets/../../v0/assets/guide.md', 'assets\\guide.md', 'assets/%2e%2e/guide.md',
    'assets//guide.md', 'assets/./guide.md', 'assets/guide.md\x00', 'assets/guide.py'])
def test_manifest_rejects_every_noncanonical_path(storage, path):
    publication, raw = reviewed_document(PACKAGE, 'v1', document(attachment()))
    metadata = yaml.safe_load(raw.decode().split('---\n')[1])
    metadata['assets'][0]['path'] = path
    raw = ('---\n' + yaml.safe_dump(metadata) + '---\nRead [[asset:guide]].').encode()
    publication = publication.model_copy(update={'content_sha256': hashlib.sha256(raw).hexdigest()})
    with pytest.raises(SkillError, match='ASSET_MANIFEST_INVALID'):
        storage.validate_bytes(PACKAGE, publication, raw)


@pytest.mark.parametrize('damage', ['symlink_file', 'symlink_directory', 'hardlink', 'fifo', 'missing', 'drift', 'same_size_drift'])
def test_asset_read_rejects_unsafe_files_and_drift(storage, tmp_path, damage):
    skill = publish(storage)
    target = storage.root / skill.nas_path.replace('SKILL.md', 'assets/guide.md')
    outside = tmp_path / 'guide.md'
    outside.write_text('A private reference.')
    target.unlink()
    if damage == 'symlink_file':
        target.symlink_to(outside)
    elif damage == 'symlink_directory':
        target.parent.rmdir()
        target.parent.symlink_to(tmp_path)
    elif damage == 'hardlink':
        os.link(outside, target)
    elif damage == 'fifo':
        os.mkfifo(target)
    elif damage == 'drift':
        target.write_text('changed reference')
    elif damage == 'same_size_drift':
        target.write_text('B private reference.')
    with pytest.raises(SkillError, match='REJECTED|HASH_MISMATCH|NOT_REGULAR|OUTSIDE_STORAGE'):
        storage.read_assets(skill, ['guide'])


def test_symlink_to_another_revision_inside_storage_is_also_rejected(storage):
    old = publish(storage)
    newer = publish(storage, revision='v2')
    original = (storage.root / old.nas_path).parent / 'assets/guide.md'
    target = (storage.root / newer.nas_path).parent / 'assets/guide.md'
    target.unlink()
    target.symlink_to(original)
    with pytest.raises(SkillError, match='READ_REJECTED'):
        storage.read_assets(newer, ['guide'])


def test_renderer_only_loads_declared_original_body_references(storage):
    skill = publish(storage, document(attachment(content='[[asset:hidden]]'),
                                     attachment('hidden', content='do not load')))
    reader = Mock(wraps=storage._read)
    storage._read = reader
    ids, base, values = prepare_resources(skill, {}, MAX_RENDERED_BYTES)
    assert ids == ('guide',)
    texts = storage.read_assets(skill, ids)
    rendered = render_resources(skill, ids, base, values, texts, MAX_RENDERED_BYTES)
    assert reader.call_count == 1 and 'do not load' not in rendered
    assert '[[asset:hidden]]' in rendered  # Literal reference data, never recursive IO.
    assert 'assets/guide.md' not in rendered and str(storage.root) not in rendered
    for asset_id in ('../guide', '/secret', 'missing'):
        with pytest.raises(SkillError, match='NOT_DECLARED'):
            storage.read_assets(skill, [asset_id])
    reader.assert_called_once()


def test_no_reference_means_summary_only_and_zero_asset_reads(storage):
    skill = publish(storage, document(attachment(), body='Read the instructions.'))
    ids, base, values = prepare_resources(skill, {}, MAX_RENDERED_BYTES)
    assert not ids and '附件用途' in base and 'A private reference.' not in base
    assert render_resources(skill, ids, base, values, {}, MAX_RENDERED_BYTES) == base


def test_preflight_budget_and_post_substitution_budget(storage):
    skill = publish(storage, document(attachment(content='x' * MAX_RENDERED_BYTES)))
    with pytest.raises(SkillError, match='ASSET_BUDGET_EXCEEDED'):
        prepare_resources(skill, {}, MAX_RENDERED_BYTES)
    skill = publish(storage, document(attachment(kind='template', content='{{args.org}}' * 800),
        variables={'org': TemplateVariable(source='org_id', type='string')}), revision='v2')
    ids, base, values = prepare_resources(skill, {'org_id': 'o' * 36}, MAX_RENDERED_BYTES)
    with pytest.raises(SkillError, match='BUDGET_EXCEEDED'):
        render_resources(skill, ids, base, values, storage.read_assets(skill, ids), MAX_RENDERED_BYTES)


def test_template_types_are_explicit_and_values_come_from_server(storage, monkeypatch):
    monkeypatch.setenv('PRIVATE_VALUE', 'never-read-this')
    skill = publish(storage, document(attachment(kind='template', content='Org {{args.org}}, channel {{args.channel}}'),
        variables={'org': {'type': 'string', 'source': 'org_id'},
                   'channel': {'type': 'boolean', 'source': 'is_channel'}}))
    ids, base, values = prepare_resources(skill, {'org_id': 'org-1', 'is_channel': False}, MAX_RENDERED_BYTES)
    rendered = render_resources(skill, ids, base, values, storage.read_assets(skill, ids), MAX_RENDERED_BYTES)
    assert 'Org org-1, channel false' in rendered and 'never-read-this' not in rendered
    for context in ({}, {'org_id': 123, 'is_channel': False}, {'org_id': 'org', 'is_channel': 'false'}):
        with pytest.raises(SkillError, match='SERVER_VALUE_UNAVAILABLE'):
            prepare_resources(skill, context, MAX_RENDERED_BYTES)


@pytest.mark.parametrize('source,type_', [('HOME', 'string'), ('env.TOKEN', 'string'),
    ('/etc/passwd', 'string'), ('workspace_path', 'string'), ('org_id', 'boolean'), ('is_channel', 'string')])
def test_arbitrary_template_sources_and_type_coercion_are_rejected(source, type_):
    with pytest.raises(ValidationError):
        TemplateVariable(source=source, type=type_)


@pytest.mark.parametrize('text', ['{{env.HOME}}', '${PRIVATE_VALUE}', '{{args.path}}',
    '{{args.org.__class__}}', "{% include '/secret' %}"])
def test_undeclared_and_executable_templates_cannot_be_reviewed(text):
    with pytest.raises(SkillError, match='VARIABLE_FORBIDDEN|VARIABLE_UNDECLARED'):
        reviewed_document(PACKAGE, 'v1', document(attachment(kind='template', content=text)))


def test_manifest_hash_covers_asset_changes_and_revision_is_installed_atomically(storage, monkeypatch):
    content = document(attachment())
    first, raw = reviewed_document(PACKAGE, 'v1', content)
    second, _ = reviewed_document(PACKAGE, 'v1', document(attachment(content='new content')))
    assert first.body_sha256 == second.body_sha256 and first.content_sha256 != second.content_sha256
    skill = publish(storage, content)
    target = storage.root / skill.nas_path.replace('SKILL.md', 'assets/guide.md')
    inode = target.stat().st_ino
    assert target.stat().st_mode & 0o222 == 0
    publish(storage, content)
    assert target.stat().st_ino == inode
    with pytest.raises(SkillError, match='CONTENT_HASH_MISMATCH'):
        publish(storage, document(attachment(content='changed')))
    assert not list(storage.root.rglob('.publishing-*'))
    original = os.rename
    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('ack lost')
    monkeypatch.setattr(os, 'rename', lose_ack)
    with pytest.raises(SkillError, match='WRITE_REJECTED'):
        publish(storage, content, revision='v2')
    monkeypatch.setattr(os, 'rename', original)
    publish(storage, content, revision='v2')
    assert target.read_text() == content.assets[0].content


def test_partial_asset_write_never_exposes_revision(storage, monkeypatch):
    original = os.open
    def fail_asset(path, *args, **kwargs):
        if path == 'guide.md':
            raise PermissionError('write failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', fail_asset)
    with pytest.raises(SkillError, match='WRITE_REJECTED'):
        publish(storage)
    assert not (storage.root / 'platform/report/v1').exists()
    assert not list(storage.root.rglob('.publishing-*'))


def test_unknown_files_are_ignored_and_changed_asset_cannot_be_retried(storage):
    skill = publish(storage)
    root = (storage.root / skill.nas_path).parent
    (root / 'unlisted.py').write_text('raise Exception("never execute")')
    assert storage.read_assets(skill, ['guide']) == {'guide': 'A private reference.'}
    target = root / 'assets/guide.md'
    target.chmod(0o640)
    target.write_text('drift')
    with pytest.raises(SkillError, match='ASSET_HASH_MISMATCH'):
        publish(storage)


def test_asset_counts_sizes_formats_and_duplicate_ids_are_bounded():
    with pytest.raises(ValidationError):
        document(attachment(), attachment())
    with pytest.raises(ValidationError):
        document(*(attachment(f'a{i}', content='x' * 65536) for i in range(5)))
    with pytest.raises(ValidationError):
        document(*(attachment(f'a{i}') for i in range(17)))
    with pytest.raises(ValidationError):
        attachment(content='中' * 65536)
    for change in ({'format': 'py'}, {'path': '../x'}, {'sha256': '0' * 64}, {'content': '\x00'}):
        with pytest.raises(ValidationError):
            AssetDraft.model_validate(attachment().model_dump() | change)
