"""生产部署脚本的服务生命周期契约。"""

import os
from pathlib import Path
import shutil
import subprocess


SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/deploy.sh"
).read_text()
DEPLOY_HELPERS = (
    Path(__file__).resolve().parents[2] / "deploy/deploy-helpers.sh"
).read_text()
MIGRATION_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/run-migrations.sh"
).read_text()
INSTALL_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/install-service-units.sh"
).read_text()
GIT_PUSH_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "git-push.sh"
RELEASE_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/release.sh"
).read_text()
RELEASE_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "deploy/release-policy.conf"
)


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
    assert "--exclude 'config.env*'" in SCRIPT


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
    assert 'command -v python3.12 || command -v python3' in SCRIPT
    assert '"$PYTHON_BIN" -m venv venv' in SCRIPT


def test_deploy_fails_closed_and_requires_pushed_release_source() -> None:
    assert "set -euo pipefail" in SCRIPT
    assert "npm run test:run ||" not in SCRIPT
    assert "--expected-sha" in SCRIPT
    assert "source deploy/deploy-helpers.sh" in SCRIPT
    assert "git ls-remote origin" in DEPLOY_HELPERS
    assert "部署目录含未提交内容" in DEPLOY_HELPERS
    assert "verify_public_endpoints" in SCRIPT
    assert '"https://${DOMAIN}/api/health"' in DEPLOY_HELPERS


def test_unified_release_uses_an_isolated_git_worktree() -> None:
    assert "git-push.sh" in RELEASE_SCRIPT
    assert 'worktree add --detach "$release_dir" "$release_sha"' in RELEASE_SCRIPT
    assert '--expected-sha "$release_sha"' in RELEASE_SCRIPT
    assert 'if (( ${#DEPLOY_ARGS[@]} > 0 )); then' in RELEASE_SCRIPT
    assert "--skip-test)" in RELEASE_SCRIPT
    assert 'DEPLOY_ARGS+=("$1")' in RELEASE_SCRIPT
    assert "git add -A" not in RELEASE_SCRIPT


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _make_release_test_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    (repo / "deploy").mkdir()
    shutil.copy2(GIT_PUSH_SCRIPT_PATH, repo / "git-push.sh")
    shutil.copy2(RELEASE_POLICY_PATH, repo / "deploy/release-policy.conf")
    (repo / "allowed.txt").write_text("before\n", encoding="utf-8")

    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "Release Test")
    _run_git(repo, "config", "user.email", "release@example.com")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "chore: baseline")
    _run_git(tmp_path, "init", "--bare", str(remote))
    _run_git(repo, "remote", "add", "origin", str(remote))
    _run_git(repo, "push", "-u", "origin", "main")
    return repo, remote


def test_git_push_commits_only_explicit_task_files(tmp_path: Path) -> None:
    repo, _ = _make_release_test_repo(tmp_path)
    (repo / "allowed.txt").write_text("after\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("keep local\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "git-push.sh",
            "--message",
            "fix: explicit task scope",
            "--file",
            "allowed.txt",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    changed = _run_git(repo, "show", "--name-only", "--format=").stdout.splitlines()
    assert changed == ["allowed.txt"]
    assert "?? unrelated.txt" in _run_git(repo, "status", "--short").stdout


def test_git_push_rejects_forbidden_env_and_cursor_paths(
    tmp_path: Path,
) -> None:
    repo, _ = _make_release_test_repo(tmp_path)
    for forbidden in (".env", ".cursor/rules/private.md"):
        target = repo / forbidden
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("private\n", encoding="utf-8")
        result = subprocess.run(
            [
                "bash",
                "git-push.sh",
                "--message",
                "chore: forbidden file",
                "--file",
                forbidden,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "发布策略禁止提交" in result.stderr


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
    generation_gate = SCRIPT.index(
        "scripts/verify_runtime_generation_capabilities.py"
    )
    restart = SCRIPT.index('sudo systemctl restart "$service"')

    assert migration_gate < generation_gate < restart
    assert "source .env.runtime" in SCRIPT
    assert "source .env.worker-client" in SCRIPT
    assert "scripts/migration_runner.py plan" in MIGRATION_SCRIPT
    assert "scripts/migration_runner.py apply" in MIGRATION_SCRIPT
    assert "scripts/verify_worker_control_preconditions.py" in MIGRATION_SCRIPT
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


def test_migration_gate_checks_worker_control_ownership_before_apply(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{calls}'\n"
        "case \"$1 $2\" in\n"
        "  'scripts/migration_runner.py plan') "
        "echo 171_worker_media_task_control.sql ;;\n"
        "esac\n",
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
        [
            "bash",
            str(
                Path(__file__).resolve().parents[2]
                / "deploy/run-migrations.sh"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "scripts/migration_runner.py plan --applied-by deploy-script",
        "scripts/verify_worker_control_preconditions.py",
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
