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


def test_skill_catalog_release_prepares_the_personal_submount_on_existing_nas() -> None:
    setup_script = (
        Path(__file__).resolve().parents[2] / "deploy/ensure-skill-personal-storage.sh"
    ).read_text()
    assert "prepare_skill_personal_mount()" in SCRIPT
    assert 'prepare_skill_personal_mount' in SCRIPT
    assert 'remote_exec sudo bash -s < deploy/ensure-skill-personal-storage.sh' in SCRIPT
    assert 'personal_alias="$workspace_root/.platform-skills/personal"' in setup_script
    assert 'expected_personal_source="${platform_source%/}/personal"' in setup_script
    assert 'cp -a "$fstab" "$backup"' in setup_script
    assert 'mount "$personal_mount"' in setup_script
    assert 'stat -c \'%u:%g:%a\' "$personal_mount"' in setup_script
    assert 'if [[ -L "$personal_alias" ]]' in setup_script
    assert 'stat -c \'%i\' "$personal_alias"' in setup_script
    assert 'install -D -m 0644 "$dropin_source" "$dropin_target"' in SCRIPT
    assert 'effective_read_only_paths=$(sudo systemctl show everydayai-backend -p ReadOnlyPaths --value)' in SCRIPT
    assert '未保留 Skill 存储根目录只读保护' in SCRIPT
    assert 'systemctl daemon-reload' in SCRIPT
    main_body = SCRIPT[SCRIPT.rindex('main() {'):SCRIPT.rindex('# 执行主函数')]
    assert main_body.index('remote_exec /var/www/everydayai/backend/venv/bin/python -') < main_body.index('prepare_skill_personal_mount')
    assert main_body.index('build_backend\n        prepare_scheduled_task_cutover\n        prepare_skill_personal_mount') < main_body.index('sync_backend\n')


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


def test_deploy_pins_local_python_312_and_remote_python_311() -> None:
    assert 'EVERYDAYAI_PYTHON_BIN="${EVERYDAYAI_PYTHON_BIN:-python3.12}"' in SCRIPT
    assert 'EVERYDAYAI_REQUIRED_PYTHON="${EVERYDAYAI_REQUIRED_PYTHON:-3.12}"' in SCRIPT
    assert '"$EVERYDAYAI_PYTHON_BIN" -m venv venv' in SCRIPT
    assert 'venv/bin/python -m pip install -q -r requirements.txt' in SCRIPT
    assert 'python3.11 -m venv venv' in SCRIPT
    assert 'python3 -m venv venv' not in SCRIPT
    assert '\n    pip install -q -r requirements.txt' not in SCRIPT
    assert '\n        pip install -q -r requirements.txt' not in SCRIPT


def test_local_backend_tests_use_safe_configuration_without_real_database() -> None:
    assert 'DATABASE_URL="postgresql://test"' in SCRIPT
    assert 'JWT_SECRET_KEY="test"' in SCRIPT
    assert 'DASHSCOPE_API_KEY="test"' in SCRIPT
    assert 'FILE_WORKSPACE_ROOT="$test_workspace_root"' in SCRIPT
    assert '--ignore=tests/test_wecom_concurrent_safety.py' in SCRIPT
