"""Old paused chats cannot depend on a Skill created after their checkpoint."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.skills.contracts import SkillError
from tests.test_skill_removal_postgres import (  # noqa: F401
    MIGRATIONS, removal, environment, postgres_socket, deprecated, remove, task, create, action,
)


def legacy_task(env, pid, *, wrapped=False):
    tid = task(env, pid=pid, status='paused', kind='unrelated')
    state = {'safe_point': 'before_model', 'messages': []}
    if wrapped:
        state = {'payload': state}
    with env.pool.connection(privileged=True) as conn:
        conn.execute('''UPDATE conversation_turn_checkpoints SET state=%s, status='paused',
            updated_at=(SELECT created_at - interval '2 days' FROM skill_packages WHERE id=%s)
            WHERE task_id=%s''', (Jsonb(state), pid, tid))
    return tid


@pytest.mark.parametrize('wrapped', [False, True])
def test_older_paused_chats_without_skill_records_do_not_block_removal(removal, wrapped):
    svc, pid = deprecated(removal)
    legacy_task(removal, pid, wrapped=wrapped)
    assert svc.deletion_check(pid) == {
        'allowed': True, 'reason': None, 'blocking_tasks': 0, 'uncertain_tasks': 0,
    }
    assert remove(svc, pid)['deleted']


@pytest.mark.parametrize('change', [
    'running', 'resumed', 'checkpoint_ready', 'checkpoint_invalid', 'wrong_turn',
    'same_time', 'newer', 'malformed_payload', 'null_runtime', 'wrapped_null_runtime',
    'selection', 'string_params', 'no_checkpoint',
])
def test_only_proven_legacy_snapshots_are_exempt(removal, change):
    svc, pid = deprecated(removal)
    tid = legacy_task(removal, pid)
    with removal.pool.connection(privileged=True) as conn:
        if change in ('running', 'resumed'):
            conn.execute('UPDATE tasks SET status=%s WHERE id=%s',
                         ('running' if change == 'running' else 'pending', tid))
        elif change in ('checkpoint_ready', 'checkpoint_invalid'):
            conn.execute('UPDATE conversation_turn_checkpoints SET status=%s WHERE task_id=%s',
                         (change.removeprefix('checkpoint_'), tid))
        elif change == 'wrong_turn':
            conn.execute('UPDATE conversation_turn_checkpoints SET turn_id=gen_random_uuid() WHERE task_id=%s', (tid,))
        elif change in ('same_time', 'newer'):
            conn.execute('''UPDATE conversation_turn_checkpoints SET updated_at=
                (SELECT created_at FROM skill_packages WHERE id=%s) + %s * interval '1 second'
                WHERE task_id=%s''', (pid, 0 if change == 'same_time' else 1, tid))
        elif change in ('malformed_payload', 'null_runtime', 'wrapped_null_runtime'):
            state = {'malformed_payload': {'payload': []}, 'null_runtime': {'skill_runtime': None},
                     'wrapped_null_runtime': {'payload': {'skill_runtime': None}}}[change]
            conn.execute('UPDATE conversation_turn_checkpoints SET state=%s WHERE task_id=%s', (Jsonb(state), tid))
        elif change in ('selection', 'string_params'):
            params = {'_selected_skill': {'skill_id': 'orders', 'revision': 'v1'}} if change == 'selection' else '{}'
            conn.execute('UPDATE tasks SET request_params=%s WHERE id=%s', (Jsonb(params), tid))
        else:
            conn.execute('DELETE FROM conversation_turn_checkpoints WHERE task_id=%s', (tid,))
    check = svc.deletion_check(pid)
    assert not check['allowed'] and check['uncertain_tasks'] == 1
    with pytest.raises(SkillError, match='DELETE_'): remove(svc, pid)


@pytest.mark.parametrize('kind', ['directory', 'active', 'selection'])
def test_legacy_exemption_never_hides_an_explicit_reference(removal, kind):
    svc, pid = deprecated(removal)
    legacy_task(removal, pid)
    tid = task(removal, pid=pid, status='paused', kind=kind)
    with removal.pool.connection(privileged=True) as conn:
        conn.execute('''UPDATE conversation_turn_checkpoints SET status='paused',
            updated_at=(SELECT created_at - interval '1 day' FROM skill_packages WHERE id=%s)
            WHERE task_id=%s''', (pid, tid))
    check = svc.deletion_check(pid)
    assert not check['allowed'] and check['blocking_tasks'] == 1 and check['uncertain_tasks'] == 0
    with pytest.raises(SkillError, match='DELETE_IN_USE'): remove(svc, pid)
    # The database trigger must enforce the same rule without the service precheck.
    with pytest.raises(psycopg.errors.CheckViolation, match='DELETE_IN_USE'):
        with svc._transaction('delete') as cursor:
            cursor.execute('''UPDATE skill_drafts SET deleted_at=now(),deleted_by=%s,version=version+1
                WHERE package_id=%s''', (removal.actor, pid))


def test_delete_rechecks_legacy_task_that_is_concurrently_resumed(removal):
    svc, pid = deprecated(removal)
    tid = legacy_task(removal, pid)
    version = svc.detail(pid)['draft']['version']
    assert svc.deletion_check(pid)['allowed']
    started = Event()
    def deletion():
        started.set()
        return remove(removal.service(), pid, version)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with removal.pool.connection(privileged=True) as conn:
            conn.execute("UPDATE tasks SET status='running' WHERE id=%s", (tid,))
            future = executor.submit(deletion)
            assert started.wait(2)
            with pytest.raises(TimeoutError): future.result(timeout=.15)
        with pytest.raises(SkillError, match='CHECK_UNCERTAIN'): future.result(timeout=4)
    assert svc.detail(pid)['draft']['status'] == 'deprecated'


def test_package_timestamp_lookup_is_org_scoped_and_restores_rls_setting(removal):
    svc, pid = deprecated(removal)
    with svc._transaction('deletion_check') as cursor:
        cursor.execute("SELECT set_config('row_security', 'off', true)")
        cursor.execute('SELECT skill_deletion_package_created_at(%s,%s) AS created', (pid, 'orders'))
        assert cursor.fetchone()['created'] is not None
        cursor.execute("SELECT current_setting('row_security') AS setting")
        assert cursor.fetchone()['setting'] == 'off'
        cursor.execute('SELECT skill_deletion_package_created_at(%s,%s) AS created', (pid, 'wrong-key'))
        assert cursor.fetchone()['created'] is None
    with removal.service(owner=removal.other)._transaction('deletion_check') as cursor:
        cursor.execute('SELECT skill_deletion_package_created_at(%s,%s) AS created', (pid, 'orders'))
        assert cursor.fetchone()['created'] is None
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match='PACKAGE_UNAVAILABLE'):
        with removal.service(owner=removal.other)._transaction('deletion_check') as cursor:
            cursor.execute('SELECT skill_deletion_blockers(%s,%s)', (pid, 'orders'))


def test_legacy_check_rollback_reapply_keeps_deleted_markers_and_audits(removal):
    svc, pid = deprecated(removal)
    legacy_task(removal, pid)
    assert remove(svc, pid)['deleted']
    other = create(svc, 'another-skill')
    action(svc, other, 'deprecate')
    with removal.pool.connection(privileged=True) as conn:
        before = conn.execute('SELECT deleted_at,deleted_by FROM skill_drafts WHERE package_id=%s', (pid,)).fetchone()
        audit = conn.execute('SELECT count(*) FROM skill_change_audits WHERE package_id=%s', (pid,)).fetchone()
        conn.execute((MIGRATIONS / 'rollback/262_skill_legacy_pause_removal_rollback.sql').read_text())
    assert svc.deletion_check(other)['uncertain_tasks'] == 1
    with removal.pool.connection(privileged=True) as conn:
        conn.execute((MIGRATIONS / '262_skill_legacy_pause_removal.sql').read_text())
        assert conn.execute('SELECT deleted_at,deleted_by FROM skill_drafts WHERE package_id=%s', (pid,)).fetchone() == before
        assert conn.execute('SELECT count(*) FROM skill_change_audits WHERE package_id=%s', (pid,)).fetchone() == audit
    assert svc.deletion_check(other)['allowed']
