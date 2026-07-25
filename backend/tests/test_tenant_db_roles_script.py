"""租户数据库角色初始化脚本合同测试。"""

from pathlib import Path
import os
import subprocess


SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/setup-tenant-db-roles.sh"
)


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured_sql = tmp_path / "captured.sql"
    fake_psql = fake_bin / "psql"
    fake_psql.write_text(
        f"#!/bin/sh\nprintf '%s' \"$*\" > '{tmp_path / 'psql-args'}'\n"
        f"cat > '{captured_sql}'\n",
        encoding="utf-8",
    )
    fake_psql.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TENANT_DB_ADMIN_URL": "postgresql://admin@localhost/everydayai",
        "EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD": (
            "config-reader-password-0001"
        ),
        "EVERYDAYAI_MIGRATOR_PASSWORD": "migrator-password-000001",
        "EVERYDAYAI_RUNTIME_PASSWORD": "runtime-password-00000001",
        "EVERYDAYAI_SYNC_PASSWORD": "sync-password-000000000001",
        "EVERYDAYAI_WECOM_RUNTIME_PASSWORD": "wecom-runtime-password-0001",
        "EVERYDAYAI_WORKER_PASSWORD": "worker-password-0000000001",
    }, captured_sql


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_role_script_fails_when_required_secret_is_missing(
    tmp_path: Path,
) -> None:
    env, _ = _environment(tmp_path)
    del env["EVERYDAYAI_WORKER_PASSWORD"]

    result = _run(env)

    assert result.returncode == 1
    assert "EVERYDAYAI_WORKER_PASSWORD" in result.stderr


def test_role_script_requires_config_import_reader_secret(
    tmp_path: Path,
) -> None:
    env, _ = _environment(tmp_path)
    del env["EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD"]

    result = _run(env)

    assert result.returncode == 1
    assert "EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD" in result.stderr


def test_role_script_fails_when_wecom_runtime_secret_is_missing(
    tmp_path: Path,
) -> None:
    env, _ = _environment(tmp_path)
    del env["EVERYDAYAI_WECOM_RUNTIME_PASSWORD"]

    result = _run(env)

    assert result.returncode == 1
    assert "EVERYDAYAI_WECOM_RUNTIME_PASSWORD" in result.stderr


def test_role_script_rejects_shared_passwords(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["EVERYDAYAI_WORKER_PASSWORD"] = env["EVERYDAYAI_RUNTIME_PASSWORD"]

    result = _run(env)

    assert result.returncode == 1
    assert "必须使用不同密码" in result.stderr


def test_role_script_rejects_reader_reusing_migrator_password(
    tmp_path: Path,
) -> None:
    env, _ = _environment(tmp_path)
    env["EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD"] = (
        env["EVERYDAYAI_MIGRATOR_PASSWORD"]
    )

    result = _run(env)

    assert result.returncode == 1
    assert "必须使用不同密码" in result.stderr


def test_role_script_rejects_shared_wecom_runtime_password(
    tmp_path: Path,
) -> None:
    env, _ = _environment(tmp_path)
    env["EVERYDAYAI_WECOM_RUNTIME_PASSWORD"] = (
        env["EVERYDAYAI_RUNTIME_PASSWORD"]
    )

    result = _run(env)

    assert result.returncode == 1
    assert "必须使用不同密码" in result.stderr


def test_role_script_rejects_short_password(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["EVERYDAYAI_RUNTIME_PASSWORD"] = "too-short"

    result = _run(env)

    assert result.returncode == 1
    assert "不能少于 24 个字符" in result.stderr


def test_role_script_enforces_role_boundaries(tmp_path: Path) -> None:
    env, captured_sql = _environment(tmp_path)

    result = _run(env)

    assert result.returncode == 0
    sql = captured_sql.read_text(encoding="utf-8")
    assert "everydayai_owner NOLOGIN" in sql
    for role in (
        "everydayai_config_import_reader",
        "everydayai_migrator",
        "everydayai_runtime",
        "everydayai_sync",
        "everydayai_wecom_runtime",
        "everydayai_worker",
    ):
        assert f"{role} LOGIN" in sql
    assert "NOBYPASSRLS" in sql
    assert "GRANT everydayai_owner TO everydayai_migrator" in sql
    assert "REVOKE everydayai_owner FROM everydayai_config_import_reader" in sql
    assert "REVOKE everydayai_owner FROM everydayai_runtime" in sql
    assert "REVOKE everydayai_owner FROM everydayai_sync" in sql
    assert "REVOKE everydayai_owner FROM everydayai_wecom_runtime" in sql
    assert "REVOKE everydayai_owner FROM everydayai_worker" in sql


def test_role_script_escapes_single_quote_in_password(tmp_path: Path) -> None:
    env, captured_sql = _environment(tmp_path)
    env["EVERYDAYAI_WORKER_PASSWORD"] = "worker-password-with-'quote"

    result = _run(env)

    assert result.returncode == 0
    assert "worker-password-with-''quote" in captured_sql.read_text(
        encoding="utf-8",
    )


def test_role_script_escapes_wecom_runtime_password(
    tmp_path: Path,
) -> None:
    env, captured_sql = _environment(tmp_path)
    env["EVERYDAYAI_WECOM_RUNTIME_PASSWORD"] = (
        "wecom-runtime-password-'quote"
    )

    result = _run(env)

    assert result.returncode == 0
    assert "wecom-runtime-password-''quote" in captured_sql.read_text(
        encoding="utf-8",
    )


def test_role_script_does_not_print_passwords(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)

    result = _run(env)

    output = result.stdout + result.stderr
    assert env["EVERYDAYAI_MIGRATOR_PASSWORD"] not in output
    assert env["EVERYDAYAI_CONFIG_IMPORT_READER_PASSWORD"] not in output
    assert env["EVERYDAYAI_RUNTIME_PASSWORD"] not in output
    assert env["EVERYDAYAI_SYNC_PASSWORD"] not in output
    assert env["EVERYDAYAI_WECOM_RUNTIME_PASSWORD"] not in output
    assert env["EVERYDAYAI_WORKER_PASSWORD"] not in output


def test_role_script_keeps_admin_url_out_of_process_arguments(
    tmp_path: Path,
) -> None:
    env, captured_sql = _environment(tmp_path)

    result = _run(env)

    assert result.returncode == 0
    arguments = (tmp_path / "psql-args").read_text(encoding="utf-8")
    assert env["TENANT_DB_ADMIN_URL"] not in arguments
    assert captured_sql.read_text(encoding="utf-8").startswith(
        "\\set ON_ERROR_STOP on\n"
    )


def test_role_script_rejects_whitespace_in_admin_url(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    env["TENANT_DB_ADMIN_URL"] += "\n\\echo unsafe"

    result = _run(env)

    assert result.returncode == 1
    assert "不能包含空白字符" in result.stderr


def test_role_script_propagates_psql_failure(tmp_path: Path) -> None:
    env, _ = _environment(tmp_path)
    fake_psql = Path(env["PATH"].split(":", 1)[0]) / "psql"
    fake_psql.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")

    result = _run(env)

    assert result.returncode != 0
    assert "已创建或更新" not in result.stdout
