"""生产部署脚本的服务生命周期契约。"""

from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy/deploy.sh"
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


def test_deploy_pins_python_311_for_local_and_remote_builds() -> None:
    assert 'EVERYDAYAI_PYTHON_BIN="${EVERYDAYAI_PYTHON_BIN:-python3.11}"' in SCRIPT
    assert 'EVERYDAYAI_REQUIRED_PYTHON="3.11"' in SCRIPT
    assert '"$EVERYDAYAI_PYTHON_BIN" -m venv venv' in SCRIPT
    assert 'venv/bin/python -m pip install -q -r requirements.txt' in SCRIPT
    assert 'python3.11 -m venv venv' in SCRIPT
    assert 'python3 -m venv venv' not in SCRIPT
    assert '\n    pip install -q -r requirements.txt' not in SCRIPT
    assert '\n        pip install -q -r requirements.txt' not in SCRIPT
