"""迁移 153/154 的真实 PostgreSQL 登录角色隔离矩阵。

仅在显式提供隔离测试库 URL、数据库名确认和 RUN_TENANT_DB_MATRIX=1 时运行。
"""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from core.db_scope import SET_DATABASE_SCOPE_SQL
from testing.tenant_role_matrix import (
    TenantMatrixConfigError,
    TenantRoleMatrixConfig,
)


pytestmark = pytest.mark.external

RLS_TABLES = (
    "users",
    "organizations",
    "org_members",
    "org_configs",
    "wecom_user_mappings",
    "wecom_chat_targets",
    "conversations",
    "messages",
    "tasks",
    "credits_history",
    "credit_transactions",
    "image_generations",
    "detail_projects",
    "detail_project_images",
    "refresh_tokens",
    "user_subscriptions",
    "user_memory_settings",
)


@pytest.fixture(scope="module")
def matrix_config() -> TenantRoleMatrixConfig:
    try:
        return TenantRoleMatrixConfig.from_mapping(os.environ)
    except TenantMatrixConfigError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def tenant_facts(matrix_config):
    user_a, user_b = str(uuid4()), str(uuid4())
    org_a, org_b = str(uuid4()), str(uuid4())
    suffix = uuid4().hex
    phone_a, phone_b = f"17{suffix[:9]}", f"18{suffix[:9]}"
    with psycopg.connect(matrix_config.runtime_url) as runtime:
        _register(runtime, user_a, phone_a)
        _register(runtime, user_b, phone_b)
    with psycopg.connect(matrix_config.admin_url) as admin:
        with admin.transaction():
            admin.execute(
                "INSERT INTO organizations(id, name, owner_id) "
                "VALUES (%s, %s, %s), (%s, %s, %s)",
                (
                    org_a, f"matrix-a-{suffix}", user_a,
                    org_b, f"matrix-b-{suffix}", user_b,
                ),
            )
            admin.execute(
                "INSERT INTO org_members(org_id, user_id, role, status) "
                "VALUES (%s, %s, 'owner', 'active'), "
                "(%s, %s, 'owner', 'active')",
                (org_a, user_a, org_b, user_b),
            )
    facts = {
        "user_a": user_a, "user_b": user_b,
        "org_a": org_a, "org_b": org_b,
    }
    yield facts
    with psycopg.connect(matrix_config.admin_url) as admin:
        with admin.transaction():
            admin.execute(
                "DELETE FROM organizations WHERE id IN (%s, %s)",
                (org_a, org_b),
            )
            admin.execute(
                "DELETE FROM users WHERE id IN (%s, %s)",
                (user_a, user_b),
            )


def _register(connection, user_id: str, phone: str) -> None:
    with connection.transaction():
        connection.execute(
            SET_DATABASE_SCOPE_SQL, ("", "", "runtime", "role-matrix"),
        )
        row = connection.execute(
            "SELECT register_web_identity(%s, %s, %s, NULL, %s, "
            "NOW() + INTERVAL '1 day')",
            (user_id, phone, "矩阵用户", uuid4().hex + uuid4().hex),
        ).fetchone()
        assert str(row[0]["id"]) == user_id


def _scoped_count(
    database_url: str,
    actor_id: str | None,
    org_id: str | None,
    sql: str,
    params: tuple,
) -> int:
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute(
                SET_DATABASE_SCOPE_SQL,
                (actor_id or "", org_id or "", "runtime", "role-matrix"),
            )
            return connection.execute(sql, params).fetchone()[0]


def test_matrix_preconditions_are_real_roles_and_rls(matrix_config) -> None:
    with psycopg.connect(matrix_config.admin_url) as connection:
        roles = dict(connection.execute(
            "SELECT rolname, rolbypassrls FROM pg_roles "
            "WHERE rolname = ANY(%s)",
            ([
                "everydayai_runtime", "everydayai_wecom_runtime",
                "everydayai_worker",
            ],),
        ).fetchall())
        rls = connection.execute(
            "SELECT COUNT(*) FROM pg_class "
            "WHERE relname = ANY(%s) AND relrowsecurity",
            (list(RLS_TABLES),),
        ).fetchone()[0]
    assert roles == {
        "everydayai_runtime": False,
        "everydayai_wecom_runtime": False,
        "everydayai_worker": False,
    }
    assert rls == len(RLS_TABLES)


def test_web_runtime_individual_and_cross_tenant_isolation(
    matrix_config, tenant_facts,
) -> None:
    own = _scoped_count(
        matrix_config.runtime_url, tenant_facts["user_a"], None,
        "SELECT COUNT(*) FROM users WHERE id = %s",
        (tenant_facts["user_a"],),
    )
    other = _scoped_count(
        matrix_config.runtime_url, tenant_facts["user_a"], None,
        "SELECT COUNT(*) FROM users WHERE id = %s",
        (tenant_facts["user_b"],),
    )
    wrong_org = _scoped_count(
        matrix_config.runtime_url,
        tenant_facts["user_a"], tenant_facts["org_b"],
        "SELECT COUNT(*) FROM organizations WHERE id = %s",
        (tenant_facts["org_b"],),
    )
    assert (own, other, wrong_org) == (1, 0, 0)


def test_disabled_employee_loses_enterprise_visibility(
    matrix_config, tenant_facts,
) -> None:
    with psycopg.connect(matrix_config.admin_url) as admin:
        admin.execute(
            "UPDATE org_members SET status = 'disabled' "
            "WHERE org_id = %s AND user_id = %s",
            (tenant_facts["org_a"], tenant_facts["user_a"]),
        )
        admin.commit()
    try:
        visible = _scoped_count(
            matrix_config.runtime_url,
            tenant_facts["user_a"], tenant_facts["org_a"],
            "SELECT COUNT(*) FROM organizations WHERE id = %s",
            (tenant_facts["org_a"],),
        )
        assert visible == 0
    finally:
        with psycopg.connect(matrix_config.admin_url) as admin:
            admin.execute(
                "UPDATE org_members SET status = 'active' "
                "WHERE org_id = %s AND user_id = %s",
                (tenant_facts["org_a"], tenant_facts["user_a"]),
            )
            admin.commit()


def test_sensitive_tables_and_auth_rpc_are_role_partitioned(
    matrix_config,
) -> None:
    with psycopg.connect(matrix_config.runtime_url) as runtime:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("SELECT token_hash FROM refresh_tokens LIMIT 1")
    with psycopg.connect(matrix_config.worker_url) as worker:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SELECT id FROM users LIMIT 1")
    for url in (matrix_config.wecom_url, matrix_config.worker_url):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "SELECT lookup_web_auth_candidate(%s, NULL)",
                    ("17000000000",),
                )


def test_public_and_service_execute_grants_match_matrix(matrix_config) -> None:
    signature = (
        "update_wecom_conversation_setting(uuid,uuid,text,text,uuid)"
    )
    with psycopg.connect(matrix_config.admin_url) as connection:
        public_auth = connection.execute(
            "SELECT has_function_privilege("
            "'public', 'lookup_web_auth_candidate(text,text)', 'EXECUTE')"
        ).fetchone()[0]
        wecom_message = connection.execute(
            "SELECT has_function_privilege("
            "'everydayai_wecom_runtime', %s, 'EXECUTE')",
            (signature,),
        ).fetchone()[0]
        worker_message = connection.execute(
            "SELECT has_function_privilege("
            "'everydayai_worker', %s, 'EXECUTE')",
            (signature,),
        ).fetchone()[0]
    assert public_auth is False
    assert wecom_message is True
    assert worker_message is False
