"""Agent Runtime production flags-off 安装合同测试。"""

from pathlib import Path
import os
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
INSTALL_SCRIPT = DEPLOY / "install-service-units.sh"
DEPLOY_SCRIPT = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
FLAGS_OFF_SCRIPT = (DEPLOY / "runtime-flags-off-install.sh").read_text(
    encoding="utf-8"
)
RELEASE_SCRIPT = (DEPLOY / "release.sh").read_text(encoding="utf-8")
RELEASE_SHA = "a" * 40
RUNTIME_SERVICES = (
    "everydayai-agent-runtime",
    "everydayai-agent-projection",
    "everydayai-agent-authorization",
    "everydayai-sandbox-worker",
)


def _write_backend_envs(directory: Path) -> None:
    values = {
        ".env.runtime": (
            "DATABASE_URL=postgresql://everydayai_runtime:runtime-secret@localhost/db\n"
            "AGENT_RUNTIME_INGRESS_ENABLED=false\n"
            "TOOL_CONFIRMATION_V3_ENABLED=false\n"
            "AGENT_RUNTIME_AGENT_DEFINITION_ID=everydayai-default\n"
            "AGENT_RUNTIME_AGENT_DEFINITION_REVISION=v3\n"
        ),
        ".env.wecom-runtime": (
            "DATABASE_URL=postgresql://everydayai_wecom_runtime:wecom-secret@localhost/db\n"
            "AGENT_RUNTIME_INGRESS_ENABLED=false\n"
            "AGENT_RUNTIME_AGENT_DEFINITION_ID=everydayai-default\n"
            "AGENT_RUNTIME_AGENT_DEFINITION_REVISION=v3\n"
        ),
        ".env.worker": (
            "DATABASE_URL=postgresql://everydayai_worker:worker-secret@localhost/db\n"
        ),
        ".env.worker-client": (
            "WORKER_DATABASE_URL=postgresql://everydayai_worker:worker-secret@localhost/db\n"
        ),
        ".env.migrator": (
            "MIGRATION_DATABASE_URL=postgresql://everydayai_migrator:migrator-secret@localhost/db\n"
        ),
        ".env.sync": (
            "DATABASE_URL=postgresql://everydayai_sync:sync-secret@localhost/db\n"
        ),
    }
    directory.mkdir()
    for filename, content in values.items():
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)


def _render_worker_envs(directory: Path) -> None:
    replacements = {
        "<password>": "worker-secret",
        "<sandbox-worker-password>": "sandbox-secret",
        "<git-sha>": RELEASE_SHA,
        "<sandbox-runtime-revision>": "sandbox-v1",
        "<revision>": "sandbox-v1",
        "<manifest-sha256>": "1" * 64,
        "<nsjail-sha256>": "2" * 64,
        "<seccomp-sha256>": "3" * 64,
        "<redis-password-or-empty>": "",
        "<existing-sentry-dsn>": "",
    }
    names = (
        "agent-runtime-worker",
        "agent-projection-worker",
        "agent-authorization-worker",
        "sandbox-worker",
    )
    directory.mkdir()
    for name in names:
        content = (DEPLOY / f"env-templates/{name}.env.template").read_text(
            encoding="utf-8"
        )
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        path = directory / f"{name}.env"
        path.write_text(content, encoding="utf-8")
        path.chmod(0o640)


def _fake_commands(directory: Path) -> tuple[Path, Path]:
    fake_bin = directory / "bin"
    fake_bin.mkdir()
    calls = directory / "systemctl-calls"
    sudo = fake_bin / "sudo"
    sudo.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    sudo.chmod(0o755)
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    return fake_bin, calls


def _run_installer(
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    backend_dir = tmp_path / "backend-env"
    runtime_env_dir = tmp_path / "runtime-env"
    systemd_dir = tmp_path / "systemd"
    libexec_dir = tmp_path / "libexec"
    _write_backend_envs(backend_dir)
    _render_worker_envs(runtime_env_dir)
    systemd_dir.mkdir()
    fake_bin, calls = _fake_commands(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SYSTEMD_UNIT_DIR": str(systemd_dir),
        "AGENT_RUNTIME_ENV_DIR": str(runtime_env_dir),
        "LIBEXEC_DIR": str(libexec_dir),
    }
    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SCRIPT),
            str(backend_dir),
            "agent-runtime-only",
            RELEASE_SHA,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, systemd_dir, calls


def test_agent_runtime_only_installs_four_units_and_wrapper(tmp_path: Path) -> None:
    result, systemd_dir, calls = _run_installer(tmp_path)

    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in systemd_dir.iterdir()) == sorted(
        f"{service}.service" for service in RUNTIME_SERVICES
    )
    wrapper = tmp_path / "libexec/everydayai-sandbox-worker-cgroup-wrapper"
    assert wrapper.read_bytes() == (
        DEPLOY / "sandbox-worker-cgroup-wrapper.sh"
    ).read_bytes()
    assert calls.read_text(encoding="utf-8").splitlines() == ["daemon-reload"]
    assert "未启停或启用服务" in result.stdout


def test_agent_runtime_only_rejects_different_target_before_writes(
    tmp_path: Path,
) -> None:
    backend_dir = tmp_path / "backend-env"
    runtime_env_dir = tmp_path / "runtime-env"
    systemd_dir = tmp_path / "systemd"
    libexec_dir = tmp_path / "libexec"
    _write_backend_envs(backend_dir)
    _render_worker_envs(runtime_env_dir)
    systemd_dir.mkdir()
    target = systemd_dir / "everydayai-agent-projection.service"
    target.write_text("existing-content\n", encoding="utf-8")
    fake_bin, calls = _fake_commands(tmp_path)

    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SCRIPT),
            str(backend_dir),
            "agent-runtime-only",
            RELEASE_SHA,
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "SYSTEMD_UNIT_DIR": str(systemd_dir),
            "AGENT_RUNTIME_ENV_DIR": str(runtime_env_dir),
            "LIBEXEC_DIR": str(libexec_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "拒绝覆盖" in result.stderr
    assert target.read_text(encoding="utf-8") == "existing-content\n"
    assert [path.name for path in systemd_dir.iterdir()] == [target.name]
    assert not libexec_dir.exists()
    assert not calls.exists()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("UNKNOWN_RUNTIME_KEY=value\n", "未知配置键"),
        ("AGENT_RUNTIME_PROCESS_ROLE=agent_runtime\n", "重复配置键"),
    ),
)
def test_agent_runtime_only_strictly_rejects_worker_env_keys(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    backend_dir = tmp_path / "backend-env"
    runtime_env_dir = tmp_path / "runtime-env"
    _write_backend_envs(backend_dir)
    _render_worker_envs(runtime_env_dir)
    path = runtime_env_dir / "agent-runtime-worker.env"
    path.write_text(path.read_text(encoding="utf-8") + mutation, encoding="utf-8")
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()

    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SCRIPT),
            str(backend_dir),
            "agent-runtime-only",
            RELEASE_SHA,
        ],
        env={
            **os.environ,
            "SYSTEMD_UNIT_DIR": str(systemd_dir),
            "AGENT_RUNTIME_ENV_DIR": str(runtime_env_dir),
            "LIBEXEC_DIR": str(tmp_path / "libexec"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert expected in result.stderr
    assert not any(systemd_dir.iterdir())


def test_flags_off_deploy_path_is_mutually_exclusive_and_write_gated() -> None:
    verify = FLAGS_OFF_SCRIPT.index("remote_exec bash")
    sync = FLAGS_OFF_SCRIPT.index("rsync -avz --relative", verify)
    install = FLAGS_OFF_SCRIPT.index("agent-runtime-only", sync)

    assert verify < sync < install
    assert 'exec bash deploy/runtime-flags-off-install.sh "$@"' in DEPLOY_SCRIPT
    assert "必须为 inactive + disabled" in FLAGS_OFF_SCRIPT
    assert "--runtime-flags-off-install 不能与其他部署模式组合" in FLAGS_OFF_SCRIPT
    assert "run-migrations" not in FLAGS_OFF_SCRIPT
    assert "systemctl restart" not in FLAGS_OFF_SCRIPT
    assert "transfer-agent-runtime-ownership" not in FLAGS_OFF_SCRIPT
    assert "--runtime-flags-off-install" in RELEASE_SCRIPT
    assert "不能同时选择多个部署范围" in RELEASE_SCRIPT


def test_flags_off_deploy_route_rejects_other_modes_before_release_checks() -> None:
    result = subprocess.run(
        [
            "bash",
            str(DEPLOY / "deploy.sh"),
            "--runtime-flags-off-install",
            "--backend-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "不能与其他部署模式组合" in result.stderr


def test_flags_off_templates_and_projection_dependency_are_pinned() -> None:
    sandbox = (DEPLOY / "env-templates/sandbox-worker.env.template").read_text()
    projection = (DEPLOY / "everydayai-agent-projection.service").read_text()

    assert "AGENT_RUNTIME_RELEASE_REVISION=<git-sha>" in sandbox
    assert "redis.service" in projection
    assert "redis-server.service" not in projection
