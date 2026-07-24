"""Legacy configuration export source ACL deployment contracts."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
GRANT_SCRIPT = ROOT / "deploy/grant-legacy-config-export-access.sh"
ROLLBACK_SCRIPT = ROOT / "deploy/rollback-legacy-config-export-access.sh"
MIGRATION = (
    ROOT / "backend/migrations/162_configuration_legacy_export_access.sql"
)
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback"
    / "162_configuration_legacy_export_access_rollback.sql"
)


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
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


def test_access_scripts_require_admin_url(tmp_path: Path) -> None:
    for script in (GRANT_SCRIPT, ROLLBACK_SCRIPT):
        env, sql_path = _environment(tmp_path / script.stem)
        del env["TENANT_DB_ADMIN_URL"]

        result = _run(script, env)

        assert result.returncode == 1
        assert "缺少 TENANT_DB_ADMIN_URL" in result.stderr
        assert not sql_path.exists()


def test_grant_script_is_narrow_and_fails_closed(tmp_path: Path) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(GRANT_SCRIPT, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert "CONFIG_EXPORT_ACCESS_REQUIRES_ADMIN" in sql
    assert (
        "GRANT SELECT ON TABLE public.kuaimai_external_credentials\n"
        "TO everydayai_owner;"
    ) in sql
    assert "CONFIG_EXPORT_READER_TABLE_ACCESS_INVALID" in sql
    assert "GRANT SELECT ON TABLE public.organizations" not in sql
    assert "GRANT SELECT ON TABLE public.org_configs" not in sql


def test_rollback_script_only_revokes_the_granted_acl(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(ROLLBACK_SCRIPT, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert "CONFIG_EXPORT_ACCESS_ROLLBACK_REQUIRES_ADMIN" in sql
    assert (
        "REVOKE SELECT ON TABLE public.kuaimai_external_credentials\n"
        "FROM everydayai_owner;"
    ) in sql
    assert "CONFIG_EXPORT_ACCESS_ROLLBACK_OWNER_INVALID" in sql
    assert "pg_get_userbyid(relation.relowner)" in sql
    assert "DROP TABLE" not in sql


def test_migration_records_owner_and_reader_acl_contract() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CONFIG_EXPORT_OWNER_SOURCE_ACCESS_INVALID" in sql
    assert "CONFIG_EXPORT_READER_TABLE_ACCESS_INVALID" in sql
    assert "everydayai_owner" in sql
    assert "everydayai_config_import_reader" in sql
    assert "GRANT " not in sql
    assert "REVOKE " not in sql


def test_migration_rollback_requires_admin_acl_rollback_first() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "CONFIG_EXPORT_ACCESS_RUN_ADMIN_ROLLBACK_FIRST" in sql
    assert "has_table_privilege" in sql
    assert "REVOKE " not in sql
