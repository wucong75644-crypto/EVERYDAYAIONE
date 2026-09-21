"""Re-enabling restores only the audited suspension, without republishing content."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest

from core.db_scope import DatabaseAccessKind, SET_DATABASE_SCOPE_SQL
from services.skills.authoring_contracts import SaveDraft
from services.skills.contracts import SkillError
from services.skills.resolver import SkillResolutionContext
from services.skills.runtime import SkillReplayError
from services.skills.runtime_source import ActorSkillSource
from tests.test_skill_authoring_postgres import (  # noqa: F401
    CONTENT, MIGRATIONS, environment, postgres_socket, create, action, publish,
)
from tests.test_skill_runtime import state, activate


def test_disabled_publication_can_be_reenabled_without_new_revision(environment):
    svc = environment.service()
    pid = create(svc)
    published = publish(svc, pid)
    original = svc.repository.enabled_revisions()[0]
    path = environment.root / original.nas_path
    before_file = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    disabled = action(svc, pid, 'disable')
    assert svc.repository.catalog_candidates() == []
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        svc.repository.assigned_revision(pid, original.revision, restoring=True)

    request_id = str(uuid4())
    svc.repository.scope = replace(svc.repository.scope, request_id=request_id)
    enabled = action(svc, pid, 'enable', disabled['draft']['version'])

    assert enabled['draft']['status'] == 'published'
    assert enabled['draft']['version'] == disabled['draft']['version'] + 1
    assert enabled['revisions'] == published['revisions']
    assert svc.repository.enabled_revisions() == [original]
    assert svc.repository.assigned_revision(pid, original.revision, restoring=True) == original
    assert (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == before_file
    with environment.pool.connection(privileged=True) as conn:
        audits = conn.execute('''SELECT entity, from_state, to_state, actor_user_id, request_id
            FROM skill_change_audits WHERE package_id = %s AND action = 'enable' ''', (pid,)).fetchall()
    assert {row[0] for row in audits} == {'skill_drafts', 'skill_revisions'}
    assert all(row[1:] == ('disabled', 'published', environment.actor, request_id) for row in audits)


@pytest.mark.parametrize('working_state', ['draft', 'in_review', 'approved', 'published'])
def test_restore_working_state_without_publishing_unreviewed_content(environment, working_state):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    if working_state in ('draft', 'in_review', 'approved'):
        draft = action(svc, pid, 'start_draft')['draft']
        svc.save(pid, SaveDraft(expected_version=draft['version'], content=CONTENT.model_copy(update={'body': 'Unpublished changes'})))
        if working_state in ('in_review', 'approved'):
            action(svc, pid, 'submit')
        if working_state == 'approved':
            action(svc, pid, 'approve')
    before = svc.detail(pid)
    with pytest.raises(SkillError, match='TRANSITION_INVALID'):
        action(svc, pid, 'enable')
    for _ in range(2):
        action(svc, pid, 'disable')
        enabled = action(svc, pid, 'enable')
        assert enabled['revisions'] == before['revisions']
        assert enabled['available_revision'] == before['available_revision']
        for field in ('status', 'content', 'revision', 'approved_by', 'approved_at'):
            assert enabled['draft'][field] == before['draft'][field]


def test_unpublished_draft_can_be_reenabled_without_nas_but_cannot_skip_review(environment):
    svc = environment.service()
    pid = create(svc)
    before = svc.detail(pid)['draft']
    action(svc, pid, 'disable')
    environment.config.skill_storage_root = str(environment.root / 'unavailable')
    enabled = action(svc, pid, 'enable')
    assert enabled['draft']['status'] == 'draft' and enabled['draft']['content'] == before['content']
    assert enabled['revisions'] == [] and svc.repository.catalog_candidates() == []
    with pytest.raises(SkillError, match='TRANSITION_INVALID'):
        action(svc, pid, 'publish')


@pytest.mark.parametrize('damage', ['tamper', 'missing'])
def test_reenable_requires_valid_nas_and_rolls_back(environment, damage):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    first = svc.repository.enabled_revisions()[0]
    action(svc, pid, 'start_draft')
    publish(svc, pid)
    # Damage the old version, not the assignment's current version. Restoring
    # existing Turns must not silently enable an unverified historical file.
    path = environment.root / first.nas_path
    raw = path.read_bytes()
    disabled = action(svc, pid, 'disable')
    if damage == 'tamper':
        path.chmod(0o640)
        path.write_bytes(b'tampered')
    else:
        path.unlink()
    with pytest.raises(SkillError):
        action(svc, pid, 'enable')
    assert svc.detail(pid) == disabled
    assert svc.repository.catalog_candidates() == []
    with environment.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE action = 'enable'").fetchone()[0] == 0
    path.write_bytes(raw)
    path.chmod(0o440)
    assert action(svc, pid, 'enable')['draft']['status'] == 'published'


def test_reenable_does_not_restore_revoked_assignment_or_retired_revision(environment):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    first = svc.repository.enabled_revisions()[0]
    svc.repository.retire_revision(pid, first.id)
    action(svc, pid, 'start_draft')
    publish(svc, pid)
    current = svc.repository.enabled_revisions()[0]
    action(svc, pid, 'disable')
    svc.repository.set_assignment(pid, current.id, enabled=False)
    restored = action(svc, pid, 'enable')
    assert {row['revision']: row['status'] for row in restored['revisions']} == {
        first.revision: 'retired', current.revision: 'published',
    }
    assert restored['available_revision'] is None and svc.repository.catalog_candidates() == []
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        svc.repository.assigned_revision(pid, current.revision, restoring=True)


def test_concurrent_reenable_accepts_one_expected_version(environment):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    version = action(svc, pid, 'disable')['draft']['version']
    barrier = Barrier(2)
    def attempt(_):
        barrier.wait(timeout=5)
        try:
            action(environment.service(), pid, 'enable', version)
            return 'enabled'
        except SkillError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, range(2))) == ['SKILL_VERSION_CONFLICT', 'enabled']
    with environment.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE action='enable' AND entity='skill_drafts'").fetchone()[0] == 1


def test_database_failure_rolls_back_revision_restore_and_enable_audits(environment):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    disabled = action(svc, pid, 'disable')
    with environment.pool.connection(privileged=True) as conn:
        conn.execute("""CREATE FUNCTION reject_enable() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test database failure'; END $$;
            CREATE TRIGGER test_failure BEFORE UPDATE ON skill_drafts
            FOR EACH ROW EXECUTE FUNCTION reject_enable()""")
    with pytest.raises(psycopg.Error, match='test database failure'):
        action(svc, pid, 'enable')
    assert svc.detail(pid) == disabled and svc.repository.catalog_candidates() == []
    with environment.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE action='enable'").fetchone()[0] == 0
        conn.execute('DROP TRIGGER test_failure ON skill_drafts; DROP FUNCTION reject_enable()')
    assert action(svc, pid, 'enable')['draft']['status'] == 'published'


def test_reenable_preserves_organization_platform_and_role_boundaries(environment):
    svc, platform = environment.service(), environment.service(None)
    pid, global_pid = create(svc), create(platform, 'global')
    action(svc, pid, 'disable')
    action(platform, global_pid, 'disable')
    for forbidden, target, reason in [
        (environment.service(environment.other), pid, 'PACKAGE_UNAVAILABLE'),
        (svc, global_pid, 'OWNER_SCOPE_MISMATCH'),
        (environment.service(access=DatabaseAccessKind.PROJECTION), pid, 'CONTROL_ACCESS_REQUIRED'),
    ]:
        with pytest.raises(SkillError, match=reason):
            action(forbidden, target, 'enable', 2)
    assert svc.detail(pid)['draft']['status'] == 'disabled'
    with environment.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, environment.service(environment.other).repository.scope.settings)
        assert conn.execute('SELECT skill_disabled_restore_state(%s,2)', (pid,)).fetchone()[0] is None
        assert conn.execute("UPDATE skill_drafts SET status='draft',version=3 WHERE package_id=%s RETURNING package_id", (pid,)).fetchall() == []


def test_missing_disable_audit_cannot_reopen_revision_or_draft(environment):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    # A different trusted administrative operation is not an undoable disable.
    with environment.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, svc.repository.scope.settings)
        conn.execute("UPDATE skill_revisions SET status='disabled' WHERE package_id=%s", (pid,))
        conn.execute("UPDATE skill_drafts SET status='disabled',version=version+1 WHERE package_id=%s", (pid,))
    with pytest.raises(SkillError, match='TRANSITION_INVALID'):
        action(svc, pid, 'enable')
    with environment.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, svc.repository.scope.settings)
        conn.execute("SELECT set_config('app.skill_action','enable',true)")
        for statement in [
            "UPDATE skill_revisions SET status='published' WHERE package_id=%s",
            "UPDATE skill_drafts SET status='published',version=version+1 WHERE package_id=%s",
        ]:
            with pytest.raises(psycopg.errors.CheckViolation, match='TRANSITION_INVALID'):
                with conn.transaction():
                    conn.execute(statement, (pid,))


def test_previously_disabled_revision_is_not_restored_by_later_package_disable(environment):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    first = svc.repository.enabled_revisions()[0]
    action(svc, pid, 'start_draft')
    publish(svc, pid)
    with environment.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, svc.repository.scope.settings)
        conn.execute("SELECT set_config('app.skill_action','disable',true)")
        conn.execute("UPDATE skill_revisions SET status='disabled' WHERE id=%s", (first.id,))
    action(svc, pid, 'disable')
    restored = action(svc, pid, 'enable')
    assert next(row for row in restored['revisions'] if row['revision'] == first.revision)['status'] == 'disabled'
    assert len(svc.repository.catalog_candidates()) == 1
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        svc.repository.assigned_revision(pid, first.revision, restoring=True)


def test_database_reenable_cannot_change_content_or_choose_another_state(environment):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'disable')
    with environment.pool.connection() as conn:
        conn.execute(SET_DATABASE_SCOPE_SQL, svc.repository.scope.settings)
        for action_name, mutation in [
            ('save', "status='draft'"),
            ('enable', "status='in_review'"),
            ('enable', "status='draft',content='{}'"),
            ('enable', "status='draft',revision='forged'"),
        ]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("SELECT set_config('app.skill_action',%s,true)", (action_name,))
                    conn.execute('UPDATE skill_drafts SET version=version+1,' + mutation + ' WHERE package_id=%s', (pid,))
    assert svc.detail(pid)['draft']['status'] == 'disabled'


def test_preexisting_disabled_record_survives_migration_and_data_preserving_rollback(environment):
    svc = environment.service()
    # Exercise the older 259/260 guard in isolation; 261 adds this trigger.
    with environment.pool.connection(privileged=True) as conn:
        conn.execute('DROP TRIGGER skill_assignment_lifecycle ON skill_assignments')
    with environment.pool.connection(privileged=True) as conn:
        conn.execute((MIGRATIONS / 'rollback/260_skill_reenable_rollback.sql').read_text())
    pid = create(svc)
    publish(svc, pid)
    action(svc, pid, 'disable')
    with environment.pool.connection(privileged=True) as conn:
        conn.execute((MIGRATIONS / '260_skill_reenable.sql').read_text())
    assert action(svc, pid, 'enable')['draft']['status'] == 'published'
    action(svc, pid, 'disable')
    before = svc.detail(pid)
    with environment.pool.connection(privileged=True) as conn:
        conn.execute((MIGRATIONS / 'rollback/260_skill_reenable_rollback.sql').read_text())
        with pytest.raises(psycopg.errors.CheckViolation, match='TRANSITION_INVALID'):
            with conn.transaction():
                conn.execute("UPDATE skill_revisions SET status='published' WHERE package_id=%s", (pid,))
        conn.execute((MIGRATIONS / '260_skill_reenable.sql').read_text())
    assert svc.detail(pid) == before
    assert action(svc, pid, 'enable')['draft']['status'] == 'published'


async def test_new_turn_and_original_checkpoint_resume_after_reenable(environment):
    env = environment
    svc = env.service()
    pid = create(svc)
    publish(svc, pid)
    source = ActorSkillSource(SimpleNamespace(db=SimpleNamespace(pool=env.pool)),
        SimpleNamespace(actor_user_id=str(env.actor), org_id=str(env.org)), env.config)
    source._resolution_context = AsyncMock(return_value=SkillResolutionContext(
        actor_user_id=env.actor, org_id=env.org, conversation_scope='user', agent_domain='general',
        execution_mode='interactive', enabled_feature_flags={'skill_catalog_enabled'}))
    old = state(source)
    await old.initialize()
    assert (await old.activate(activate('orders')))['ok']
    checkpoint = old.checkpoint()
    action(svc, pid, 'start_draft')
    second = publish(svc, pid)
    action(svc, pid, 'disable')
    blocked = state(source)
    await blocked.initialize()
    assert blocked.directory == {}
    with pytest.raises(SkillReplayError, match='PINNED_REVISION_UNAVAILABLE'):
        await state(source).initialize(checkpoint)
    action(svc, pid, 'enable')
    resumed = state(source)
    await resumed.initialize(checkpoint)
    assert resumed.active['orders'].revision == checkpoint['active'][0]['revision']
    assert resumed.active['orders'].rendered == CONTENT.body
    assert resumed.effective_allowed_tool_names == {'file_search'}
    fresh = state(source)
    await fresh.initialize()
    assert (await fresh.activate(activate('orders')))['ok']
    assert fresh.active['orders'].revision == second['draft']['revision']
    action(svc, pid, 'disable')
    action(svc, pid, 'deprecate')
    deprecated = state(source)
    await deprecated.initialize()
    assert deprecated.directory == {}
    restored_deprecated = state(source)
    await restored_deprecated.initialize(checkpoint)
    assert restored_deprecated.active['orders'].revision == checkpoint['active'][0]['revision']
