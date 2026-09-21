"""Asset publication and replay through real revision grants, RLS and NAS."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest

from services.skills.assets import AssetDraft
from services.skills.authoring_contracts import CreateSkill, SaveDraft
from services.skills.contracts import PackageCreate, SkillError
from services.skills.runtime import SkillReplayError
from services.skills.runtime_source import ActorSkillSource
from services.skills.resolver import SkillResolutionContext
from tests.test_skill_authoring_postgres import environment, postgres_socket, action, publish  # noqa: F401
from tests.test_skill_assets import attachment, document, publish as publish_storage
from tests.test_skill_runtime import state, activate


def actor_source(env):
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=env.pool)),
        SimpleNamespace(actor_user_id=str(env.actor), org_id=str(env.org)), env.config)
    source._resolution_context = AsyncMock(return_value=SkillResolutionContext(
        actor_user_id=env.actor, org_id=env.org, conversation_scope='user',
        agent_domain='general', execution_mode='interactive',
        enabled_feature_flags={'skill_catalog_enabled'}))
    source.load_assets = AsyncMock(wraps=source.load_assets)
    return source


def create(env, content=None):
    svc = env.service()
    pid = svc.create(CreateSkill(skill_key='asset-report', content=content or document(attachment())))['package_id']
    released = publish(svc, pid)
    return svc, pid, released['draft']['revision']


def test_reviewed_assets_are_immutable_and_admin_reads_only_summaries(environment):
    svc, pid, revision = create(environment)
    view = svc.read_revision(pid, revision).model_dump(mode='json')
    assert view['asset_summaries'][0]['summary'] == '附件用途'
    assert set(view['asset_summaries'][0]) == {'id', 'name', 'kind', 'summary', 'format', 'bytes'}
    assert all(forbidden not in json.dumps(view) for forbidden in ('nas_path', 'sha256', 'A private reference.', str(environment.root)))
    with environment.pool.connection(privileged=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation, match='IMMUTABLE'):
            with conn.transaction():
                conn.execute("UPDATE skill_revisions SET content_sha256=%s WHERE package_id=%s", ('0' * 64, pid))
    action(svc, pid, 'start_draft')
    draft = svc.detail(pid)['draft']
    changed = document(attachment(content='NEW ASSET'))
    svc.save(pid, SaveDraft(expected_version=draft['version'], content=changed))
    newer = publish(svc, pid)['draft']['revision']
    assert revision != newer
    assert svc.read_revision(pid, revision).asset_summaries[0]['bytes'] == len('A private reference.')
    assert svc.read_revision(pid, newer).asset_summaries[0]['bytes'] == len('NEW ASSET')


async def test_replay_uses_old_asset_snapshot_after_assignment_and_context_change(environment):
    svc, pid, old_revision = create(environment, document(
        attachment(kind='template', content='Snapshot for {{args.org}}'),
        variables={'org': {'type': 'string', 'source': 'org_id'}}))
    source = actor_source(environment)
    runtime = state(source, template_context={'org_id': 'original-org'})
    await runtime.initialize()
    assert (await runtime.activate(activate('asset-report')))['ok']
    checkpoint = runtime.checkpoint()
    assert checkpoint['active'][0]['loaded_asset_ids'] == ['guide']
    assert 'Snapshot for original-org' in checkpoint['active'][0]['rendered']
    assert str(environment.root) not in json.dumps(checkpoint)
    action(svc, pid, 'start_draft')
    draft = svc.detail(pid)['draft']
    svc.save(pid, SaveDraft(expected_version=draft['version'], content=document(attachment(content='new version content'))))
    new_revision = publish(svc, pid)['draft']['revision']
    assert old_revision != new_revision
    restored = state(source, template_context={'org_id': 'changed-value'})
    await restored.initialize(checkpoint)
    assert restored.checkpoint() == checkpoint
    assert source.load_assets.await_args.args[0].revision == old_revision
    action(svc, pid, 'deprecate')
    fresh = state(source)
    await fresh.initialize()
    assert not fresh.directory
    # Even a previously advertised directory cannot newly activate a deprecated revision.
    fresh.directory = runtime.directory.copy()
    assert (await fresh.activate(activate('asset-report')))['code'] == 'SKILL_PINNED_REVISION_UNAVAILABLE'
    resumed = state(source)
    await resumed.initialize(checkpoint)
    assert resumed.checkpoint() == checkpoint


@pytest.mark.parametrize('damage', ['hash', 'missing', 'manifest', 'checkpoint', 'retired', 'revoked', 'disabled'])
async def test_old_asset_replay_fails_closed_and_never_selects_latest(environment, damage):
    svc, pid, revision = create(environment)
    source = actor_source(environment)
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate('asset-report')))['ok']
    checkpoint = runtime.checkpoint()
    saved = svc.repository.assigned_revision(pid, revision)
    asset = (environment.root / saved.nas_path).parent / 'assets/guide.md'
    if damage == 'hash':
        asset.chmod(0o640)
        asset.write_text('drift')
    elif damage == 'missing':
        asset.unlink()
    elif damage == 'manifest':
        path = environment.root / saved.nas_path
        path.chmod(0o640)
        path.write_text(path.read_text().replace('附件用途', 'changed manifest'))
    elif damage == 'checkpoint':
        checkpoint['active'][0]['loaded_asset_ids'] = []
    elif damage == 'retired':
        svc.repository.retire_revision(pid, saved.id)
    elif damage == 'revoked':
        svc.repository.set_assignment(pid, saved.id, enabled=False)
    elif damage == 'disabled':
        action(svc, pid, 'disable')
    source.discover = AsyncMock(side_effect=AssertionError('Must never discover latest during replay'))
    with pytest.raises(SkillReplayError):
        await state(source).initialize(checkpoint)
    source.discover.assert_not_awaited()


async def test_unreferenced_and_overbudget_assets_do_not_reach_reader(environment):
    svc, pid, _ = create(environment, document(attachment(content='x' * 65536), body='Summary only.'))
    source = actor_source(environment)
    runtime = state(source)
    await runtime.initialize()
    assert (await runtime.activate(activate('asset-report')))['ok']
    source.load_assets.assert_not_awaited()
    assert '附件用途' in runtime.active['asset-report'].rendered
    assert 'x' * 50 not in runtime.active['asset-report'].rendered
    action(svc, pid, 'start_draft')
    draft = svc.detail(pid)['draft']
    svc.save(pid, SaveDraft(expected_version=draft['version'], content=document(attachment(content='x' * 65536))))
    publish(svc, pid)
    over = state(source)
    await over.initialize()
    assert (await over.activate(activate('asset-report')))['code'] == 'SKILL_ASSET_BUDGET_EXCEEDED'
    source.load_assets.assert_not_awaited()
    assert not over.active


def test_reenable_checks_historical_asset_hashes_before_restoring_grants(environment):
    svc, pid, revision = create(environment)
    saved = svc.repository.assigned_revision(pid, revision)
    action(svc, pid, 'disable')
    path = (environment.root / saved.nas_path).parent / 'assets/guide.md'
    path.chmod(0o640)
    path.write_text('changed while disabled')
    with pytest.raises(SkillError, match='ASSET_HASH_MISMATCH'):
        action(svc, pid, 'enable')
    assert svc.detail(pid)['draft']['status'] == 'disabled'
    path.write_text('A private reference.')
    assert action(svc, pid, 'enable')['draft']['status'] == 'published'


def test_legacy_package_management_copy_preserves_assets(environment):
    svc = environment.service()
    package = svc.repository.create_package(PackageCreate(skill_key='legacy-assets', source='managed',
        scope_kind='org', org_id=environment.org))
    skill = publish_storage(svc._storage(), package=package)
    saved = svc.repository.publish_revision(package.id, skill)
    svc.repository.set_assignment(package.id, saved.id, enabled=True)
    copied = action(svc, package.id, 'start_draft')['draft']['content']
    assert copied['assets'][0]['content'] == 'A private reference.'
    assert set(copied['assets'][0]) == set(AssetDraft.model_fields)
    publish(svc, package.id)
    assert svc.read_revision(package.id, 'v1').asset_summaries[0]['id'] == 'guide'
