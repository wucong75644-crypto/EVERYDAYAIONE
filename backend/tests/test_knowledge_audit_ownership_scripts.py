"""Knowledge/Audit 所有权转移与回滚脚本合同。"""

from pathlib import Path
import os
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
TRANSFER = ROOT / "deploy/transfer-knowledge-audit-ownership.sh"
ROLLBACK = ROOT / "deploy/rollback-knowledge-audit-ownership.sh"
TABLES = {
    "knowledge_nodes",
    "knowledge_metrics",
    "knowledge_edges",
    "scoring_audit_log",
    "tool_audit_log",
    "permission_audit_log",
}


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
            "postgresql://admin-secret@localhost/everydayai"
        ),
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


def test_transfer_preflights_exact_domain_and_dynamic_partitions(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    table_block = sql.split(
        "target_tables CONSTANT TEXT[] := ARRAY[", 1,
    )[1].split("];", 1)[0]
    assert set(re.findall(r"'([a-z_]+)'", table_block)) == TABLES
    assert "child.inhparent = 'public.tool_audit_log'::regclass" in sql
    assert "KNOWLEDGE_AUDIT_TABLE_MISSING" in sql
    assert "KNOWLEDGE_AUDIT_OWNER_UNEXPECTED" in sql
    assert "KNOWLEDGE_AUDIT_SEQUENCE_OWNER_UNEXPECTED" in sql


def test_transfer_moves_objects_without_schema_wide_grants(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert "ALTER TABLE public.%I OWNER TO everydayai_owner" in sql
    assert "ALTER SEQUENCE public.%I OWNER TO everydayai_owner" in sql
    assert "dependency.deptype IN ('a', 'i')" in sql
    assert (
        "ALTER FUNCTION public.maintain_tool_audit_partitions()\n"
        "    OWNER TO everydayai_owner"
    ) in sql
    assert "ON ALL TABLES IN SCHEMA public" not in sql
    assert "ON ALL SEQUENCES IN SCHEMA public" not in sql


def test_rollback_requires_guard_and_checks_owner_and_force_rls(
    tmp_path: Path,
) -> None:
    env, _ = _environment(tmp_path)

    blocked = _run(ROLLBACK, env)

    assert blocked.returncode == 1
    assert "ALLOW_DESTRUCTIVE_TENANT_DB_ROLLBACK=true" in blocked.stderr

    env["ALLOW_DESTRUCTIVE_TENANT_DB_ROLLBACK"] = "true"
    result = _run(ROLLBACK, env)

    assert result.returncode == 0
    sql = (tmp_path / "captured.sql").read_text(encoding="utf-8")
    assert "relation.relforcerowsecurity" in sql
    assert "KNOWLEDGE_AUDIT_ROLLBACK_PRECONDITION_FAILED" in sql
    assert "KNOWLEDGE_AUDIT_FUNCTION_OWNER_UNEXPECTED" in sql
    assert "ALTER TABLE public.%I OWNER TO %I" in sql
    assert "ALTER SEQUENCE public.%I OWNER TO %I" in sql
