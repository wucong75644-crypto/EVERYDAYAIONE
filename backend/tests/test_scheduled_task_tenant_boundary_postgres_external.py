"""Migration 180 real PostgreSQL role and RLS matrix."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest


pytestmark = pytest.mark.external
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/180_scheduled_task_tenant_boundary.sql"
).read_text(encoding="utf-8")


def _database_url() -> str:
    url = os.getenv("SCHEDULED_RLS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SCHEDULED_RLS_TEST_DATABASE_URL_REQUIRED")
    return url


@pytest.fixture()
def scheduled_facts() -> dict[str, str]:
    user_a, user_b, org_a, org_b = (str(uuid4()) for _ in range(4))
    task_a, task_b, run_a = (str(uuid4()) for _ in range(3))
    setup = """
        DROP TABLE IF EXISTS scheduled_task_runs, scheduled_tasks,
            org_members, organizations CASCADE;
        DROP FUNCTION IF EXISTS tenant_actor_is_active_member(UUID);
        DROP FUNCTION IF EXISTS tenant_actor_user_id();
        DROP FUNCTION IF EXISTS tenant_org_id();
        DO $roles$
        BEGIN
            IF to_regrole('everydayai_owner') IS NULL THEN
                CREATE ROLE everydayai_owner NOLOGIN;
            END IF;
            IF to_regrole('everydayai_runtime') IS NULL THEN
                CREATE ROLE everydayai_runtime NOLOGIN;
            END IF;
            IF to_regrole('everydayai_wecom_runtime') IS NULL THEN
                CREATE ROLE everydayai_wecom_runtime NOLOGIN;
            END IF;
            IF to_regrole('everydayai_worker') IS NULL THEN
                CREATE ROLE everydayai_worker NOLOGIN;
            END IF;
        END
        $roles$;
        GRANT everydayai_owner, everydayai_runtime,
            everydayai_wecom_runtime, everydayai_worker TO postgres;
        GRANT USAGE, CREATE ON SCHEMA public TO everydayai_owner;
        SET ROLE everydayai_owner;
        CREATE TABLE organizations(
            id UUID PRIMARY KEY,
            status TEXT NOT NULL
        );
        CREATE TABLE org_members(
            org_id UUID NOT NULL,
            user_id UUID NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE scheduled_tasks(
            id UUID PRIMARY KEY,
            org_id UUID NOT NULL,
            user_id UUID NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE scheduled_task_runs(
            id UUID PRIMARY KEY,
            task_id UUID NOT NULL,
            org_id UUID NOT NULL,
            status TEXT NOT NULL
        );
        CREATE FUNCTION tenant_actor_user_id()
        RETURNS UUID LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('app.actor_user_id', TRUE), '')::UUID
        $$;
        CREATE FUNCTION tenant_org_id()
        RETURNS UUID LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('app.org_id', TRUE), '')::UUID
        $$;
        CREATE FUNCTION tenant_actor_is_active_member(p_org_id UUID)
        RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
            SELECT EXISTS (
                SELECT 1
                  FROM org_members member
                  JOIN organizations organization
                    ON organization.id = member.org_id
                 WHERE member.org_id = p_org_id
                   AND member.user_id = tenant_actor_user_id()
                   AND member.status = 'active'
                   AND organization.status = 'active'
            )
        $$;
        RESET ROLE;
    """
    with psycopg.connect(_database_url()) as connection:
        connection.execute(setup)
        connection.execute(MIGRATION)
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO organizations VALUES (%s, 'active'), (%s, 'active')",
            (org_a, org_b),
        )
        connection.execute(
            "INSERT INTO org_members VALUES "
            "(%s, %s, 'active'), (%s, %s, 'active')",
            (org_a, user_a, org_b, user_b),
        )
        connection.execute(
            "INSERT INTO scheduled_tasks VALUES "
            "(%s, %s, %s, 'active'), (%s, %s, %s, 'active')",
            (task_a, org_a, user_a, task_b, org_b, user_b),
        )
        connection.execute(
            "INSERT INTO scheduled_task_runs VALUES (%s, %s, %s, 'running')",
            (run_a, task_a, org_a),
        )
        connection.commit()
    return {
        "user_a": user_a,
        "org_a": org_a,
        "org_b": org_b,
    }


def test_runtime_is_tenant_scoped_and_worker_has_no_table_access(
    scheduled_facts: dict[str, str],
) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute("SET ROLE everydayai_runtime")
        connection.execute(
            "SELECT set_config('app.actor_user_id', %s, false), "
            "set_config('app.org_id', %s, false)",
            (scheduled_facts["user_a"], scheduled_facts["org_a"]),
        )
        assert connection.execute(
            "SELECT count(*) FROM scheduled_tasks"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM scheduled_task_runs"
        ).fetchone() == (1,)
        connection.rollback()

        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE org_members SET status = 'disabled' "
            "WHERE org_id = %s AND user_id = %s",
            (scheduled_facts["org_a"], scheduled_facts["user_a"]),
        )
        connection.commit()
        connection.execute("SET ROLE everydayai_runtime")
        connection.execute(
            "SELECT set_config('app.actor_user_id', %s, false), "
            "set_config('app.org_id', %s, false)",
            (scheduled_facts["user_a"], scheduled_facts["org_a"]),
        )
        assert connection.execute(
            "SELECT count(*) FROM scheduled_tasks"
        ).fetchone() == (0,)
        connection.rollback()

        connection.execute("SET ROLE everydayai_worker")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM scheduled_tasks")
