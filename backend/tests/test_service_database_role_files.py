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
        "/var/www/everydayai/backend/.env.kek",
        "/etc/everydayai/runtime-admin.env",
    ]
    assert _environment_files("everydayai-wecom.service") == [
        "/var/www/everydayai/backend/.env",
        "/var/www/everydayai/backend/.env.wecom-runtime",
        "/var/www/everydayai/backend/.env.worker-client",
        "/var/www/everydayai/backend/.env.kek",
    ]


def test_actor_uses_runtime_and_worker_clients() -> None:
    assert _environment_files("everydayai-conversation-actor.service") == [
        "/var/www/everydayai/backend/.env",
        "/var/www/everydayai/backend/.env.runtime",
        "/var/www/everydayai/backend/.env.worker-client",
        "/var/www/everydayai/backend/.env.kek",
    ]


def test_actor_delegates_only_its_cgroup_to_non_root_sandbox() -> None:
    unit = (DEPLOY / "everydayai-conversation-actor.service").read_text()
    sandbox = (DEPLOY / "sandbox.cfg").read_text()
    helper = DEPLOY / "actor-sandbox-cgroup.sh"
    installer = (DEPLOY / "install-service-units.sh").read_text()

    assert "User=everydayai-actor" in unit
    assert "Delegate=yes" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ExecStartPre=+/usr/local/libexec/everydayai-actor-sandbox-cgroup prepare" in unit
    assert "ExecStartPre=/usr/local/bin/nsjail --config " in unit
    assert "ExecStopPost=+/usr/local/libexec/everydayai-actor-sandbox-cgroup cleanup" in unit
    assert sandbox.count(
        'system.slice/everydayai-conversation-actor.service/nsjail',
    ) == 3
    assert 'cgroup_cpu_mount: "/sys/fs/cgroup/cpu,cpuacct"' in sandbox
    assert 'outside_id: "0"' not in sandbox
    assert helper.stat().st_mode & 0o111
    helper_text = helper.read_text()
    assert "test \"$(id -u)\" -eq 0" in helper_text
    assert "${parent}/nsjail" in helper_text
    assert "everydayai-actor-sandbox-cgroup" in installer


def test_sync_uses_isolated_role_and_kek() -> None:
    assert _environment_files("everydayai-sync.service") == [
        "/var/www/everydayai/backend/.env",
        "/var/www/everydayai/backend/.env.sync",
        "/var/www/everydayai/backend/.env.kek",
    ]
