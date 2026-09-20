"""State, RLS, NAS failures and real concurrent publication in temporary PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import getpass
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope, SET_DATABASE_SCOPE_SQL
from services.skills.authoring import SkillAuthoring
from services.skills.authoring_contracts import CreateSkill, DraftContent, SaveDraft, TransitionDraft, reviewed_document
from services.skills.contracts import SkillError, PackageCreate
from services.skills.repository import SkillRepository
from services.skills.runtime import SkillReplayError
from services.skills.runtime_source import ActorSkillSource
from services.skills.resolver import SkillResolutionContext
from services.skills.storage import SkillStorage
from tests.test_scheduled_task_draft_delete_integration import postgres_socket  # noqa: F401
from tests.test_skill_catalog import settings
from tests.test_skill_runtime import state, activate

MIGRATIONS = Path(__file__).parents[1] / 'migrations'


class Pool:
    def __init__(self, socket, database):
        self.socket, self.database = socket, database

    @contextmanager
    def connection(self, *, privileged=False):
        with psycopg.connect(host=self.socket, dbname=self.database, user=getpass.getuser()) as conn:
            if not privileged:
                conn.execute('SET ROLE everydayai')
            yield conn


@pytest.fixture
def environment(postgres_socket, tmp_path):
    name = 'skill_' + uuid4().hex
    with psycopg.connect(host=postgres_socket, dbname='postgres', user=getpass.getuser(), autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'everydayai'").fetchone():
            conn.execute('CREATE ROLE everydayai')
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    pool = Pool(postgres_socket, name)
    org, other, actor = uuid4(), uuid4(), uuid4()
    with pool.connection(privileged=True) as conn:
        conn.execute('CREATE TABLE organizations(id UUID PRIMARY KEY)')
        conn.execute('INSERT INTO organizations VALUES (%s),(%s)', (org, other))
        for migration in ('256_skill_catalog.sql', '257_skill_catalog_metadata.sql', '259_skill_authoring.sql',
                          '260_skill_reenable.sql'):
            conn.execute((MIGRATIONS / migration).read_text())
    root = tmp_path / 'nas'
    root.mkdir()
    config = settings(skill_catalog_enabled=True, skill_storage_root=str(root), file_workspace_root=str(tmp_path / 'workspace'))

    def service(owner=org, user=actor, access=DatabaseAccessKind.RUNTIME_ADMIN):
        return SkillAuthoring(SkillRepository(pool, DatabaseScope(str(user), str(owner) if owner else None, access)), config)

    yield SimpleNamespace(pool=pool, org=org, other=other, actor=actor, service=service, config=config, root=root)
    with psycopg.connect(host=postgres_socket, dbname='postgres', user=getpass.getuser(), autocommit=True) as conn:
        conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


CONTENT = DraftContent(description='审核说明', body='Read the reviewed orders.\n', catalog_metadata={
    'name': '订单说明', 'model_selectable': True, 'allowed_tool_names': ['file_search'],
})


def create(service, key='orders'):
    return service.create(CreateSkill(skill_key=key, content=CONTENT))['package_id']


def action(service, pid, name, version=None):
    draft = service.detail(pid)['draft']
    return service.transition(pid, TransitionDraft(action=name,
        expected_version=(draft['version'] if draft else 0) if version is None else version))


def publish(service, pid):
    action(service, pid, 'submit')
    action(service, pid, 'approve')
    return action(service, pid, 'publish')


def test_edit_review_publish_new_revision_and_immutable_history(environment):
    svc = environment.service()
    pid = create(svc)
    with pytest.raises(SkillError, match='TRANSITION_INVALID'):
        action(svc, pid, 'publish')
    action(svc, pid, 'submit')
    with pytest.raises(SkillError, match='APPROVAL_REQUIRED'):
        action(svc, pid, 'publish')
    with pytest.raises(SkillError, match='TRANSITION_INVALID'):
        svc.save(pid, SaveDraft(expected_version=2, content=CONTENT))
    action(svc, pid, 'approve')
    first = action(svc, pid, 'publish')
    v1 = first['revisions'][0]['revision']
    assert svc.read_revision(pid, v1).body == CONTENT.body
    assert svc.repository.catalog_candidates()[0].revision == v1
    action(svc, pid, 'start_draft')
    changed = CONTENT.model_copy(update={'body': 'New reviewed body.\n'})
    version = svc.detail(pid)['draft']['version']
    saved = svc.save(pid, SaveDraft(expected_version=version, content=changed))
    assert not saved['draft']['approved_by']
    assert svc.read_revision(pid, v1).body == CONTENT.body
    assert svc.repository.catalog_candidates()[0].revision == v1
    second = publish(svc, pid)
    v2 = second['draft']['revision']
    assert len(second['revisions']) == 2 and v2 != v1
    assert svc.repository.catalog_candidates()[0].revision == v2
    assert svc.repository.assigned_revision(pid, v1, restoring=True).revision == v1
    assert svc.read_revision(pid, v2).body == changed.body
    with environment.pool.connection(privileged=True) as conn:
        for column, value in [('summary', 'rewritten'), ('nas_path', 'elsewhere'), ('content_sha256', '0' * 64)]:
            with pytest.raises(psycopg.errors.CheckViolation, match='IMMUTABLE'):
                with conn.transaction():
                    conn.execute(sql.SQL('UPDATE skill_revisions SET {} = %s WHERE package_id = %s').format(
                        sql.Identifier(column)), (value, pid))


def test_reject_clears_approval_and_stale_editor_conflicts(environment):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'submit')
    action(svc, pid, 'approve')
    result = action(svc, pid, 'reject')
    assert result['draft']['status'] == 'draft' and result['draft']['approved_by'] is None
    with pytest.raises(SkillError, match='VERSION_CONFLICT'):
        svc.save(pid, SaveDraft(expected_version=1, content=CONTENT))


@pytest.mark.parametrize('damage', ['write', 'readback', 'database'])
def test_publish_failure_rolls_back_and_retries(environment, monkeypatch, damage):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'submit')
    approved = action(svc, pid, 'approve')['draft']
    if damage == 'database':
        with environment.pool.connection(privileged=True) as conn:
            conn.execute("""CREATE FUNCTION reject_assignment() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'test database failure'; END $$;
                CREATE TRIGGER test_failure BEFORE INSERT ON skill_assignments
                FOR EACH ROW EXECUTE FUNCTION reject_assignment()""")
    else:
        target = 'publish' if damage == 'write' else 'validate'
        original = getattr(SkillStorage, target)
        def fail(*args, **kwargs):
            raise SkillError('INJECTED_NAS_FAILURE')
        monkeypatch.setattr(SkillStorage, target, fail)
    with pytest.raises((SkillError, psycopg.Error)):
        action(svc, pid, 'publish')
    detail = svc.detail(pid)
    assert detail['draft']['version'] == approved['version'] and detail['draft']['status'] == 'in_review'
    assert detail['revisions'] == [] and svc.repository.catalog_candidates() == []
    with environment.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE action='publish'").fetchone()[0] == 0
        if damage == 'database':
            conn.execute('DROP TRIGGER test_failure ON skill_assignments; DROP FUNCTION reject_assignment()')
    if damage != 'database':
        monkeypatch.setattr(SkillStorage, target, original)
    files = list(environment.root.rglob('SKILL.md'))
    inode = files[0].stat().st_ino if files else None
    action(svc, pid, 'publish')
    if inode:
        assert files[0].stat().st_ino == inode
    assert len(svc.detail(pid)['revisions']) == 1


def test_concurrent_publish_has_one_revision_and_one_transition(environment):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'submit')
    version = action(svc, pid, 'approve')['draft']['version']
    barrier = Barrier(2)
    def attempt(_):
        barrier.wait(timeout=5)
        try:
            action(environment.service(), pid, 'publish', version)
            return 'published'
        except SkillError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, range(2)))
    assert sorted(outcomes) == ['SKILL_VERSION_CONFLICT', 'published']
    assert len(svc.detail(pid)['revisions']) == 1
    with environment.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE action='publish' AND entity='skill_drafts'").fetchone()[0] == 1


def test_organization_platform_rls_and_audits(environment):
    env = environment
    svc, other, platform = env.service(), env.service(env.other), env.service(None)
    pid, foreign, global_pid = create(svc), create(other), create(platform, 'global')
    assert {item['package_id'] for item in svc.list()} == {pid, global_pid}
    with pytest.raises(SkillError, match='PACKAGE_UNAVAILABLE'):
        svc.detail(foreign)
    with pytest.raises(SkillError, match='OWNER_SCOPE_MISMATCH'):
        action(svc, global_pid, 'disable')
    assert svc.detail(global_pid)['draft'] is None and svc.detail(global_pid)['editable'] is False
    with pytest.raises(SkillError, match='CONTROL_ACCESS_REQUIRED'):
        env.service(access=DatabaseAccessKind.PROJECTION).list()
    publish(svc, pid)
    action(svc, pid, 'deprecate')
    with env.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, svc.repository.scope.settings)
        assert {row[0] for row in conn.execute('SELECT package_id FROM skill_drafts')} == {pid}
        assert conn.execute('UPDATE skill_drafts SET version = version + 1 WHERE package_id = %s RETURNING package_id', (foreign,)).fetchall() == []
        audits = conn.execute('SELECT action, actor_user_id FROM skill_change_audits WHERE package_id = %s', (pid,)).fetchall()
        assert {'create', 'submit', 'approve', 'publish', 'deprecate'} <= {row[0] for row in audits}
        assert all(row[1] == env.actor for row in audits)
        with pytest.raises(psycopg.errors.CheckViolation, match='AUDIT_IMMUTABLE'):
            with conn.transaction():
                conn.execute("""INSERT INTO skill_change_audits(package_id,org_id,actor_user_id,request_id,entity,action)
                    VALUES (%s,%s,%s,'','fake','fake')""", (pid, env.org, env.actor))
    with env.pool.connection(privileged=True) as conn:
        for mutation in ("UPDATE skill_change_audits SET action='fake'", 'DELETE FROM skill_change_audits'):
            with pytest.raises(psycopg.errors.CheckViolation, match='AUDIT_IMMUTABLE'):
                with conn.transaction():
                    conn.execute(mutation)


async def test_deprecated_new_turn_blocked_active_checkpoint_restores_old_revision(environment):
    env = environment
    svc = env.service()
    pid = create(svc)
    publish(svc, pid)
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=env.pool)),
        SimpleNamespace(actor_user_id=str(env.actor), org_id=str(env.org)), env.config)
    source._resolution_context = AsyncMock(return_value=SkillResolutionContext(
        actor_user_id=env.actor, org_id=env.org, conversation_scope='user', agent_domain='general',
        execution_mode='interactive', enabled_feature_flags={'skill_catalog_enabled'}))
    old, not_active = state(source), state(source)
    await old.initialize()
    await not_active.initialize()
    assert (await old.activate(activate('orders')))['ok']
    checkpoint = old.checkpoint()
    old_revision = checkpoint['active'][0]['revision']
    action(svc, pid, 'start_draft')
    publish(svc, pid)
    action(svc, pid, 'deprecate')
    fresh = state(source)
    await fresh.initialize()
    assert fresh.directory == {}
    assert not (await not_active.activate(activate('orders')))['ok']
    restored = state(source)
    await restored.initialize(checkpoint)
    assert restored.active['orders'].revision == old_revision
    assert restored.active['orders'].rendered == CONTENT.body
    assert restored.effective_allowed_tool_names == {'file_search'}
    action(svc, pid, 'disable')
    with pytest.raises(SkillReplayError, match='PINNED_REVISION_UNAVAILABLE'):
        await state(source).initialize(checkpoint)


def test_restore_still_requires_grant_and_hash(environment):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    revision = svc.repository.enabled_revisions()[0]
    action(svc, pid, 'deprecate')
    assert svc.repository.assigned_revision(pid, revision.revision, restoring=True).status == 'deprecated'
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        svc.repository.assigned_revision(pid, revision.revision)
    path = environment.root / revision.nas_path
    path.chmod(0o640)
    path.write_text('tampered')
    with pytest.raises(SkillError, match='HASH_MISMATCH'):
        svc.read_revision(pid, revision.revision)
    svc.repository.set_assignment(pid, revision.id, enabled=False)
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        svc.repository.assigned_revision(pid, revision.revision, restoring=True)


@pytest.mark.parametrize('action_name', ['start_draft', 'submit', 'publish', 'deprecate'])
def test_disabled_requires_explicit_reenable(environment, action_name):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'disable')
    with pytest.raises(SkillError, match='TRANSITION_INVALID'):
        action(svc, pid, action_name)


def test_database_frozen_review_and_publication_bypass_rejected(environment):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'submit')
    with environment.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, svc.repository.scope.settings)
        for statement in ("UPDATE skill_drafts SET content='{}',version=version+1 WHERE package_id=%s",
                          "UPDATE skill_drafts SET status='published',version=version+1 WHERE package_id=%s"):
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(statement, (pid,))
    package = svc.repository.get_package(pid)
    publication, raw = reviewed_document(package, svc.detail(pid)['draft']['revision'], CONTENT)
    validated = svc._storage().publish(package, publication, raw)
    with pytest.raises(psycopg.errors.CheckViolation, match='APPROVAL_REQUIRED'):
        svc.repository.publish_revision(pid, validated)


def test_legacy_package_can_start_managed_draft(environment):
    svc = environment.service()
    package = svc.repository.create_package(PackageCreate(skill_key='legacy', source='legacy', scope_kind='org', org_id=environment.org))
    assert svc.detail(package.id)['draft'] is None
    action(svc, package.id, 'start_draft')
    assert svc.detail(package.id)['draft']['status'] == 'draft'


def test_migration_rollback_reapply_and_populated_refusal(environment):
    rollback = (MIGRATIONS / 'rollback/259_skill_authoring_rollback.sql').read_text()
    with environment.pool.connection(privileged=True) as conn:
        conn.execute((MIGRATIONS / 'rollback/260_skill_reenable_rollback.sql').read_text())
        conn.execute(rollback)
        assert conn.execute("SELECT to_regclass('skill_drafts')").fetchone()[0] is None
        conn.execute((MIGRATIONS / '259_skill_authoring.sql').read_text())
        conn.execute((MIGRATIONS / '260_skill_reenable.sql').read_text())
    pid = create(environment.service())
    with environment.pool.connection(privileged=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='AUTHORING_NOT_EMPTY'):
            with conn.transaction():
                conn.execute(rollback)
        assert conn.execute('SELECT package_id FROM skill_drafts').fetchone()[0] == pid
        assert conn.execute("SELECT relforcerowsecurity FROM pg_class WHERE relname='skill_drafts'").fetchone()[0]


def test_failed_database_publish_can_be_rejected_edited_and_republished(environment):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'submit')
    approved = action(svc, pid, 'approve')['draft']
    # Emulate the exact durable NAS outcome of a failed database transaction.
    package = svc.repository.get_package(pid)
    publication, raw = reviewed_document(package, approved['revision'], CONTENT)
    orphan = svc._storage().publish(package, publication, raw)
    action(svc, pid, 'reject')
    changed = CONTENT.model_copy(update={'body': 'Corrected content after failure.'})
    version = svc.detail(pid)['draft']['version']
    saved = svc.save(pid, SaveDraft(expected_version=version, content=changed))
    assert saved['draft']['revision'] != approved['revision']
    released = publish(svc, pid)
    assert svc.read_revision(pid, released['draft']['revision']).body == changed.body
    assert (environment.root / orphan.nas_path).read_bytes() == raw
    assert len(released['revisions']) == 1


@pytest.mark.parametrize('source_state,operation,allowed', [
    ('draft', 'start_draft', False), ('draft', 'approve', False), ('draft', 'reject', False),
    ('draft', 'deprecate', True), ('in_review', 'start_draft', False), ('in_review', 'submit', False),
    ('in_review', 'deprecate', True), ('in_review', 'disable', True),
    ('published', 'submit', False), ('published', 'approve', False), ('published', 'reject', False),
    ('published', 'publish', False), ('published', 'disable', True),
    ('deprecated', 'start_draft', False), ('deprecated', 'submit', False),
    ('deprecated', 'approve', False), ('deprecated', 'reject', False), ('deprecated', 'publish', False),
])
def test_remaining_state_edges(environment, source_state, operation, allowed):
    svc = environment.service()
    pid = create(svc)
    if source_state == 'in_review':
        action(svc, pid, 'submit')
    elif source_state in ('published', 'deprecated'):
        publish(svc, pid)
        if source_state == 'deprecated':
            action(svc, pid, 'deprecate')
    if allowed:
        result = action(svc, pid, operation)
        assert result['draft']['status'] == {'deprecate': 'deprecated', 'disable': 'disabled'}[operation]
    else:
        with pytest.raises(SkillError, match='TRANSITION_INVALID'):
            action(svc, pid, operation)
        assert svc.detail(pid)['draft']['status'] == source_state


def test_legacy_published_body_clones_without_rewriting_revision(environment):
    svc = environment.service()
    package = svc.repository.create_package(PackageCreate(skill_key='legacy', source='legacy', scope_kind='org', org_id=environment.org))
    publication, raw = reviewed_document(package, 'v1', CONTENT)
    validated = svc._storage().publish(package, publication, raw)
    original = svc.repository.publish_revision(package.id, validated)
    svc.repository.set_assignment(package.id, original.id, enabled=True)
    result = action(svc, package.id, 'start_draft')
    assert result['draft']['content']['body'] == CONTENT.body
    assert result['draft']['revision'] != 'v1'
    assert svc.repository.catalog_candidates()[0].revision == 'v1'
    assert (environment.root / original.nas_path).read_bytes() == raw


def test_non_bypass_rollback_owner_sees_hidden_data_and_preserves_rls(environment):
    pid = create(environment.service())
    with environment.pool.connection(privileged=True) as conn:
        conn.execute('CREATE ROLE skill_rollback_owner NOSUPERUSER NOBYPASSRLS')
        for table in ('skill_packages', 'skill_revisions', 'skill_assignments', 'skill_drafts', 'skill_change_audits'):
            conn.execute(sql.SQL('ALTER TABLE {} OWNER TO skill_rollback_owner').format(sql.Identifier(table)))
        conn.execute('SET ROLE skill_rollback_owner')
        assert conn.execute('SELECT count(*) FROM skill_drafts').fetchone()[0] == 0
        with pytest.raises(psycopg.errors.RaiseException, match='AUTHORING_NOT_EMPTY'):
            with conn.transaction():
                conn.execute((MIGRATIONS / 'rollback/259_skill_authoring_rollback.sql').read_text())
        assert conn.execute("SHOW row_security").fetchone()[0] == 'on'
        assert conn.execute("SELECT relforcerowsecurity FROM pg_class WHERE relname='skill_drafts'").fetchone()[0]
        conn.execute('RESET ROLE')
        assert conn.execute('SELECT package_id FROM skill_drafts').fetchone()[0] == pid


def test_library_projects_working_content_and_actual_available_revision(environment):
    svc = environment.service()
    pid = create(svc)
    first = svc.list()[0]
    assert first['name'] == '订单说明'
    assert first['working_description'] == CONTENT.description
    assert first['updated_at'] is not None
    assert first['available_revision'] is None
    released = publish(svc, pid)
    v1 = released['revisions'][0]['revision']
    assert released['available_revision'] == v1
    action(svc, pid, 'start_draft')
    draft = svc.detail(pid)['draft']
    changed = CONTENT.model_copy(update={'description': '新草稿用途', 'catalog_metadata': CONTENT.catalog_metadata.model_copy(update={'name': '草稿新名称'})})
    svc.save(pid, SaveDraft(expected_version=draft['version'], content=changed))
    row = svc.list()[0]
    assert row['name'] == '草稿新名称' and row['working_description'] == '新草稿用途'
    assert row['description'] == CONTENT.description  # Existing projection stays compatible.
    assert row['status'] == 'draft' and row['available_revision'] == v1
    assert row['available_revision_number'] == 1
    publish(svc, pid)
    assert svc.list()[0]['available_revision_number'] == 2
    # Assignment, rather than the most recent publication, decides what is usable.
    with environment.pool.connection(privileged=True) as conn:
        old_id = conn.execute('SELECT id FROM skill_revisions WHERE package_id=%s AND revision=%s', (pid, v1)).fetchone()[0]
    svc.repository.set_assignment(pid, old_id, enabled=True)
    assert svc.list()[0]['available_revision_number'] == 1
    assert svc.detail(pid)['available_revision'] == v1
    svc.repository.set_assignment(pid, old_id, enabled=False)
    assert svc.list()[0]['available_revision'] is None
    assert svc.detail(pid)['available_revision'] is None
    svc.repository.set_assignment(pid, old_id, enabled=True)
    action(svc, pid, 'deprecate')
    assert svc.list()[0]['available_revision'] is None
    assert svc.detail(pid)['available_revision'] is None


def test_library_never_exposes_platform_working_name_or_private_draft(environment):
    platform, org = environment.service(None), environment.service()
    pid = create(platform, 'platform-skill')
    row = org.list()[0]
    assert row['name'] is None and row['working_description'] is None
    publish(platform, pid)
    action(platform, pid, 'start_draft')
    draft = platform.detail(pid)['draft']
    private = CONTENT.model_copy(update={'description': 'unpublished secret', 'catalog_metadata': CONTENT.catalog_metadata.model_copy(update={'name': 'private name'})})
    platform.save(pid, SaveDraft(expected_version=draft['version'], content=private))
    row = org.list()[0]
    assert row['name'] == CONTENT.catalog_metadata.name
    assert row['working_description'] == CONTENT.description
    assert row['status'] == 'published'
    assert org.detail(pid)['draft'] is None
