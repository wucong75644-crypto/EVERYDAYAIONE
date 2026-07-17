"""生产部署脚本的服务生命周期契约。"""

from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/deploy.sh"
).read_text()
CHART_SETUP = (
    Path(__file__).resolve().parents[2] / "deploy/setup-chart-runtime.sh"
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


def test_backend_deploy_gates_restart_on_chart_runtime_smoke() -> None:
    setup = 'bash ../deploy/setup-chart-runtime.sh "$PWD"'
    assert setup in SCRIPT
    assert SCRIPT.index(setup) < SCRIPT.index('sudo systemctl restart "$service"')
    assert "command -v apt-get" in CHART_SETUP
    assert "python -m playwright install --with-deps chromium" in CHART_SETUP
    assert "command -v dnf" in CHART_SETUP
    assert "dnf install -y atk at-spi2-atk at-spi2-core" in CHART_SETUP
    assert "python -m playwright install chromium" in CHART_SETUP
    assert "PYTHONPATH=. python scripts/smoke_chart_renderer.py" in CHART_SETUP
