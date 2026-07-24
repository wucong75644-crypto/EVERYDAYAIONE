"""Migration 161 production preflight shell contract tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/preflight-legacy-config-import.sh"


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
        "TENANT_DB_ADMIN_URL": (
            "postgresql://admin:secret@localhost/everydayai"
        ),
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


def test_preflight_is_read_only_and_uses_safe_psql_runner(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(env)

    assert result.returncode == 0
    assert "只读前置检查通过" in result.stdout
    assert "secret" not in result.stdout + result.stderr
    sql = sql_path.read_text(encoding="utf-8")
    assert "BEGIN;" in sql
    assert "SET TRANSACTION READ ONLY;" in sql
    assert "ROLLBACK;" in sql
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE"):
        assert forbidden not in sql


def test_preflight_checks_migrations_roles_grants_rls_and_empty_target(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    sql = sql_path.read_text(encoding="utf-8")
    for identity in (
        "158_configuration_control_plane_foundation.sql",
        "159_configuration_management_core.sql",
        "159_configuration_management_facades.sql",
        "160_configuration_resolution_core.sql",
        "160_configuration_resolution_facades.sql",
        "161_configuration_legacy_import.sql",
    ):
        assert identity in sql
    for role in (
        "everydayai_owner",
        "everydayai_config_import_reader",
        "everydayai_migrator",
        "everydayai_runtime",
        "everydayai_wecom_runtime",
        "everydayai_worker",
    ):
        assert role in sql
    assert "rolsuper" in sql
    assert "rolbypassrls" in sql
    assert "rolcanlogin" in sql
    assert "import_legacy_configuration_batch(uuid,jsonb)" in sql
    assert "export_legacy_configuration_snapshot()" in sql
    assert "has_function_privilege" in sql
    assert "aclexplode(procedure.proacl)" in sql
    assert "acl.privilege_type = 'EXECUTE'" in sql
    assert "has_table_privilege" in sql
    assert "CONFIG_EXPORT_READER_TABLE_ACCESS_INVALID" in sql
    assert "0::OID" in sql
    assert "'PUBLIC'," not in sql
    assert "relrowsecurity" in sql
    assert "relforcerowsecurity" in sql
    assert "configuration_definitions) <> 15" in sql
    assert "configuration_bundle_definitions) <> 11" in sql
    for table in (
        "configuration_entries",
        "configuration_policies",
        "secret_records",
        "configuration_import_audit_log",
    ):
        assert f"public.{table}" in sql


def test_preflight_requires_old_source_tables_and_separate_admin(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    assert _run(env).returncode == 0

    sql = sql_path.read_text(encoding="utf-8")
    assert "CONFIG_IMPORT_PREFLIGHT_REQUIRES_ADMIN" in sql
    assert "public.organizations" in sql
    assert "public.org_configs" in sql
    assert "public.kuaimai_external_credentials" in sql
