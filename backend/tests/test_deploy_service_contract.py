"""生产部署脚本的服务生命周期契约。"""

import os
from pathlib import Path
import subprocess


SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/deploy.sh"
).read_text()
MIGRATION_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/run-migrations.sh"
).read_text()
INSTALL_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/install-service-units.sh"
).read_text()


def test_backend_deploy_restarts_all_required_services() -> None:
    for service in (
        "everydayai-backend",
        "everydayai-sync",
        "everydayai-wecom",
        "everydayai-conversation-actor",
    ):
        assert service in SCRIPT
    assert 'sudo systemctl restart "$service"' in SCRIPT
    assert 'sudo systemctl is-active --quiet "$service"' in SCRIPT


def test_backend_deploy_has_bounded_readiness_check() -> None:
    assert "seq 1 20" in SCRIPT
    assert "http://127.0.0.1:8000/api/health" in SCRIPT
    assert "后端 readiness 超时" in SCRIPT


def test_rsync_preserves_runtime_and_sensitive_files() -> None:
    for excluded in (
        ".env*",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "tmp/",
        "outputs/",
        "external/mediacrawler",
    ):
        assert f"--exclude '{excluded}'" in SCRIPT


def test_missing_required_service_fails_deployment() -> None:
    assert "缺少必需服务" in SCRIPT
    assert 'systemctl list-unit-files "${service}.service"' in SCRIPT


def test_backend_deploy_does_not_install_chart_runtime() -> None:
    assert "setup-chart-runtime" not in SCRIPT
    assert "playwright" not in SCRIPT


def test_backend_deploy_validates_migration_mode() -> None:
    assert "RUN_MIGRATIONS=false" in SCRIPT
    assert 'case "${RUN_MIGRATIONS:-false}" in' in MIGRATION_SCRIPT
    assert "RUN_MIGRATIONS 只能是 true 或 false" in MIGRATION_SCRIPT
    assert "缺少 MIGRATION_DATABASE_URL" in MIGRATION_SCRIPT
    assert "source .env.migrator" in MIGRATION_SCRIPT
    assert "venv/bin/python -m pytest" in SCRIPT


def test_backend_deploy_installs_canonical_service_units() -> None:
    assert "install-service-units.sh" in SCRIPT
    assert "validate-tenant-db-env.sh" in INSTALL_SCRIPT
    assert "validate-kek-env.sh" in INSTALL_SCRIPT
    assert "sudo install -m 0644" in INSTALL_SCRIPT
    assert "sudo systemctl daemon-reload" in INSTALL_SCRIPT
    for service in (
        "everydayai-backend",
        "everydayai-sync",
        "everydayai-wecom",
        "everydayai-conversation-actor",
    ):
        assert service in INSTALL_SCRIPT


def test_migrations_run_before_service_restart_and_fail_closed() -> None:
    migration_gate = SCRIPT.index("bash ../deploy/run-migrations.sh")
    restart = SCRIPT.index('sudo systemctl restart "$service"')

    assert migration_gate < restart
    assert "scripts/migration_runner.py plan" in MIGRATION_SCRIPT
    assert "scripts/migration_runner.py apply" in MIGRATION_SCRIPT
    assert 'elif [ -n "$migration_plan" ]; then' in MIGRATION_SCRIPT
    assert "存在待执行迁移" in MIGRATION_SCRIPT


def test_migration_gate_blocks_pending_when_apply_is_disabled(
    tmp_path: Path,
) -> None:
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\necho 150_change.sql\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "RUN_MIGRATIONS": "false",
        "MIGRATION_DATABASE_URL": "postgresql://migrator@example/db",
        "MIGRATION_PYTHON": str(fake_python),
    }

    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[2] / "deploy/run-migrations.sh")],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "存在待执行迁移" in result.stdout


def test_migration_gate_plans_then_applies_when_enabled(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "RUN_MIGRATIONS": "true",
        "MIGRATION_DATABASE_URL": "postgresql://migrator@example/db",
        "MIGRATION_PYTHON": str(fake_python),
    }

    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[2] / "deploy/run-migrations.sh")],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "scripts/migration_runner.py plan --applied-by deploy-script",
        "scripts/migration_runner.py apply --applied-by deploy-script",
    ]


def test_migration_gate_fails_before_runner_without_migration_url(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "python-called"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        f"#!/bin/sh\ntouch '{marker}'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "RUN_MIGRATIONS": "true",
        "MIGRATION_PYTHON": str(fake_python),
    }
    env.pop("MIGRATION_DATABASE_URL", None)

    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[2] / "deploy/run-migrations.sh")],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "缺少 MIGRATION_DATABASE_URL" in result.stdout
    assert not marker.exists()


def test_migration_gate_loads_dedicated_migrator_environment(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$MIGRATION_DATABASE_URL\" > '{calls}'\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (tmp_path / ".env.migrator").write_text(
        "MIGRATION_DATABASE_URL="
        "postgresql://everydayai_migrator:secret@localhost/everydayai\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "RUN_MIGRATIONS": "false",
        "MIGRATION_PYTHON": str(fake_python),
    }
    env.pop("MIGRATION_DATABASE_URL", None)

    result = subprocess.run(
        [
            "bash",
            str(
                Path(__file__).resolve().parents[2]
                / "deploy/run-migrations.sh"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert calls.read_text(encoding="utf-8").startswith(
        "postgresql://everydayai_migrator:"
    )
