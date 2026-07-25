"""150–163 production tenant cutover preflight contract tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/preflight-tenant-cutover.sh"
MIGRATION_IDENTITIES = (
    "150_agent_runtime_tenant_defense.sql",
    "151_agent_runtime_role_grants.sql",
    "152_wecom_runtime_capability.sql",
    "153_runtime_message_rls_and_auth.sql",
    "154_wecom_message_rpc_facades.sql",
    "155_web_wecom_oauth_capabilities.sql",
    "156_governance_authority_foundation.sql",
    "157_governance_write_capabilities.sql",
    "158_configuration_control_plane_foundation.sql",
    "159_configuration_management_core.sql",
    "159_configuration_management_facades.sql",
    "160_configuration_resolution_core.sql",
    "160_configuration_resolution_facades.sql",
    "161_configuration_legacy_import.sql",
    "162_configuration_legacy_export_access.sql",
    "163_conversation_actor_worker_discovery.sql",
)


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sql_path = tmp_path / "captured.sql"
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        f"#!/bin/sh\nprintf '%s' \"$*\" > '{tmp_path / 'psql-args'}'\n"
        f"cat > '{sql_path}'\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TENANT_DB_ADMIN_URL": (
            "postgresql://admin:secret@localhost/everydayai"
        ),
        "LEGACY_DATABASE_OWNER": "everydayai",
    }, sql_path


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_preflight_requires_admin_url_before_psql(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)
    del env["TENANT_DB_ADMIN_URL"]

    result = _run(env)

    assert result.returncode == 1
    assert "缺少 TENANT_DB_ADMIN_URL" in result.stderr
    assert not sql_path.exists()


def test_preflight_rejects_unsafe_legacy_owner(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)
    env["LEGACY_DATABASE_OWNER"] = "everydayai; DROP ROLE unsafe"

    result = _run(env)

    assert result.returncode == 1
    assert "不是合法 PostgreSQL 角色名" in result.stderr
    assert not sql_path.exists()


def test_preflight_is_read_only_and_hides_admin_url(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(env)

    assert result.returncode == 0
    assert "只读前置检查通过" in result.stdout
    assert env["TENANT_DB_ADMIN_URL"] not in result.stdout + result.stderr
    arguments = (tmp_path / "psql-args").read_text(encoding="utf-8")
    assert env["TENANT_DB_ADMIN_URL"] not in arguments
    sql = sql_path.read_text(encoding="utf-8")
    assert "BEGIN;" in sql
    assert "SET TRANSACTION READ ONLY;" in sql
    assert "ROLLBACK;" in sql
    for forbidden in (
        "INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE",
        "CREATE TABLE", "COMMIT;",
    ):
        assert forbidden not in sql


def test_preflight_pins_complete_migration_ledger_contract(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    sql = sql_path.read_text(encoding="utf-8")
    for identity in MIGRATION_IDENTITIES:
        assert identity in sql
    assert sql.count("sql',") >= len(MIGRATION_IDENTITIES) - 1
    assert "checksum_sha256 <> expected.checksum" in sql
    assert "TENANT_CUTOVER_MIGRATION_INVALID" in sql
    assert "TENANT_CUTOVER_MIGRATION_PARTIAL" in sql
    assert "applied_migrations NOT IN (0, cardinality(expected_migrations))" in sql


def test_preflight_pins_current_162_checksum(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    checksum = hashlib.sha256(
        (
            ROOT
            / "backend/migrations"
            / "162_configuration_legacy_export_access.sql"
        ).read_bytes()
    ).hexdigest()
    assert checksum in sql_path.read_text(encoding="utf-8")


def test_preflight_pins_current_163_checksum(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    checksum = hashlib.sha256(
        (
            ROOT
            / "backend/migrations"
            / "163_conversation_actor_worker_discovery.sql"
        ).read_bytes()
    ).hexdigest()
    assert checksum in sql_path.read_text(encoding="utf-8")


def test_preflight_checks_actor_worker_capability_boundary(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    sql = sql_path.read_text(encoding="utf-8")
    for function in (
        "discover_generation_turn_candidates(integer)",
        "worker_get_claimed_generation_task(uuid,uuid)",
        "worker_commit_generation_turn_with_context_v2",
        "worker_fail_generation_turn(uuid,uuid,text,text)",
    ):
        assert function in sql
    assert "procedure.prosecdef" in sql
    assert "acl.grantee = 0" in sql
    assert "TENANT_CUTOVER_ACTOR_WORKER_CAPABILITY_INVALID" in sql
    assert "TENANT_CUTOVER_ACTOR_WORKER_DIRECT_ACCESS_INVALID" in sql
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert f"'public.tasks', '{privilege}'" in sql
    assert sql.count("has_any_column_privilege(") == 3


def test_preflight_checks_roles_owners_and_object_boundaries(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    sql = sql_path.read_text(encoding="utf-8")
    for role in (
        "everydayai_owner",
        "everydayai_config_import_reader",
        "everydayai_migrator",
        "everydayai_runtime",
        "everydayai_wecom_runtime",
        "everydayai_worker",
    ):
        assert role in sql
    for contract in (
        "TENANT_CUTOVER_PREFLIGHT_REQUIRES_ADMIN",
        "TENANT_CUTOVER_ROLE_SET_PARTIAL",
        "TENANT_CUTOVER_ROLE_CONTRACT_INVALID",
        "TENANT_CUTOVER_OWNER_MEMBERSHIP_INVALID",
        "TENANT_CUTOVER_TABLE_MISSING",
        "TENANT_CUTOVER_FUNCTION_MISSING",
        "TENANT_CUTOVER_OWNERSHIP_PARTIAL",
        "TENANT_CUTOVER_OWNER_UNEXPECTED",
        "TENANT_CUTOVER_FUNCTION_OWNER_INVALID",
        "TENANT_CUTOVER_FORCE_RLS_UNEXPECTED",
        "TENANT_CUTOVER_CONFIG_EXPORT_ACCESS_INVALID",
    ):
        assert contract in sql
    assert sql.count("'conversation_artifacts'") >= 1
    assert sql.count("'user_memory_settings'") >= 1
    assert "register_user_asset(uuid,text" in sql
    assert "wecom_get_or_create_user(text,text,uuid,text,text)" in sql
    assert "'kuaimai_external_credentials'" in sql


def test_preflight_checks_rls_counts_connections_and_stages(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    sql = sql_path.read_text(encoding="utf-8")
    assert "relation.relrowsecurity" in sql
    assert "relation.relforcerowsecurity" in sql
    assert "TENANT_CUTOVER_RLS_INCOMPLETE" in sql
    assert "TENANT_CUTOVER_FORCE_RLS_INCOMPLETE" in sql
    assert "source_orgs FROM public.organizations" in sql
    assert "source_configs FROM public.org_configs" in sql
    assert "FROM public.kuaimai_external_credentials" in sql
    assert "configuration_import_audit_log" in sql
    assert "FROM pg_catalog.pg_stat_activity" in sql
    for stage in ("pre_ownership", "owners_ready", "migrations_applied"):
        assert f"'{stage}'" in sql
    for sensitive_fragment in (
        "SELECT value FROM public.org_configs",
        "cookie",
        "secret_envelope",
        "PGPASSWORD",
    ):
        assert sensitive_fragment not in sql
