"""数据库角色环境文件合同测试。"""

from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/validate-tenant-db-env.sh"
TEMPLATES = ROOT / "deploy/env-templates"


def _write_env_files(directory: Path) -> None:
    values = {
        ".env.runtime": (
            "DATABASE_URL="
            "postgresql://everydayai_runtime:runtime-secret@localhost/everydayai"
        ),
        ".env.wecom-runtime": (
            "DATABASE_URL=postgresql://everydayai_wecom_runtime:"
            "wecom-runtime-secret@localhost/everydayai"
        ),
        ".env.worker": (
            "DATABASE_URL="
            "postgresql://everydayai_worker:worker-secret@localhost/everydayai"
        ),
        ".env.worker-client": (
            "WORKER_DATABASE_URL="
            "postgresql://everydayai_worker:worker-secret@localhost/everydayai"
        ),
        ".env.migrator": (
            "MIGRATION_DATABASE_URL="
            "postgresql://everydayai_migrator:migrator-secret@localhost/everydayai"
        ),
        ".env.sync": (
            "DATABASE_URL="
            "postgresql://everydayai_sync:sync-secret@localhost/everydayai"
        ),
    }
    for filename, value in values.items():
        path = directory / filename
        path.write_text(f"# role-specific connection\n{value}\n", encoding="utf-8")
        path.chmod(0o600)


def _run(
    directory: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(directory)],
        env=env or os.environ,
        capture_output=True,
        text=True,
        check=False,
    )


def _fake_stat_environment(
    tmp_path: Path,
    implementation: str,
) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_stat = fake_bin / "stat"
    if implementation == "gnu":
        behavior = (
            "if [ \"$1\" = \"-c\" ]; then echo 600; exit 0; fi\n"
            "echo '?p'; exit 0\n"
        )
    else:
        behavior = (
            "if [ \"$1\" = \"-c\" ]; then exit 1; fi\n"
            "if [ \"$1\" = \"-f\" ]; then echo 600; exit 0; fi\n"
            "exit 1\n"
        )
    fake_stat.write_text(f"#!/bin/sh\n{behavior}", encoding="utf-8")
    fake_stat.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }


def test_role_env_templates_are_safe_placeholders() -> None:
    expected = {
        "runtime.env.template": "everydayai_runtime:<runtime-password>",
        "wecom-runtime.env.template": (
            "everydayai_wecom_runtime:<wecom-runtime-password>"
        ),
        "worker.env.template": "everydayai_worker:<worker-password>",
        "worker-client.env.template": "everydayai_worker:<worker-password>",
        "migrator.env.template": "everydayai_migrator:<migrator-password>",
        "sync.env.template": "everydayai_sync:<sync-password>",
        "legacy-config-import.env.template": (
            "everydayai_config_import_reader:<reader-password>"
        ),
    }
    for filename, placeholder in expected.items():
        content = (TEMPLATES / filename).read_text(encoding="utf-8")
        assert placeholder in content
        assert "127.0.0.1:5432/everydayai" in content


def test_role_env_contract_accepts_isolated_role_files(tmp_path: Path) -> None:
    _write_env_files(tmp_path)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert "合同验证通过" in result.stdout
    assert "secret" not in result.stdout + result.stderr


def test_role_env_contract_accepts_gnu_stat(tmp_path: Path) -> None:
    _write_env_files(tmp_path)
    env = _fake_stat_environment(tmp_path, "gnu")

    result = _run(tmp_path, env)

    assert result.returncode == 0
    assert "合同验证通过" in result.stdout


def test_role_env_contract_accepts_bsd_stat(tmp_path: Path) -> None:
    _write_env_files(tmp_path)
    env = _fake_stat_environment(tmp_path, "bsd")

    result = _run(tmp_path, env)

    assert result.returncode == 0
    assert "合同验证通过" in result.stdout


def test_role_env_contract_rejects_missing_file(tmp_path: Path) -> None:
    _write_env_files(tmp_path)
    (tmp_path / ".env.worker").unlink()

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "缺少角色环境文件" in result.stderr


def test_role_env_contract_rejects_missing_wecom_runtime_file(
    tmp_path: Path,
) -> None:
    _write_env_files(tmp_path)
    (tmp_path / ".env.wecom-runtime").unlink()

    result = _run(tmp_path)

    assert result.returncode == 1
    assert ".env.wecom-runtime" in result.stderr


def test_role_env_contract_rejects_sync_using_runtime_role(
    tmp_path: Path,
) -> None:
    _write_env_files(tmp_path)
    path = tmp_path / ".env.sync"
    path.write_text(
        "DATABASE_URL="
        "postgresql://everydayai_runtime:wrong-role@localhost/everydayai\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "必须使用 everydayai_sync 数据库角色" in result.stderr


def test_role_env_contract_rejects_insecure_permissions(tmp_path: Path) -> None:
    _write_env_files(tmp_path)
    (tmp_path / ".env.runtime").chmod(0o644)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "权限必须为 0600" in result.stderr


def test_role_env_contract_rejects_wrong_database_role(tmp_path: Path) -> None:
    _write_env_files(tmp_path)
    path = tmp_path / ".env.worker"
    path.write_text(
        "DATABASE_URL="
        "postgresql://everydayai_runtime:wrong-role@localhost/everydayai\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "必须使用 everydayai_worker" in result.stderr


def test_role_env_contract_rejects_wrong_wecom_runtime_role(
    tmp_path: Path,
) -> None:
    _write_env_files(tmp_path)
    path = tmp_path / ".env.wecom-runtime"
    path.write_text(
        "DATABASE_URL="
        "postgresql://everydayai_runtime:wrong-role@localhost/everydayai\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "必须使用 everydayai_wecom_runtime" in result.stderr


def test_role_env_contract_rejects_mismatched_worker_client(
    tmp_path: Path,
) -> None:
    _write_env_files(tmp_path)
    path = tmp_path / ".env.worker-client"
    path.write_text(
        "WORKER_DATABASE_URL="
        "postgresql://everydayai_worker:other-secret@localhost/everydayai\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "必须指向同一 Worker 连接" in result.stderr


def test_role_env_contract_rejects_template_placeholder(
    tmp_path: Path,
) -> None:
    _write_env_files(tmp_path)
    path = tmp_path / ".env.migrator"
    path.write_text(
        "MIGRATION_DATABASE_URL="
        "postgresql://everydayai_migrator:<password>@localhost/everydayai\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "模板占位符" in result.stderr


def test_role_env_contract_rejects_empty_password(tmp_path: Path) -> None:
    _write_env_files(tmp_path)
    path = tmp_path / ".env.runtime"
    path.write_text(
        "DATABASE_URL="
        "postgresql://everydayai_runtime:@localhost/everydayai\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "必须使用 everydayai_runtime" in result.stderr
