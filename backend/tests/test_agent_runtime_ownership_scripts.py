"""Agent Runtime 首次所有权转移脚本合同测试。"""

from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[2]
TRANSFER = ROOT / "deploy/transfer-agent-runtime-ownership.sh"
ROLLBACK = ROOT / "deploy/rollback-agent-runtime-ownership.sh"
RUNTIME_TABLES = {
    "conversation_artifacts",
    "conversation_attachment_refs",
    "conversation_channel_bindings",
    "conversation_compactions",
    "conversation_context_items",
    "conversation_context_receipts",
    "conversation_data_evidence",
    "message_generation_requests",
    "task_attachment_refs",
    "memory_atoms",
    "user_assets",
    "user_asset_refs",
    "user_activity_events",
}
ASSET_FUNCTIONS = {
    "_resolve_user_asset",
    "_bind_user_asset_ref",
    "register_user_asset",
}


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
        "TENANT_DB_ADMIN_URL": "postgresql://admin-secret@localhost/everydayai",
        "LEGACY_DATABASE_OWNER": "everydayai",
    }, sql_path


def _run(
    script: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_transfer_requires_admin_url(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    del env["TENANT_DB_ADMIN_URL"]

    result = _run(TRANSFER, env)

    assert result.returncode == 1
    assert "TENANT_DB_ADMIN_URL" in result.stderr


def test_transfer_rejects_unsafe_legacy_role_name(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["LEGACY_DATABASE_OWNER"] = "everydayai; DROP ROLE x"

    result = _run(TRANSFER, env)

    assert result.returncode == 1
    assert "不是合法 PostgreSQL 角色名" in result.stderr


def test_transfer_owns_exact_runtime_group_without_enabling_rls(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    owner_lines = {
        line.split()[2].removeprefix("public.")
        for line in sql.splitlines()
        if line.startswith("ALTER TABLE public.")
        and line.endswith(" OWNER TO everydayai_owner;")
    }
    assert owner_lines == RUNTIME_TABLES | {"schema_migration_ledger"}
    assert "ENABLE ROW LEVEL SECURITY" not in sql
    assert "FORCE ROW LEVEL SECURITY" not in sql


def test_transfer_preflights_and_revokes_runtime_access(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert "TENANT_TABLE_MISSING" in sql
    assert "TENANT_TABLE_OWNER_UNEXPECTED" in sql
    assert "TENANT_FUNCTION_MISSING" in sql
    assert "TENANT_FUNCTION_OWNER_UNEXPECTED" in sql
    assert "schema_migration_ledger TO everydayai_migrator" in sql
    assert "GRANT everydayai_owner TO everydayai;" in sql
    assert "FROM everydayai_runtime, everydayai_worker" in sql
    assert "TO everydayai;" in sql


def test_transfer_grants_owner_only_policy_dependency_reads(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    dependency_grant = sql[
        sql.index("GRANT SELECT ON TABLE\n    public.organizations"):
        sql.index("GRANT SELECT, INSERT, UPDATE, DELETE", sql.index(
            "GRANT SELECT ON TABLE\n    public.organizations"
        ))
    ]
    for table in ("organizations", "org_members", "conversations", "tasks"):
        assert f"public.{table}" in dependency_grant
    assert "TO everydayai_owner;" in dependency_grant
    assert "everydayai_runtime" not in dependency_grant
    assert "everydayai_worker" not in dependency_grant


def test_transfer_grants_schema_usage_without_table_access(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert (
        "GRANT USAGE ON SCHEMA public "
        "TO everydayai_runtime, everydayai_worker;"
    ) in sql
    assert "FROM everydayai_runtime, everydayai_worker;" in sql


def test_transfer_owns_asset_security_definer_functions(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    owned_functions = {
        line.split("public.", 1)[1].split("(", 1)[0]
        for line in sql.splitlines()
        if line.startswith("ALTER FUNCTION public.")
    }
    assert owned_functions == ASSET_FUNCTIONS
    assert sql.count(") OWNER TO everydayai_owner;") == len(ASSET_FUNCTIONS)


def test_admin_url_is_not_exposed_in_psql_arguments(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    arguments = (tmp_path / "psql-args").read_text(encoding="utf-8")
    assert env["TENANT_DB_ADMIN_URL"] not in arguments
    assert sql_path.read_text(encoding="utf-8").startswith(
        "\\set ON_ERROR_STOP on\n"
    )


def test_transfer_rejects_whitespace_in_admin_url(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["TENANT_DB_ADMIN_URL"] += " unsafe"

    result = _run(TRANSFER, env)

    assert result.returncode == 1
    assert "不能包含空白字符" in result.stderr


def test_rollback_requires_explicit_destructive_guard(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)

    result = _run(ROLLBACK, env)

    assert result.returncode == 1
    assert "ALLOW_TENANT_DB_OWNERSHIP_ROLLBACK=true" in result.stderr


def test_rollback_restores_exact_group_and_rejects_force_rls(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)
    env["ALLOW_TENANT_DB_OWNERSHIP_ROLLBACK"] = "true"

    result = _run(ROLLBACK, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    owner_lines = {
        line.split()[2].removeprefix("public.")
        for line in sql.splitlines()
        if line.startswith("ALTER TABLE public.")
        and line.endswith(" OWNER TO everydayai;")
    }
    assert owner_lines == RUNTIME_TABLES | {"schema_migration_ledger"}
    assert "DISABLE_FORCE_RLS_BEFORE_OWNERSHIP_ROLLBACK" in sql
    restored_functions = {
        line.split("public.", 1)[1].split("(", 1)[0]
        for line in sql.splitlines()
        if line.startswith("ALTER FUNCTION public.")
    }
    assert restored_functions == ASSET_FUNCTIONS
    assert sql.count(") OWNER TO everydayai;") == len(ASSET_FUNCTIONS)
    assert (
        "REVOKE USAGE ON SCHEMA public "
        "FROM everydayai_runtime, everydayai_worker;"
    ) in sql
    assert sql.startswith("\\set ON_ERROR_STOP on\n")
