"""迁移 165 的真实 PostgreSQL Memory Runtime 隔离矩阵。

仅在目标测试库已执行 165，且显式配置 RUN_TENANT_DB_MATRIX=1 时运行。
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

MEMORY_TABLES = (
    "memory_pipeline_state",
    "memory_session_logs",
    "memory_consolidation_runs",
    "memory_atoms",
)


@pytest.fixture(scope="module")
def matrix_config() -> TenantRoleMatrixConfig:
    try:
        return TenantRoleMatrixConfig.from_mapping(os.environ)
    except TenantMatrixConfigError as exc:
        pytest.skip(str(exc))


def _set_scope(
    connection: psycopg.Connection,
    user_id: str,
    org_id: str,
) -> None:
    connection.execute(
        SET_DATABASE_SCOPE_SQL,
        (user_id, org_id, "worker", "memory-role-matrix"),
    )


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _commit_flush(
    connection: psycopg.Connection,
    org_id: str,
    user_id: str,
    conversation_id: str,
) -> dict:
    return connection.execute(
        "SELECT commit_memory_session_flush("
        "%s::uuid, %s::uuid, %s::uuid, 0, 1, 'matrix', "
        "'{}'::jsonb, '[]'::jsonb, %s, 'matrix-model', 'matrix-v1')",
        (org_id, user_id, conversation_id, uuid4().hex),
    ).fetchone()[0]


def _commit_consolidation(
    connection: psycopg.Connection,
    org_id: str,
    user_id: str,
    source_log_ids: list[str],
) -> dict:
    return connection.execute(
        "SELECT commit_memory_consolidation("
        "%s::uuid, %s::uuid, %s::uuid[], %s, '[]'::jsonb, "
        "'matrix-model', 'matrix-v1', '{}'::jsonb)",
        (org_id, user_id, source_log_ids, uuid4().hex),
    ).fetchone()[0]


def test_memory_tables_are_owned_and_force_rls(matrix_config) -> None:
    with psycopg.connect(matrix_config.admin_url) as connection:
        rows = connection.execute(
            "SELECT c.relname, owner.rolname, c.relrowsecurity, "
            "c.relforcerowsecurity "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_roles owner ON owner.oid = c.relowner "
            "WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
            (list(MEMORY_TABLES),),
        ).fetchall()
    assert {
        _text(name): (_text(owner), rls, forced)
        for name, owner, rls, forced in rows
    } == {
        table: ("everydayai_owner", True, True)
        for table in MEMORY_TABLES
    }


def _seed_pipeline_facts(
    matrix_config: TenantRoleMatrixConfig,
) -> tuple[str, str, str, str, list[str], list[str]]:
    user_a, user_b = str(uuid4()), str(uuid4())
    org_a, org_b = str(uuid4()), str(uuid4())
    conversations_a = [str(uuid4()) for _ in range(3)]
    conversations_b = [str(uuid4()) for _ in range(3)]
    suffix = uuid4().hex
    with psycopg.connect(matrix_config.admin_url) as admin:
        with admin.transaction():
            admin.execute(
                "INSERT INTO users(id, phone, nickname) VALUES "
                "(%s, %s, 'memory-a'), (%s, %s, 'memory-b')",
                (user_a, f"17{uuid4().hex[:9]}", user_b, f"18{uuid4().hex[:9]}"),
            )
            admin.execute(
                "INSERT INTO organizations(id, name, owner_id) VALUES "
                "(%s, %s, %s), (%s, %s, %s)",
                (
                    org_a, f"memory-a-{suffix}", user_a,
                    org_b, f"memory-b-{suffix}", user_b,
                ),
            )
            admin.execute(
                "INSERT INTO org_members(org_id, user_id, role, status) "
                "VALUES (%s, %s, 'owner', 'active'), "
                "(%s, %s, 'owner', 'active')",
                (org_a, user_a, org_b, user_b),
            )
            admin.execute(
                "INSERT INTO conversations("
                "id, user_id, org_id, scope_type, title"
                ") SELECT value::uuid, %s::uuid, %s::uuid, 'user', 'a' "
                "FROM unnest(%s::uuid[]) value",
                (user_a, org_a, conversations_a),
            )
            admin.execute(
                "INSERT INTO conversations("
                "id, user_id, org_id, scope_type, title"
                ") SELECT value::uuid, %s::uuid, %s::uuid, 'user', 'b' "
                "FROM unnest(%s::uuid[]) value",
                (user_b, org_b, conversations_b),
            )
            admin.execute(
                "INSERT INTO memory_pipeline_state("
                "org_id, user_id, session_id"
                ") SELECT %s::uuid, %s::uuid, value::uuid "
                "FROM unnest(%s::uuid[]) value",
                (org_a, user_a, conversations_a),
            )
            admin.execute(
                "INSERT INTO memory_pipeline_state("
                "org_id, user_id, session_id"
                ") SELECT %s::uuid, %s::uuid, value::uuid "
                "FROM unnest(%s::uuid[]) value",
                (org_b, user_b, conversations_b),
            )
            admin.execute(
                "INSERT INTO memory_atoms("
                "org_id, user_id, content, type, priority"
                ") VALUES (%s, %s, 'atom-a', 'instruction', 50), "
                "(%s, %s, 'atom-b', 'instruction', 50)",
                (org_a, user_a, org_b, user_b),
            )
    return user_a, user_b, org_a, org_b, conversations_a, conversations_b


def _tenant_counts(
    connection: psycopg.Connection,
    table: str,
    user_a: str,
    user_b: str,
) -> tuple[int, int]:
    allowed = {
        "memory_pipeline_state",
        "memory_session_logs",
        "memory_consolidation_runs",
        "memory_atoms",
    }
    if table not in allowed:
        raise ValueError("unsupported memory table")
    return connection.execute(
        f"SELECT COUNT(*) FILTER (WHERE user_id = %s), "
        f"COUNT(*) FILTER (WHERE user_id = %s) FROM {table}",
        (user_a, user_b),
    ).fetchone()


def _exercise_pipeline(
    matrix_config: TenantRoleMatrixConfig,
    facts: tuple[str, str, str, str, list[str], list[str]],
) -> None:
    user_a, user_b, org_a, org_b, conversations_a, conversations_b = facts
    with psycopg.connect(matrix_config.worker_url) as worker_b:
        with worker_b.transaction():
            _set_scope(worker_b, user_b, org_b)
            logs_b = [
                str(_commit_flush(
                    worker_b, org_b, user_b, conversation_id,
                )["session_log_id"])
                for conversation_id in conversations_b
            ]
            assert _commit_consolidation(
                worker_b, org_b, user_b, logs_b,
            )["outcome"] == "committed"

    with psycopg.connect(matrix_config.worker_url) as worker:
        with worker.transaction():
            _set_scope(worker, user_a, org_a)
            pipeline_counts = _tenant_counts(
                worker, "memory_pipeline_state", user_a, user_b,
            )
            atom_counts = _tenant_counts(
                worker, "memory_atoms", user_a, user_b,
            )
            logs_a = [
                str(_commit_flush(
                    worker, org_a, user_a, conversation_id,
                )["session_log_id"])
                for conversation_id in conversations_a
            ]
            session_counts = _tenant_counts(
                worker, "memory_session_logs", user_a, user_b,
            )
            result_a = _commit_consolidation(
                worker, org_a, user_a, logs_a,
            )
            run_counts = _tenant_counts(
                worker, "memory_consolidation_runs", user_a, user_b,
            )
            cross_sources = _commit_consolidation(
                worker, org_a, user_a, logs_b,
            )
            assert pipeline_counts == (3, 0)
            assert atom_counts == (1, 0)
            assert session_counts == (3, 0)
            assert run_counts == (1, 0)
            assert result_a["outcome"] == "committed"
            assert cross_sources["outcome"] == "stale_sources"
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute(
                    "DELETE FROM memory_pipeline_state WHERE user_id = %s",
                    (user_a,),
                )


def _cleanup_pipeline(
    matrix_config: TenantRoleMatrixConfig,
    facts: tuple[str, str, str, str, list[str], list[str]],
) -> None:
    user_a, user_b, org_a, org_b, conversations_a, conversations_b = facts
    all_conversations = conversations_a + conversations_b
    with psycopg.connect(matrix_config.admin_url) as admin:
        with admin.transaction():
            admin.execute(
                "DELETE FROM memory_atoms WHERE user_id IN (%s, %s)",
                (user_a, user_b),
            )
            admin.execute(
                "DELETE FROM memory_session_logs "
                "WHERE conversation_id = ANY(%s::uuid[])",
                (all_conversations,),
            )
            admin.execute(
                "DELETE FROM memory_consolidation_runs "
                "WHERE user_id IN (%s, %s)",
                (user_a, user_b),
            )
            admin.execute(
                "DELETE FROM memory_pipeline_state "
                "WHERE session_id = ANY(%s::uuid[])",
                (all_conversations,),
            )
            admin.execute(
                "DELETE FROM conversations WHERE id = ANY(%s::uuid[])",
                (all_conversations,),
            )
            admin.execute(
                "DELETE FROM organizations WHERE id IN (%s, %s)",
                (org_a, org_b),
            )
            admin.execute(
                "DELETE FROM users WHERE id IN (%s, %s)",
                (user_a, user_b),
            )


def test_worker_executes_full_pipeline_with_tenant_isolation(
    matrix_config,
) -> None:
    facts = _seed_pipeline_facts(matrix_config)
    try:
        _exercise_pipeline(matrix_config, facts)
    finally:
        _cleanup_pipeline(matrix_config, facts)
