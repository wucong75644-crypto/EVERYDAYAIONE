"""Real PostgreSQL organization lifecycle migration and role contract."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
from typing import Iterator
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from core.db_scope import SET_DATABASE_SCOPE_SQL
from scripts.migration_runner import run as run_migrations
from testing.tenant_role_matrix import (
    TenantMatrixConfigError,
    TenantRoleMatrixConfig,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
MIGRATION_IDENTITIES = (
    "217_organization_lifecycle_governance.sql",
    "218_suspended_organization_execution_fence.sql",
)
MIGRATIONS = tuple(ROOT / "migrations" / name for name in MIGRATION_IDENTITIES)
ROLLBACKS = (
    ROOT
    / "migrations/rollback/218_suspended_organization_execution_fence_rollback.sql",
    ROOT
    / "migrations/rollback/217_organization_lifecycle_governance_rollback.sql",
)
LIFECYCLE_FUNCTIONS = (
    "suspend_governed_organization",
    "restore_governed_organization",
)
SERVICE_ROLES = (
    "everydayai_runtime",
    "everydayai_worker",
    "everydayai_wecom_runtime",
    "everydayai_sync",
)
DENIED_DATABASE_ROLES = (
    "everydayai_worker",
    "everydayai_wecom_runtime",
    "everydayai_sync",
    "service_role",
    "everydayai",
    "lifecycle_test_unprivileged",
)
FENCE_TRIGGER_NAMES = tuple(
    f"{table}_suspended_organization_fence"
    for table in (
        "tasks scheduled_tasks scheduled_task_runs agent_runtime_sessions "
        "agent_session_commands agent_runs agent_run_attempts agent_model_steps "
        "agent_runtime_events agent_projection_outbox wecom_callback_inbox "
        "conversation_deliveries"
    ).split()
)
TEST_ROLE = "lifecycle_test_unprivileged"
FENCE_HELPER = "lifecycle_test_insert_task"


def _config() -> TenantRoleMatrixConfig:
    try:
        return TenantRoleMatrixConfig.from_mapping(os.environ)
    except TenantMatrixConfigError as error:
        pytest.skip(str(error))


def _execute_migration(connection: psycopg.Connection, path: Path) -> None:
    connection.execute(path.read_text(encoding="utf-8"))
    connection.commit()


def _remove_target_ledger(connection: psycopg.Connection) -> None:
    connection.execute(
        "DELETE FROM schema_migration_ledger WHERE identity = ANY(%s)",
        (list(MIGRATION_IDENTITIES),),
    )
    connection.commit()


def _apply_target_migrations(connection: psycopg.Connection) -> None:
    pending = run_migrations(
        connection, "plan", "organization-lifecycle-external",
    )
    assert pending == list(MIGRATION_IDENTITIES)
    applied = run_migrations(
        connection, "apply", "organization-lifecycle-external",
    )
    assert applied == list(MIGRATION_IDENTITIES)


def _run_preflight(config: TenantRoleMatrixConfig) -> None:
    environment = os.environ.copy()
    environment["TENANT_DB_ADMIN_URL"] = config.admin_url
    result = subprocess.run(
        ["bash", "deploy/preflight/organization-lifecycle.sh"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "只读检查通过" in result.stdout


def _scope_call(
    connection: psycopg.Connection,
    actor_id: str,
    org_scope: str,
    access_kind: str,
    function_name: str,
    org_id: str,
) -> dict:
    with connection.transaction():
        connection.execute(
            SET_DATABASE_SCOPE_SQL,
            (actor_id, org_scope, access_kind, "org-lifecycle-external"),
        )
        statement = sql.SQL("SELECT {}(%s)").format(
            sql.Identifier(function_name),
        )
        return connection.execute(statement, (org_id,)).fetchone()[0]


def _role_call(
    admin_url: str,
    role_name: str,
    function_name: str,
    org_id: str,
) -> None:
    with psycopg.connect(admin_url) as connection:
        with connection.transaction():
            connection.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(role_name),
                )
            )
            statement = sql.SQL("SELECT {}(%s)").format(
                sql.Identifier(function_name),
            )
            connection.execute(statement, (org_id,))


def _seed_user(
    connection: psycopg.Connection,
    role: str,
    status: str = "active",
) -> str:
    user_id = str(uuid4())
    connection.execute(
        "INSERT INTO users(id, nickname, role, status) VALUES (%s, %s, %s, %s)",
        (user_id, f"lifecycle-{role}-{uuid4().hex}", role, status),
    )
    return user_id


def _seed_org(
    connection: psycopg.Connection,
    owner_id: str,
    status: str = "active",
) -> str:
    org_id = str(uuid4())
    connection.execute(
        "INSERT INTO organizations(id, name, owner_id, status) "
        "VALUES (%s, %s, %s, %s)",
        (org_id, f"lifecycle-{uuid4().hex}", owner_id, status),
    )
    return org_id


def _assert_migration_objects(connection: psycopg.Connection) -> None:
    for function_name in LIFECYCLE_FUNCTIONS:
        row = connection.execute(
            """
            SELECT owner.rolname, procedure.prosecdef,
                   procedure.proconfig = ARRAY[
                       'search_path=pg_catalog, public'
                   ]::TEXT[]
              FROM pg_proc procedure
              JOIN pg_roles owner ON owner.oid = procedure.proowner
             WHERE procedure.oid = to_regprocedure(%s)
            """,
            (f"public.{function_name}(uuid)",),
        ).fetchone()
        assert row == ("everydayai_owner", True, True)
    trigger_count = connection.execute(
        "SELECT count(*) FROM pg_trigger WHERE tgname = ANY(%s) AND NOT tgisinternal",
        (list(FENCE_TRIGGER_NAMES),),
    ).fetchone()[0]
    assert trigger_count == len(FENCE_TRIGGER_NAMES)


def _assert_acl_matrix(connection: psycopg.Connection) -> None:
    for function_name in LIFECYCLE_FUNCTIONS:
        signature = f"public.{function_name}(uuid)"
        assert connection.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            ("everydayai_runtime", signature),
        ).fetchone()[0]
        for role_name in ("public", *DENIED_DATABASE_ROLES):
            role_exists = role_name == "public" or connection.execute(
                "SELECT to_regrole(%s) IS NOT NULL", (role_name,),
            ).fetchone()[0]
            assert role_exists, f"required test role missing: {role_name}"
            assert not connection.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                (role_name, signature),
            ).fetchone()[0]
        grantable = connection.execute(
            """
            SELECT bool_or(acl.is_grantable)
              FROM pg_proc procedure
              CROSS JOIN LATERAL aclexplode(procedure.proacl) acl
             WHERE procedure.oid = to_regprocedure(%s)
               AND acl.grantee = 'everydayai_runtime'::regrole
            """,
            (signature,),
        ).fetchone()[0]
        assert grantable is False
    assert not connection.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_auth_members membership
             WHERE membership.member = ANY(
                 SELECT oid FROM pg_roles
                  WHERE rolname = ANY(%s)
             )
               AND membership.roleid = 'everydayai_runtime'::regrole
        )
        """,
        (list(DENIED_DATABASE_ROLES),),
    ).fetchone()[0]
    assert not connection.execute(
        "SELECT has_table_privilege("
        "'everydayai_runtime', 'organizations', 'SELECT,UPDATE')"
    ).fetchone()[0]
    for role_name in DENIED_DATABASE_ROLES:
        assert not connection.execute(
            "SELECT has_table_privilege(%s, 'organizations', 'UPDATE')",
            (role_name,),
        ).fetchone()[0]


def _assert_rollback_objects(
    connection: psycopg.Connection,
    expect_217: bool,
    expect_218: bool,
) -> None:
    for function_name in LIFECYCLE_FUNCTIONS:
        exists = connection.execute(
            "SELECT to_regprocedure(%s) IS NOT NULL",
            (f"public.{function_name}(uuid)",),
        ).fetchone()[0]
        assert exists is expect_217
    fence_exists = connection.execute(
        "SELECT to_regprocedure("
        "'public.reject_suspended_organization_service_write()') IS NOT NULL"
    ).fetchone()[0]
    assert fence_exists is expect_218
    trigger_count = connection.execute(
        "SELECT count(*) FROM pg_trigger WHERE tgname = ANY(%s) AND NOT tgisinternal",
        (list(FENCE_TRIGGER_NAMES),),
    ).fetchone()[0]
    assert trigger_count == (len(FENCE_TRIGGER_NAMES) if expect_218 else 0)


def _create_test_roles_and_helper(connection: psycopg.Connection) -> None:
    if not connection.execute(
        "SELECT to_regrole(%s) IS NOT NULL", (TEST_ROLE,),
    ).fetchone()[0]:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN INHERIT").format(
                sql.Identifier(TEST_ROLE),
            )
        )
    connection.execute(
        sql.SQL("""
        CREATE FUNCTION {}(
            p_user UUID, p_conversation UUID, p_org UUID
        ) RETURNS UUID
        LANGUAGE SQL SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            INSERT INTO public.tasks(
                user_id, conversation_id, type, status, org_id
            ) VALUES (p_user, p_conversation, 'chat', 'pending', p_org)
            RETURNING id
        $$;
        ALTER FUNCTION {}(UUID, UUID, UUID)
            OWNER TO everydayai_owner;
        GRANT EXECUTE ON FUNCTION {}(UUID, UUID, UUID)
            TO everydayai_runtime, everydayai_worker,
               everydayai_wecom_runtime, everydayai_sync;
        """).format(
            sql.Identifier(FENCE_HELPER),
            sql.Identifier(FENCE_HELPER),
            sql.Identifier(FENCE_HELPER),
        )
    )
    connection.commit()


def _drop_test_roles_and_helper(connection: psycopg.Connection) -> None:
    connection.execute(
        sql.SQL("DROP FUNCTION IF EXISTS {}(UUID, UUID, UUID)").format(
            sql.Identifier(FENCE_HELPER),
        )
    )
    connection.execute(
        sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(TEST_ROLE))
    )
    connection.commit()


@pytest.fixture(scope="module")
def lifecycle_database() -> Iterator[TenantRoleMatrixConfig]:
    config = _config()
    setup_complete = False
    try:
        with psycopg.connect(config.admin_url) as admin:
            _apply_target_migrations(admin)
            _create_test_roles_and_helper(admin)
            _assert_migration_objects(admin)
            _assert_acl_matrix(admin)
        _run_preflight(config)
        setup_complete = True
        yield config
    finally:
        with psycopg.connect(config.admin_url) as admin:
            _drop_test_roles_and_helper(admin)
            fence_exists, lifecycle_exists = admin.execute(
                "SELECT to_regprocedure('public."
                "reject_suspended_organization_service_write()') IS NOT NULL, "
                "to_regprocedure('public."
                "suspend_governed_organization(uuid)') IS NOT NULL"
            ).fetchone()
            if fence_exists:
                _execute_migration(admin, ROLLBACKS[0])
            if setup_complete:
                _assert_rollback_objects(
                    admin, expect_217=True, expect_218=False,
                )
            if lifecycle_exists:
                _execute_migration(admin, ROLLBACKS[1])
            if setup_complete:
                _assert_rollback_objects(
                    admin, expect_217=False, expect_218=False,
                )
            _remove_target_ledger(admin)
            if setup_complete:
                _apply_target_migrations(admin)
                _assert_migration_objects(admin)
        if setup_complete:
            _run_preflight(config)
            with psycopg.connect(config.admin_url) as admin:
                _execute_migration(admin, ROLLBACKS[0])
                _execute_migration(admin, ROLLBACKS[1])
                _remove_target_ledger(admin)


@pytest.fixture
def lifecycle_facts(
    lifecycle_database: TenantRoleMatrixConfig,
) -> Iterator[dict[str, str]]:
    config = lifecycle_database
    with psycopg.connect(config.admin_url) as admin:
        actor_id = _seed_user(admin, "super_admin")
        ordinary_id = _seed_user(admin, "user")
        disabled_id = _seed_user(admin, "super_admin", "disabled")
        owner_id = _seed_user(admin, "user")
        admin_id = _seed_user(admin, "user")
        member_id = _seed_user(admin, "user")
        org_id = _seed_org(admin, actor_id)
        admin.execute(
            "INSERT INTO org_members(org_id, user_id, role, status) VALUES "
            "(%s, %s, 'owner', 'active'), (%s, %s, 'admin', 'active'), "
            "(%s, %s, 'member', 'active')",
            (org_id, owner_id, org_id, admin_id, org_id, member_id),
        )
        admin.commit()
    facts = {
        "actor": actor_id,
        "ordinary": ordinary_id,
        "disabled": disabled_id,
        "owner": owner_id,
        "admin": admin_id,
        "member": member_id,
        "org": org_id,
    }
    try:
        yield facts
    finally:
        with psycopg.connect(config.admin_url) as admin:
            admin.execute("DELETE FROM tasks WHERE org_id = %s", (org_id,))
            admin.execute(
                "DELETE FROM governance_audit_log WHERE org_id = %s", (org_id,)
            )
            admin.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
            admin.execute(
                "DELETE FROM users WHERE id = ANY(%s)",
                ([value for key, value in facts.items() if key != "org"],),
            )
            admin.commit()


def test_transition_conflicts_missing_and_audit(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
) -> None:
    config, facts = lifecycle_database, lifecycle_facts
    with psycopg.connect(config.runtime_url) as runtime:
        suspended = _scope_call(
            runtime, facts["actor"], "", "runtime",
            "suspend_governed_organization", facts["org"],
        )
        assert suspended["status"] == "suspended"
        with pytest.raises(psycopg.errors.CheckViolation):
            _scope_call(
                runtime, facts["actor"], "", "runtime",
                "suspend_governed_organization", facts["org"],
            )
        restored = _scope_call(
            runtime, facts["actor"], "", "runtime",
            "restore_governed_organization", facts["org"],
        )
        assert restored["status"] == "active"
        with pytest.raises(psycopg.errors.CheckViolation):
            _scope_call(
                runtime, facts["actor"], "", "runtime",
                "restore_governed_organization", facts["org"],
            )
        with pytest.raises(psycopg.errors.NoDataFound):
            _scope_call(
                runtime, facts["actor"], "", "runtime",
                "suspend_governed_organization", str(uuid4()),
            )
    with psycopg.connect(config.admin_url) as admin:
        actions = admin.execute(
            "SELECT action FROM governance_audit_log "
            "WHERE org_id = %s ORDER BY created_at",
            (facts["org"],),
        ).fetchall()
    assert [row[0] for row in actions] == [
        "organization.suspend", "organization.restore",
    ]


def test_concurrent_transition_has_one_winner(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
) -> None:
    config, facts = lifecycle_database, lifecycle_facts

    def suspend() -> str:
        with psycopg.connect(config.runtime_url) as runtime:
            result = _scope_call(
                runtime, facts["actor"], "", "runtime",
                "suspend_governed_organization", facts["org"],
            )
            return result["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(suspend) for _ in range(2)]
    outcomes: list[str] = []
    for future in futures:
        try:
            outcomes.append(f"success:{future.result()}")
        except psycopg.errors.CheckViolation:
            outcomes.append("conflict")
    assert sorted(outcomes) == ["conflict", "success:suspended"]
    with psycopg.connect(config.admin_url) as admin:
        state, audits = admin.execute(
            "SELECT status, (SELECT count(*) FROM governance_audit_log "
            "WHERE org_id = %s AND action = 'organization.suspend') "
            "FROM organizations WHERE id = %s",
            (facts["org"], facts["org"]),
        ).fetchone()
    assert (state, audits) == ("suspended", 1)
