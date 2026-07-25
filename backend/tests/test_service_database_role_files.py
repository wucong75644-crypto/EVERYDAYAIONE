"""Systemd 服务数据库角色文件映射合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _environment_files(service: str) -> list[str]:
    content = (DEPLOY / service).read_text(encoding="utf-8")
    return [
        line.split("=", 1)[1]
        for line in content.splitlines()
        if line.startswith("EnvironmentFile=")
    ]


def test_backend_and_wecom_use_isolated_runtime_role_overrides() -> None:
    backend_expected = [
        "/var/www/everydayai/backend/.env",
        "/var/www/everydayai/backend/.env.runtime",
    ]

    assert _environment_files("everydayai-backend.service") == [
        *backend_expected,
        "/var/www/everydayai/backend/.env.worker-client",
    ]
    assert _environment_files("everydayai-wecom.service") == [
        "/var/www/everydayai/backend/.env",
        "/var/www/everydayai/backend/.env.wecom-runtime",
        "/var/www/everydayai/backend/.env.worker-client",
        "/var/www/everydayai/backend/.env.kek",
    ]


def test_actor_uses_worker_role_override() -> None:
    assert _environment_files("everydayai-conversation-actor.service") == [
        "/var/www/everydayai/backend/.env",
        "/var/www/everydayai/backend/.env.worker",
        "/var/www/everydayai/backend/.env.worker-client",
    ]


def test_sync_remains_on_isolated_legacy_role_override() -> None:
    assert _environment_files("everydayai-sync.service") == [
        "/var/www/everydayai/backend/.env",
        "/var/www/everydayai/backend/.env.sync",
    ]
