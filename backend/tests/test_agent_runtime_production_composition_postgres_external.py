"""Real PostgreSQL migration 223 apply, rollback guard, ACL, and reapply."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_model_attempt_postgres_external import (
    CREDITS_BOOTSTRAP,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("AR223_TEST_DATABASE_URL", "")
MIGRATIONS = tuple(
    ROOT / "migrations" / name
    for name in (
        "212_agent_runtime_core_foundation.sql",
        "213_agent_runtime_session_run_rpcs.sql",
        "214_agent_runtime_run_lifecycle_rpcs.sql",
        "215_agent_runtime_model_event_projection_rpcs.sql",
        "217_01_agent_runtime_model_attempt_foundation.sql",
        "217_02_agent_runtime_model_attempt_credits.sql",
        "217_03_agent_runtime_model_attempt_lifecycle.sql",
        "217_04_agent_runtime_model_attempt_reconciliation.sql",
        "218_01_agent_runtime_action_foundation.sql",
        "218_01a_agent_runtime_action_terminal_helpers.sql",
        "218_02_agent_runtime_action_tool_terminal.sql",
        "218_02a_agent_runtime_action_result_helpers.sql",
        "218_03_agent_runtime_action_lifecycle.sql",
        "218_04_agent_runtime_action_reconciliation.sql",
        "219_01_agent_runtime_command_claim_foundation.sql",
        "219_02_agent_runtime_command_claim_lifecycle.sql",
        "219_02a_agent_runtime_command_claim_terminal_compatibility.sql",
        "220_01_agent_runtime_model_result_foundation.sql",
        "220_02_agent_runtime_coordinator_recovery.sql",
        "220_03_agent_runtime_model_result_terminal.sql",
        "220_04_agent_runtime_action_recovery.sql",
        "220_11_agent_runtime_compat_projection_foundation.sql",
        "220_12_agent_runtime_compat_projection_rpcs.sql",
        "220_21_agent_runtime_authorization_foundation.sql",
        "220_22_agent_runtime_authorization_rpcs.sql",
        "220_23_agent_runtime_accepted_cancel_override.sql",
        "220_24_agent_runtime_authorization_dispatch_gate.sql",
        "220_25_agent_runtime_authorization_recovery.sql",
        "220_26_agent_runtime_projection_dead_recovery.sql",
        "222_01_agent_runtime_sandbox_job_foundation.sql",
        "222_02_agent_runtime_sandbox_job_rpcs.sql",
        "222_03_agent_runtime_sandbox_job_recovery_rpcs.sql",
    )
)
MIGRATION_223 = ROOT / "migrations/223_agent_runtime_production_composition.sql"
ROLLBACK_223 = (
    ROOT / "migrations/rollback/"
    "223_agent_runtime_production_composition_rollback.sql"
)
HELPERS = (
    "_agent_compat_project_command(agent_runtime_events)",
    "_agent_compat_project_completed_run("
    "agent_runs,agent_runtime_sessions,agent_session_commands,tasks)",
    "_agent_compat_project_run(agent_runtime_events,text)",
)
PROJECTION_RPCS = (
    "claim_agent_compat_projection_outbox(integer,integer)",
    "apply_agent_compat_projection(uuid,uuid,text)",
    "get_agent_compat_projection_result(uuid)",
)


def _execute(
    sql: str, params: tuple[object, ...] | None = None,
) -> list[tuple[object, ...]]:
    with psycopg.connect(DATABASE_URL) as connection:
        cursor = connection.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


def _file(path: Path, *, check: bool = True) -> None:
    sql = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("\\")
    )
    try:
        _execute(sql)
    except psycopg.Error:
        if check:
            raise
        raise


@pytest.fixture(scope="module", autouse=True)
def database() -> None:
    if os.getenv("RUN_AR223_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR223_DB_TEST=1 and AR223_TEST_DATABASE_URL required")
    if "ar223" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR223 database required")
    _file(ROOT / "tests/fixtures/agent_runtime_core_postgres_bootstrap.sql")
    _file(ROOT / "tests/fixtures/agent_runtime_compat_projection_legacy.sql")
    _execute(CREDITS_BOOTSTRAP)
    _execute("""
        SET ROLE everydayai_owner;
        ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user';
        RESET ROLE;
        DO $roles$
        DECLARE role_name TEXT;
        BEGIN
          FOREACH role_name IN ARRAY ARRAY[
            'everydayai_sandbox_worker',
            'everydayai_agent_runtime_worker',
            'everydayai_projection_worker',
            'everydayai_authorization_worker',
            'everydayai_runtime_admin'
          ] LOOP
            IF to_regrole(role_name) IS NULL THEN
              EXECUTE format('CREATE ROLE %I NOLOGIN', role_name);
            END IF;
            EXECUTE format('GRANT %I TO CURRENT_USER', role_name);
          END LOOP;
        END
        $roles$;
    """)
    for migration in MIGRATIONS:
        _file(migration)
    _file(MIGRATION_223)


def _privilege_matrix(
    functions: tuple[str, ...],
) -> dict[tuple[str, str], bool]:
    roles = (
        "public", "everydayai_worker", "everydayai_runtime",
        "everydayai_wecom_runtime", "everydayai_sync", "everydayai",
        "everydayai_projection_worker",
    )
    values: dict[tuple[str, str], bool] = {}
    for role in roles:
        for function in functions:
            [[allowed]] = _execute(
                "SELECT has_function_privilege("
                f"'{role}','{function}','EXECUTE')",
            )
            values[(role, function)] = bool(allowed)
    return values


def test_rollback_guard_precedes_acl_changes() -> None:
    before = _privilege_matrix((*HELPERS, *PROJECTION_RPCS))
    try:
        _execute("""
            SET ROLE everydayai_owner;
            INSERT INTO agent_runtime_worker_heartbeats(
              process_role,worker_id,release_revision,ready,draining,status_code
            ) VALUES ('sandbox','guard-worker','test',false,true,'guard');
            RESET ROLE;
        """)
        with pytest.raises(
            psycopg.Error, match="AGENT_RUNTIME_223_ROLLBACK_GUARD_FACTS_EXIST",
        ):
            _file(ROLLBACK_223)
        assert _privilege_matrix((*HELPERS, *PROJECTION_RPCS)) == before
    finally:
        _execute("""
            SET ROLE everydayai_owner;
            DELETE FROM agent_runtime_worker_heartbeats
             WHERE worker_id = 'guard-worker';
            RESET ROLE;
        """)


def test_production_role_schema_and_create_matrix() -> None:
    expected_usage = {
        "everydayai_agent_runtime_worker": True,
        "everydayai_projection_worker": True,
        "everydayai_authorization_worker": True,
        "everydayai_sandbox_worker": True,
        "everydayai_runtime_admin": True,
        "everydayai_worker": False,
        "everydayai_runtime": False,
    }
    for role, expected in expected_usage.items():
        [[usage]] = _execute(
            "SELECT has_schema_privilege(%s, 'public', 'USAGE')", (role,),
        )
        [[create]] = _execute(
            "SELECT has_schema_privilege(%s, 'public', 'CREATE')", (role,),
        )
        assert bool(usage) is expected
        assert not create
def test_clean_rollback_effective_privileges_and_reapply() -> None:
    _execute("""
        SET ROLE everydayai_owner;
        DELETE FROM agent_runtime_worker_heartbeats
         WHERE worker_id = 'guard-worker';
        RESET ROLE;
    """)
    _file(ROLLBACK_223)
    helper_matrix = _privilege_matrix(HELPERS)
    assert not any(helper_matrix.values())
    rpc_matrix = _privilege_matrix(PROJECTION_RPCS)
    for function in PROJECTION_RPCS:
        for role in (
            "public", "everydayai_worker", "everydayai_runtime",
            "everydayai_wecom_runtime", "everydayai_sync", "everydayai",
            "everydayai_projection_worker",
        ):
            assert not rpc_matrix[(role, function)]
    _file(MIGRATION_223)
    assert not any(_privilege_matrix(HELPERS).values())
    _file(ROLLBACK_223)
    _file(MIGRATION_223)
