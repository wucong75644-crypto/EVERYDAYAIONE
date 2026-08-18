"""生产部署脚本的服务生命周期契约。"""

from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/deploy.sh"
).read_text()
RELEASE_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/release.sh"
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


def test_actor_migrations_are_atomic_and_include_cancel_snapshot() -> None:
    assert "--single-transaction" in SCRIPT
    for migration in (
        "138_conversation_control_events.sql",
        "139_tool_invocations.sql",
        "140_conversation_subtasks.sql",
        "141_conversation_actor_cancel_snapshot.sql",
    ):
        assert f"--file /var/www/everydayai/backend/migrations/{migration}" in SCRIPT


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


def test_deploy_reuses_dependencies_and_supports_full_test_override() -> None:
    assert ".everydayai-package-lock.sha256" in SCRIPT
    assert ".everydayai-requirements.sha256" in SCRIPT
    assert "tests/test_conversation_*.py" in SCRIPT
    assert "--full-test" in SCRIPT


def test_release_reuses_a_persistent_isolated_worktree() -> None:
    assert "release_worktree=\"${EVERYDAYAI_RELEASE_WORKTREE:-$repo_parent/${repo_name}-release-worktree}\"" in RELEASE_SCRIPT
    assert "git -C \"$release_worktree\" checkout --detach \"$commit_sha\"" in RELEASE_SCRIPT
