"""Worker Control 所有权转移与回滚脚本合同。"""

from pathlib import Path
import os
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
TRANSFER = ROOT / "deploy/transfer-worker-control-ownership.sh"
ROLLBACK = ROOT / "deploy/rollback-worker-control-ownership.sh"
TABLES = {
    "error_logs",
    "knowledge_metrics",
    "scheduled_tasks",
    "scheduled_task_runs",
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


def test_transfer_preflights_exact_worker_control_domain(
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
    assert "WORKER_CONTROL_TABLE_MISSING" in sql
    assert "WORKER_CONTROL_TABLE_OWNER_UNEXPECTED" in sql
    assert "WORKER_CONTROL_SEQUENCE_OWNER_UNEXPECTED" in sql


def test_transfer_moves_owned_sequences_and_preserves_legacy_access(
    tmp_path: Path,
) -> None:
    env, sql_path = _environment(tmp_path)

    result = _run(TRANSFER, env)

    assert result.returncode == 0
    sql = sql_path.read_text(encoding="utf-8")
    assert "ALTER TABLE public.%I OWNER TO everydayai_owner" in sql
    assert (
        "REVOKE ALL ON TABLE public.%I FROM PUBLIC, everydayai_runtime, "
        "everydayai_wecom_runtime, everydayai_worker"
    ) in sql
    assert "dependency.deptype IN ('a', 'i')" in sql
    assert "ALTER SEQUENCE public.%I OWNER TO everydayai_owner" in sql
    assert "TO everydayai" in sql
    assert "ON ALL TABLES IN SCHEMA public" not in sql
    assert "ON ALL SEQUENCES IN SCHEMA public" not in sql


def test_rollback_requires_guard_and_refuses_force_rls(
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
    assert "WORKER_CONTROL_ROLLBACK_PRECONDITION_FAILED" in sql
    assert "ALTER TABLE public.%I OWNER TO %I" in sql
    assert "ALTER SEQUENCE public.%I OWNER TO %I" in sql
