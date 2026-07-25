"""旧数据库角色临时 owner 能力撤销脚本合同测试。"""

from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/finalize-tenant-db-role-cutover.sh"


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sql_path = tmp_path / "captured.sql"
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        f"#!/bin/sh\ncat > '{sql_path}'\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TENANT_DB_ADMIN_URL": "postgresql://admin@localhost/everydayai",
        "ALLOW_TENANT_DB_ROLE_FINALIZE": "true",
        "TENANT_SERVICES_USE_ISOLATED_ROLES": "true",
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


def test_finalize_requires_explicit_guards(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    del env["ALLOW_TENANT_DB_ROLE_FINALIZE"]

    result = _run(env)

    assert result.returncode == 1
    assert "ALLOW_TENANT_DB_ROLE_FINALIZE=true" in result.stderr


def test_finalize_requires_service_role_cutover(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    del env["TENANT_SERVICES_USE_ISOLATED_ROLES"]

    result = _run(env)

    assert result.returncode == 1
    assert "所有服务已切换独立角色" in result.stderr


def test_finalize_checks_migrations_owners_and_sessions(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    for migration_number in range(150, 160):
        assert f"'{migration_number}_" in sql
    assert "'158_configuration_control_plane_foundation.sql'" in sql
    assert "'158_governed_org_config_capabilities.sql'" not in sql
    assert "'159_configuration_management_core.sql'" in sql
    assert "'159_configuration_management_facades.sql'" in sql
    assert "'160_configuration_resolution_core.sql'" in sql
    assert "'160_configuration_resolution_facades.sql'" in sql
    assert "'161_configuration_legacy_import.sql'" in sql
    assert "'162_configuration_legacy_export_access.sql'" in sql
    assert "'163_conversation_actor_worker_discovery.sql'" in sql
    assert "'164_actor_task_execution_capabilities.sql'" in sql
    assert "'189_web_runtime_access_completion.sql'" in sql
    for migration_number in range(165, 186):
        assert f"'{migration_number}_" in sql
    assert "'159_org_erp_token_capabilities.sql'" not in sql
    assert "schema_migration_ledger" in sql
    assert "TENANT_OWNER_CUTOVER_INCOMPLETE" in sql
    assert "SERVICE_ROLE_HAS_OWNER_MEMBERSHIP" in sql
    assert "LEGACY_DATABASE_SESSIONS_REMAIN" in sql
    assert "FINALIZE_REQUIRES_SEPARATE_ADMIN_ROLE" in sql
    assert "worker_get_claimed_generation_task(uuid,uuid)" in sql
    assert "worker_update_generation_progress(uuid,uuid,text,jsonb)" in sql
    assert "worker_update_generation_model(uuid,uuid,text,jsonb)" in sql
    assert "worker_get_generation_terminal_snapshot(uuid,uuid)" in sql
    assert "ACTOR_WORKER_CAPABILITY_CUTOVER_INCOMPLETE" in sql
    assert "ACTOR_CORE_OWNER_CUTOVER_INCOMPLETE" in sql
    assert "WORKER_CONTROL_CAPABILITY_CUTOVER_INCOMPLETE" in sql
    assert "WORKER_CONTROL_DIRECT_TABLE_ACCESS_PRESENT" in sql
    assert "WORKER_CONTROL_RUNTIME_ACL_INVALID" in sql
    assert "WEB_RUNTIME_CORE_ACL_INVALID" in sql
    assert "AS required(table_name, privilege_name)" in sql
    assert "AS forbidden(table_name, privilege_name)" in sql
    assert "TENANT_FORCE_RLS_CUTOVER_INCOMPLETE" in sql
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert f"'public.tasks', '{privilege}'" in sql
    assert sql.count("has_any_column_privilege(") == 3
    assert "'public.conversations', 'SELECT'" in sql
    assert "'public.messages', 'SELECT'" in sql
    assert "'INSERT, UPDATE, DELETE'" in sql
    assert "close_generation_turn(uuid,uuid,uuid)" in sql
    assert "'everydayai_runtime', procedure.oid, 'EXECUTE'" in sql
    assert "'everydayai_wecom_runtime', procedure.oid, 'EXECUTE'" in sql
    assert "acl.grantee = 0" in sql
    assert "'org_invitations'" in sql
    assert "'governance_audit_log'" in sql
    for configuration_table in (
        "configuration_definitions",
        "configuration_bundle_definitions",
        "configuration_entries",
        "configuration_policies",
        "secret_records",
        "configuration_import_audit_log",
    ):
        assert f"'{configuration_table}'" in sql
    assert "REVOKE everydayai_owner FROM everydayai;" in sql


def test_finalize_rejects_unsafe_legacy_owner(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["LEGACY_DATABASE_OWNER"] = "everydayai; DROP ROLE x"

    result = _run(env)

    assert result.returncode == 1
    assert "不是合法 PostgreSQL 角色名" in result.stderr
