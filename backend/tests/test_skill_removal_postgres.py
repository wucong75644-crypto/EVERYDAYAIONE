"""Safe removal keeps history, rejects live references and serializes final checks."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from core.db_scope import DatabaseAccessKind
from services.skills.authoring_contracts import ExpectedVersion
from services.skills.contracts import SkillError
from tests.test_skill_authoring_postgres import (  # noqa: F401
    MIGRATIONS, environment, postgres_socket, create, action, publish,
)


@pytest.fixture
def removal(environment):
    env = environment
    with env.pool.connection(privileged=True) as conn:
        conn.execute('''CREATE TABLE tasks(id uuid PRIMARY KEY, org_id uuid, turn_id uuid,
            type text NOT NULL DEFAULT 'chat', status text NOT NULL, request_params jsonb)''')
        conn.execute('''CREATE TABLE conversation_turn_checkpoints(task_id uuid PRIMARY KEY REFERENCES tasks(id),
            turn_id uuid NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
            state jsonb NOT NULL, status text NOT NULL DEFAULT 'ready')''')
        conn.execute('GRANT ALL ON tasks, conversation_turn_checkpoints TO everydayai')
        conn.execute((MIGRATIONS / '262_skill_legacy_pause_removal.sql').read_text())
    return env


def remove(svc, pid, version=None):
    return svc.delete(pid, ExpectedVersion(expected_version=version or svc.detail(pid)['draft']['version']))


def task(env, *, pid, status='running', kind='directory', org=None, connection=None):
    tid, turn = uuid4(), uuid4()
    candidate = {'package_id': str(pid), 'skill_key': 'orders', 'revision': 'v1'}
    runtime = {'version': 1, 'turn_id': str(turn), 'directory': [], 'active': []}
    params = {}
    if kind in ('directory', 'wrapped', 'active'):
        runtime['directory'] = [candidate]
    if kind == 'active':
        runtime['active'] = [{'skill_key': 'orders', 'revision': 'v1'}]
    if kind in ('selection', 'string_params'):
        params = {'_selected_skill': {'skill_id': 'orders', 'revision': 'v1'}}
        if kind == 'string_params':
            import json
            params = json.dumps(params)
    if kind == 'malformed':
        runtime['directory'] = [{}]
    if kind == 'noncanonical':
        runtime['directory'] = [{**candidate, 'package_id': str(pid).replace('-', '')}]
    if kind == 'wrong_turn':
        runtime['turn_id'] = str(uuid4())
    state = {'skill_runtime': runtime}
    if kind == 'wrapped':
        state = {'payload': state}
    def insert(conn):
        conn.execute('INSERT INTO tasks(id,org_id,turn_id,status,request_params) VALUES (%s,%s,%s,%s,%s)',
                     (tid, org or env.org, turn, status, Jsonb(params)))
        if kind != 'no_checkpoint':
            conn.execute('INSERT INTO conversation_turn_checkpoints(task_id,turn_id,state) VALUES (%s,%s,%s)',
                         (tid, turn, Jsonb(state)))
    if connection:
        insert(connection)
    else:
        with env.pool.connection(privileged=True) as conn:
            insert(conn)
    return tid


def deprecated(env):
    svc = env.service()
    pid = create(svc)
    publish(svc, pid)
    action(svc, pid, 'deprecate')
    return svc, pid


def test_disabled_can_deprecate_only_audited_revisions_without_republishing(environment):
    svc = environment.service()
    pid = create(svc)
    first = publish(svc, pid)
    action(svc, pid, 'start_draft')
    publish(svc, pid)
    files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in environment.root.rglob('SKILL.md')}
    disabled = action(svc, pid, 'disable')
    result = action(svc, pid, 'deprecate', disabled['draft']['version'])
    assert result['draft']['status'] == 'deprecated' and result['available_revision'] is None
    assert len(result['revisions']) == 2 and {r['status'] for r in result['revisions']} == {'deprecated'}
    assert svc.repository.catalog_candidates() == []
    assert svc.repository.assigned_revision(pid, first['draft']['revision'], restoring=True).status == 'deprecated'
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == files
    for operation in ('enable', 'disable', 'start_draft', 'deprecate'):
        with pytest.raises(SkillError, match='TRANSITION_INVALID'):
            action(svc, pid, operation)
    with environment.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE package_id=%s AND action='deprecate' AND from_state='disabled' AND to_state='deprecated'", (pid,)).fetchone()[0] == 3


@pytest.mark.parametrize('damage', ['missing', 'tampered'])
def test_disabled_deprecation_validates_nas_before_reopening_old_tasks(environment, damage):
    svc = environment.service()
    pid = create(svc)
    publish(svc, pid)
    path = environment.root / svc.repository.enabled_revisions()[0].nas_path
    before = action(svc, pid, 'disable')
    if damage == 'missing': path.unlink()
    else:
        path.chmod(0o600)
        path.write_text('tampered')
    with pytest.raises(SkillError): action(svc, pid, 'deprecate')
    assert svc.detail(pid) == before


def test_deprecation_of_disabled_draft_needs_no_nas(environment):
    svc = environment.service()
    pid = create(svc)
    action(svc, pid, 'disable')
    environment.config.skill_storage_root = '/not-a-configured-nas'
    assert action(svc, pid, 'deprecate')['draft']['status'] == 'deprecated'


def test_safe_removal_hides_management_preserves_all_history_and_reserves_key(removal):
    svc, pid = deprecated(removal)
    before = svc.detail(pid)
    rev = before['revisions'][0]['revision']
    files = {p: p.read_bytes() for p in removal.root.rglob('SKILL.md')}
    assert svc.deletion_check(pid) == {'allowed': True, 'reason': None, 'blocking_tasks': 0, 'uncertain_tasks': 0}
    assert remove(svc, pid)['deleted'] is True
    assert svc.list() == []
    for operation in (lambda: svc.detail(pid), lambda: svc.read_revision(pid, rev), lambda: action(svc, pid, 'enable')):
        with pytest.raises(SkillError, match='PACKAGE_UNAVAILABLE'): operation()
    with pytest.raises(SkillError, match='KEY_EXISTS'): create(svc)
    assert svc.repository.catalog_candidates() == []
    assert svc.repository.assigned_revision(pid, rev, restoring=True).status == 'deprecated'
    assert {p: p.read_bytes() for p in files} == files
    with removal.pool.connection(privileged=True) as conn:
        row = conn.execute('SELECT status,version,deleted_by,deleted_at IS NOT NULL FROM skill_drafts WHERE package_id=%s', (pid,)).fetchone()
        assert row == ('deprecated', before['draft']['version'] + 1, removal.actor, True)
        assert conn.execute("SELECT from_state,to_state,actor_user_id FROM skill_change_audits WHERE package_id=%s AND action='delete'", (pid,)).fetchall() == [('deprecated','deleted',removal.actor)]
        with pytest.raises(psycopg.errors.RaiseException, match='FORWARD_FIX'):
            with conn.transaction(): conn.execute((MIGRATIONS/'rollback/261_skill_safe_removal_rollback.sql').read_text())
    with pytest.raises(psycopg.errors.CheckViolation, match='PACKAGE_DELETED'):
        with svc._transaction('enable') as cursor:
            cursor.execute("UPDATE skill_drafts SET deleted_at=NULL,deleted_by=NULL,version=version+1 WHERE package_id=%s", (pid,))


@pytest.mark.parametrize('status,kind', [
    ('pending','selection'), ('running','directory'), ('paused','active'), ('paused','wrapped'),
    ('running','no_checkpoint'), ('paused','malformed'), ('pending','string_params'), ('paused','wrong_turn'), ('paused','noncanonical'),
])
def test_live_or_uncertain_references_block_delete_until_task_ends(removal, status, kind):
    svc, pid = deprecated(removal)
    tid = task(removal, pid=pid, status=status, kind=kind)
    check = svc.deletion_check(pid)
    assert not check['allowed'] and check['blocking_tasks'] + check['uncertain_tasks'] > 0
    with pytest.raises(SkillError, match='SKILL_DELETE_'): remove(svc, pid)
    with removal.pool.connection(privileged=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (tid,))
    # Terminal tasks may retain ready checkpoints; they do not prevent removal.
    assert svc.deletion_check(pid)['allowed']
    assert remove(svc, pid)['deleted']


def test_other_org_and_known_unrelated_task_do_not_block(removal):
    svc, pid = deprecated(removal)
    task(removal, pid=pid, org=removal.other)
    task(removal, pid=uuid4())
    task(removal, pid=pid, status='pending', kind='no_checkpoint')
    assert svc.deletion_check(pid)['allowed']


def test_removal_rechecks_after_concurrent_task_write(removal):
    svc, pid = deprecated(removal)
    version = svc.detail(pid)['draft']['version']
    assert svc.deletion_check(pid)['allowed']
    started = Event()
    def deletion():
        started.set()
        return remove(removal.service(), pid, version)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with removal.pool.connection(privileged=True) as writer:
            task(removal, pid=pid, status='pending', kind='selection', connection=writer)
            future = executor.submit(deletion)
            assert started.wait(2)
            with pytest.raises(TimeoutError): future.result(timeout=.15)
        with pytest.raises(SkillError, match='DELETE_IN_USE'): future.result(timeout=4)
    assert svc.detail(pid)['draft']['status'] == 'deprecated'


def test_concurrent_removal_succeeds_once_and_audits_once(removal):
    svc, pid = deprecated(removal)
    version = svc.detail(pid)['draft']['version']
    def deletion(_):
        try: return remove(removal.service(), pid, version)['deleted']
        except SkillError as error: return str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(deletion, [0,1]), key=str) == ['SKILL_PACKAGE_UNAVAILABLE', True]
    with removal.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE package_id=%s AND action='delete'", (pid,)).fetchone()[0] == 1


def test_deleted_state_and_permissions_cannot_be_bypassed(removal):
    svc, pid = deprecated(removal)
    for unauthorized in (removal.service(owner=removal.other), removal.service(access=DatabaseAccessKind.RUNTIME)):
        with pytest.raises(SkillError): unauthorized.deletion_check(pid)
        with pytest.raises(SkillError): remove(unauthorized, pid, 5)
    platform = removal.service(owner=None)
    global_pid = create(platform, 'platform')
    with pytest.raises(SkillError, match='OWNER_SCOPE_MISMATCH'): svc.deletion_check(global_pid)
    with pytest.raises(SkillError, match='VERSION_CONFLICT'): remove(svc, pid, 1)
    task(removal, pid=pid)
    with pytest.raises(psycopg.errors.CheckViolation, match='DELETE_IN_USE'):
        with svc._transaction('delete') as cursor:
            cursor.execute('UPDATE skill_drafts SET deleted_at=now(),deleted_by=%s,version=version+1 WHERE package_id=%s', (removal.actor,pid))
    assert svc.detail(pid)['draft']['status'] == 'deprecated'


def test_rls_and_missing_check_table_fail_closed(removal):
    svc, pid = deprecated(removal)
    with removal.pool.connection(privileged=True) as conn:
        conn.execute('ALTER TABLE tasks ENABLE ROW LEVEL SECURITY')
        conn.execute('CREATE POLICY invisible ON tasks FOR ALL TO everydayai USING (false)')
    with pytest.raises(psycopg.errors.InsufficientPrivilege): svc.deletion_check(pid)
    with pytest.raises(psycopg.errors.InsufficientPrivilege): remove(svc, pid)
    with removal.pool.connection(privileged=True) as conn:
        conn.execute('ALTER TABLE tasks DISABLE ROW LEVEL SECURITY')
        conn.execute('DROP TABLE conversation_turn_checkpoints')
    with pytest.raises(psycopg.errors.UndefinedTable): svc.deletion_check(pid)
    with pytest.raises(psycopg.errors.UndefinedTable): remove(svc, pid)
    assert svc.detail(pid)['draft']['status'] == 'deprecated'


def test_legacy_disabled_deprecated_can_restore_then_be_removed(removal):
    svc, pid = deprecated(removal)
    # Seed the exact state an older 260 deployment could produce.
    old_guard = (MIGRATIONS/'260_skill_reenable.sql').read_text().split('CREATE OR REPLACE FUNCTION public.skill_authoring_guard()')[1]
    new_guard = (MIGRATIONS/'261_skill_safe_removal.sql').read_text().split('CREATE OR REPLACE FUNCTION public.skill_authoring_guard()')[1].split('CREATE OR REPLACE FUNCTION public.skill_record_change()')[0]
    with removal.pool.connection(privileged=True) as conn:
        conn.execute('CREATE OR REPLACE FUNCTION public.skill_authoring_guard()'+old_guard)
    with svc._transaction('disable') as cursor:
        cursor.execute("UPDATE skill_revisions SET status='disabled' WHERE package_id=%s", (pid,))
        cursor.execute("UPDATE skill_drafts SET status='disabled',version=version+1 WHERE package_id=%s", (pid,))
    with removal.pool.connection(privileged=True) as conn:
        conn.execute('CREATE OR REPLACE FUNCTION public.skill_authoring_guard()'+new_guard)
    assert action(svc,pid,'enable')['draft']['status'] == 'deprecated'
    assert remove(svc,pid)['deleted']


@pytest.mark.parametrize('operation', ['revision', 'assignment'])
def test_removal_serializes_with_repository_writes(removal, operation):
    svc, pid = deprecated(removal)
    revision = svc.repository.assigned_revision(pid, svc.detail(pid)['revisions'][0]['revision'], restoring=True)
    started = Event()
    def mutation():
        started.set()
        repo = removal.service().repository
        if operation == 'revision': return repo.retire_revision(pid, revision.id)
        return repo.set_assignment(pid, revision.id, enabled=False)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with svc._transaction('delete') as cursor:
            cursor.execute('SELECT * FROM skill_drafts WHERE package_id=%s FOR UPDATE', (pid,))
            future = executor.submit(mutation)
            assert started.wait(2)
            with pytest.raises(TimeoutError): future.result(timeout=.15)
            cursor.execute('UPDATE skill_drafts SET deleted_at=now(),deleted_by=%s,version=version+1 WHERE package_id=%s', (removal.actor,pid))
        with pytest.raises(psycopg.errors.CheckViolation, match='PACKAGE_DELETED'): future.result(timeout=4)
    assert svc.list() == []
    assert svc.repository.assigned_revision(pid, revision.revision, restoring=True).status == 'deprecated'


def test_removal_database_failure_keeps_state_and_audit_atomic(removal):
    svc, pid = deprecated(removal)
    with removal.pool.connection(privileged=True) as conn:
        conn.execute("""CREATE FUNCTION reject_removal_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='delete' THEN RAISE EXCEPTION 'injected audit failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER reject_removal_audit BEFORE INSERT ON skill_change_audits
                FOR EACH ROW EXECUTE FUNCTION reject_removal_audit();""")
    before = svc.detail(pid)
    with pytest.raises(psycopg.errors.RaiseException, match='injected audit failure'): remove(svc,pid)
    assert svc.detail(pid) == before and len(svc.list()) == 1
    with removal.pool.connection(privileged=True) as conn:
        assert conn.execute("SELECT count(*) FROM skill_change_audits WHERE package_id=%s AND action='delete'", (pid,)).fetchone()[0] == 0
        conn.execute('DROP TRIGGER reject_removal_audit ON skill_change_audits')
    assert remove(svc,pid)['deleted']


def test_concurrent_disable_exit_accepts_one_action(environment):
    svc = environment.service()
    pid = create(svc)
    publish(svc,pid)
    version = action(svc,pid,'disable')['draft']['version']
    def transition(name):
        try: return action(environment.service(),pid,name,version)['draft']['status']
        except SkillError as error: return str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(transition,['enable','deprecate']))
    assert results.count('SKILL_VERSION_CONFLICT') == 1
    assert svc.detail(pid)['draft']['status'] in ('published','deprecated')


@pytest.mark.parametrize('status', ['draft', 'in_review', 'published', 'disabled'])
def test_only_deprecated_skills_can_be_removed(removal, status):
    svc = removal.service()
    pid = create(svc)
    if status == 'in_review': action(svc,pid,'submit')
    elif status == 'published': publish(svc,pid)
    elif status == 'disabled': action(svc,pid,'disable')
    assert svc.deletion_check(pid)['reason'] == 'SKILL_DEPRECATION_REQUIRED'
    with pytest.raises(SkillError, match='TRANSITION_INVALID'): remove(svc,pid)
    assert len(svc.list()) == 1


def test_direct_deprecation_keeps_independently_disabled_revisions_and_revoked_grants_closed(environment):
    svc = environment.service()
    pid = create(svc)
    first = publish(svc,pid)
    action(svc,pid,'start_draft')
    publish(svc,pid)
    last = svc.repository.enabled_revisions()[0]
    with svc._transaction('disable') as cursor:
        cursor.execute("UPDATE skill_revisions SET status='disabled' WHERE package_id=%s AND revision=%s", (pid, first['draft']['revision']))
    svc.repository.set_assignment(pid,last.id,enabled=False)
    action(svc,pid,'disable')
    result = action(svc,pid,'deprecate')
    assert next(r['status'] for r in result['revisions'] if r['revision'] == first['draft']['revision']) == 'disabled'
    assert result['available_revision'] is None and svc.repository.catalog_candidates() == []
    with pytest.raises(SkillError, match='PINNED_REVISION_UNAVAILABLE'):
        svc.repository.assigned_revision(pid,last.revision,restoring=True)
