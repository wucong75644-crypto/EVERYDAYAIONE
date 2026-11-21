"""Real threaded LocalDB reads preserve explicit RLS scope and fresh authority."""
import asyncio
import getpass
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient
from core.local_db import LocalDBClient
from services.permissions.checker import PermissionChecker
from tests.test_scheduled_task_draft_delete_integration import postgres_socket  # noqa: F401


@pytest.fixture(scope='module')
def permission_db(postgres_socket):
    dsn = psycopg.conninfo.make_conninfo(host=postgres_socket, dbname='postgres', user=getpass.getuser())
    name = 'skill_permission_' + uuid4().hex
    org, a, b, boss, member = [str(uuid4()) for _ in range(5)]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute('CREATE ROLE permission_reader LOGIN')
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    test_dsn = psycopg.conninfo.make_conninfo(dsn, dbname=name)
    with psycopg.connect(test_dsn) as c:
        c.execute('''CREATE TABLE org_member_assignments (
            id uuid, user_id uuid, org_id uuid, department_id uuid, position_id uuid,
            job_title text, data_scope text, data_scope_dept_ids uuid[], perm_version integer,
            is_primary boolean)''')
        c.execute('CREATE TABLE org_positions(id uuid, code text)')
        c.execute('ALTER TABLE org_member_assignments ENABLE ROW LEVEL SECURITY')
        c.execute('''CREATE POLICY actor_scope ON org_member_assignments USING (
            user_id::text=current_setting('app.actor_user_id',true)
            AND org_id::text=current_setting('app.org_id',true))''')
        c.execute('GRANT SELECT ON org_member_assignments,org_positions TO permission_reader')
        c.execute("INSERT INTO org_positions VALUES (%s,'boss'),(%s,'member')", (boss, member))
        for actor, position in [(a, boss), (b, member)]:
            c.execute('''INSERT INTO org_member_assignments(id,user_id,org_id,position_id,
                data_scope,perm_version,is_primary) VALUES (%s,%s,%s,%s,'all',1,true)''',
                (str(uuid4()), actor, org, position))
    client = LocalDBClient(psycopg.conninfo.make_conninfo(test_dsn, user='permission_reader'), min_size=1, max_size=4)
    def checker(actor):
        scope = DatabaseScope(actor, org, DatabaseAccessKind.AUTHORIZATION)
        return PermissionChecker(ScopedDatabaseClient(client, scope))
    try:
        yield SimpleNamespace(org=org, a=a, b=b, boss=boss, member=member, checker=checker, dsn=test_dsn)
    finally:
        client.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            admin.execute('DROP ROLE permission_reader')


async def test_concurrent_permission_reads_keep_each_actors_rls_scope(permission_db):
    env = permission_db
    first, second = env.checker(env.a), env.checker(env.b)
    results = await asyncio.gather(*[checker.check_many(actor, env.org, ['order.view', 'task.execute'])
        for _ in range(8) for checker, actor in [(first, env.a), (second, env.b)]])
    assert results[::2] == [frozenset({'order.view', 'task.execute'})] * 8
    assert results[1::2] == [frozenset()] * 8
    # A caller-supplied user ID cannot override the trusted database scope.
    assert await first.check_many(env.b, env.org, ['order.view']) == frozenset()


async def test_next_batch_observes_real_permission_revocation(permission_db):
    env = permission_db
    checker = env.checker(env.a)
    assert await checker.check_many(env.a, env.org, ['order.view']) == {'order.view'}
    try:
        with psycopg.connect(env.dsn) as c:
            c.execute('UPDATE org_member_assignments SET position_id=%s WHERE user_id=%s', (env.member, env.a))
        assert await checker.check_many(env.a, env.org, ['order.view']) == frozenset()
    finally:
        with psycopg.connect(env.dsn) as c:
            c.execute('UPDATE org_member_assignments SET position_id=%s WHERE user_id=%s', (env.boss, env.a))
