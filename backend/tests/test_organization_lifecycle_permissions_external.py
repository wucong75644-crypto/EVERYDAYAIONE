"""Real PostgreSQL lifecycle scope, ACL, and suspended-write matrix."""

from __future__ import annotations

from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from core.db_scope import SET_DATABASE_SCOPE_SQL
from testing.tenant_role_matrix import TenantRoleMatrixConfig
from tests.test_organization_lifecycle_external import (
    DENIED_DATABASE_ROLES,
    FENCE_HELPER,
    MIGRATIONS,
    ROLLBACKS,
    SERVICE_ROLES,
    _assert_acl_matrix,
    _assert_migration_objects,
    _assert_rollback_objects,
    _execute_migration,
    _role_call,
    _run_preflight,
    _scope_call,
    lifecycle_database,
    lifecycle_facts,
)


pytestmark = pytest.mark.external


def test_transition_and_audit_rollback_together(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
) -> None:
    config, facts = lifecycle_database, lifecycle_facts
    with psycopg.connect(config.runtime_url) as runtime:
        runtime.execute(
            SET_DATABASE_SCOPE_SQL,
            (facts["actor"], "", "runtime", "org-lifecycle-rollback"),
        )
        runtime.execute(
            "SELECT suspend_governed_organization(%s)", (facts["org"],),
        )
        runtime.rollback()
    with psycopg.connect(config.admin_url) as admin:
        state, audits = admin.execute(
            "SELECT status, (SELECT count(*) FROM governance_audit_log "
            "WHERE org_id = %s) FROM organizations WHERE id = %s",
            (facts["org"], facts["org"]),
        ).fetchone()
    assert (state, audits) == ("active", 0)


@pytest.mark.parametrize(
    ("actor_key", "org_scope", "access_kind"),
    (
        ("ordinary", "", "runtime"),
        ("disabled", "", "runtime"),
        ("owner", "", "runtime"),
        ("admin", "", "runtime"),
        ("member", "", "runtime"),
        ("actor", "org", "runtime"),
        ("actor", "", ""),
        ("actor", "", "worker"),
    ),
)
def test_runtime_scope_and_actor_matrix(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
    actor_key: str,
    org_scope: str,
    access_kind: str,
) -> None:
    config, facts = lifecycle_database, lifecycle_facts
    resolved_scope = facts["org"] if org_scope else ""
    with psycopg.connect(config.runtime_url) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _scope_call(
                runtime, facts[actor_key], resolved_scope, access_kind,
                "suspend_governed_organization", facts["org"],
            )


@pytest.mark.parametrize(
    "actor_id", ("", "00000000-0000-0000-0000-000000000001"),
)
def test_missing_or_unknown_actor_is_denied(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
    actor_id: str,
) -> None:
    with psycopg.connect(lifecycle_database.runtime_url) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _scope_call(
                runtime, actor_id, "", "runtime",
                "suspend_governed_organization", lifecycle_facts["org"],
            )


def test_malformed_actor_uuid_is_rejected(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
) -> None:
    with psycopg.connect(lifecycle_database.runtime_url) as runtime:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            _scope_call(
                runtime, "not-a-uuid", "", "runtime",
                "suspend_governed_organization", lifecycle_facts["org"],
            )


def test_database_role_acl_and_direct_table_denial(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
) -> None:
    config, facts = lifecycle_database, lifecycle_facts
    with psycopg.connect(config.admin_url) as admin:
        _assert_acl_matrix(admin)
    for role_name in DENIED_DATABASE_ROLES:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _role_call(
                config.admin_url, role_name,
                "suspend_governed_organization", facts["org"],
            )
    with psycopg.connect(config.runtime_url) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(
                "UPDATE organizations SET status = 'suspended' WHERE id = %s",
                (facts["org"],),
            )


def test_reverse_rollback_preserves_organization_facts(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
) -> None:
    config, facts = lifecycle_database, lifecycle_facts
    with psycopg.connect(config.admin_url) as admin:
        admin.execute(
            "INSERT INTO org_configs(org_id, config_key, config_value_encrypted) "
            "VALUES (%s, 'lifecycle_test_marker', 'synthetic-test-value')",
            (facts["org"],),
        )
        admin.execute(
            "INSERT INTO governance_audit_log("
            "org_id, actor_id, authority, action, target_kind, metadata"
            ") VALUES (%s, %s, 'super_admin', 'lifecycle.test.marker', "
            "'organization', '{}'::JSONB)",
            (facts["org"], facts["actor"]),
        )
        admin.execute(
            "UPDATE organizations SET status = 'suspended' WHERE id = %s",
            (facts["org"],),
        )
        admin.commit()
        _execute_migration(admin, ROLLBACKS[0])
        _assert_rollback_objects(admin, expect_217=True, expect_218=False)
        _execute_migration(admin, ROLLBACKS[1])
        _assert_rollback_objects(admin, expect_217=False, expect_218=False)
        preserved = admin.execute(
            "SELECT organization.status, "
            "(SELECT count(*) FROM org_members WHERE org_id = organization.id), "
            "(SELECT count(*) FROM org_configs WHERE org_id = organization.id), "
            "(SELECT count(*) FROM governance_audit_log "
            "WHERE org_id = organization.id AND action = 'lifecycle.test.marker') "
            "FROM organizations organization WHERE organization.id = %s",
            (facts["org"],),
        ).fetchone()
        assert preserved == ("suspended", 3, 1, 1)
        for migration in MIGRATIONS:
            _execute_migration(admin, migration)
        _assert_migration_objects(admin)
        admin.execute(
            "UPDATE organizations SET status = 'active' WHERE id = %s",
            (facts["org"],),
        )
        admin.commit()
    _run_preflight(config)


def _role_call_fence(
    config: TenantRoleMatrixConfig,
    role_name: str,
    facts: dict[str, str],
    conversation_id: str,
) -> str | None:
    url_by_role = {
        "everydayai_runtime": config.runtime_url,
        "everydayai_worker": config.worker_url,
        "everydayai_wecom_runtime": config.wecom_url,
    }
    statement = sql.SQL("SELECT {}(%s, %s, %s)").format(
        sql.Identifier(FENCE_HELPER),
    )
    if role_name in url_by_role:
        with psycopg.connect(url_by_role[role_name]) as connection:
            return connection.execute(
                statement,
                (facts["actor"], conversation_id, facts["org"]),
            ).fetchone()[0]
    with psycopg.connect(config.admin_url) as connection:
        with connection.transaction():
            connection.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(role_name),
                )
            )
            return connection.execute(
                statement,
                (facts["actor"], conversation_id, facts["org"]),
            ).fetchone()[0]


@pytest.mark.parametrize("role_name", SERVICE_ROLES)
def test_suspended_write_fence_and_active_recovery(
    lifecycle_database: TenantRoleMatrixConfig,
    lifecycle_facts: dict[str, str],
    role_name: str,
) -> None:
    config, facts = lifecycle_database, lifecycle_facts
    conversation_id = str(uuid4())
    with psycopg.connect(config.admin_url) as admin:
        admin.execute(
            "INSERT INTO conversations(id, user_id, title, org_id) "
            "VALUES (%s, %s, %s, %s)",
            (conversation_id, facts["actor"], "fence", facts["org"]),
        )
        admin.execute(
            "UPDATE organizations SET status = 'suspended' WHERE id = %s",
            (facts["org"],),
        )
        admin.commit()
    try:
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege,
            match="ORGANIZATION_EXECUTION_SUSPENDED",
        ):
            _role_call_fence(config, role_name, facts, conversation_id)
        with psycopg.connect(config.admin_url) as admin:
            admin.execute(
                "UPDATE organizations SET status = 'active' WHERE id = %s",
                (facts["org"],),
            )
            admin.commit()
        assert _role_call_fence(
            config, role_name, facts, conversation_id,
        ) is not None
    finally:
        with psycopg.connect(config.admin_url) as admin:
            admin.execute("DELETE FROM tasks WHERE org_id = %s", (facts["org"],))
            admin.execute(
                "DELETE FROM conversations WHERE id = %s", (conversation_id,),
            )
            admin.execute(
                "UPDATE organizations SET status = 'active' WHERE id = %s",
                (facts["org"],),
            )
            admin.commit()
