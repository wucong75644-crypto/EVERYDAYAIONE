"""Agent Runtime production flags-off 安装合同测试。"""

from pathlib import Path
import os
import shutil
import subprocess

import pytest

from backend.tests.agent_runtime_deploy_test_support import fake_installer_commands

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
            "AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED=false\n"
            "AGENT_RUNTIME_SCHEDULED_WECOM_WORKER_ID=scheduled-wecom-01\n"
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
        "<everydayai-agent-runtime-uid>": "1201",
        "<base64-encoded-32-byte-kek>": "BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ=",
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
        "agent-runtime-model",
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


def _write_unit_states(
    state_dir: Path,
    phase: str,
    overrides: dict[str, tuple[str, str]] | None = None,
) -> None:
    overrides = overrides or {}
    default = ("inactive", "not-found") if phase == "pre" else (
        "inactive",
        "disabled",
    )
    for service in RUNTIME_SERVICES:
        active_state, enabled_state = overrides.get(service, default)
        (state_dir / f"{phase}.{service}.is-active").write_text(
            active_state,
            encoding="utf-8",
        )
        (state_dir / f"{phase}.{service}.is-enabled").write_text(
            enabled_state,
            encoding="utf-8",
        )


def _make_release_harness(
    tmp_path: Path,
    *,
    pre_overrides: dict[str, tuple[str, str]] | None = None,
    post_overrides: dict[str, tuple[str, str]] | None = None,
) -> tuple[Path, dict[str, str], Path, Path]:
    release_root = tmp_path / "release"
    deploy_dir = release_root / "deploy"
    fake_bin = tmp_path / "release-bin"
    state_dir = tmp_path / "unit-states"
    deploy_dir.mkdir(parents=True)
    fake_bin.mkdir()
    state_dir.mkdir()
    for name in (
        "runtime-flags-off-install.sh",
        "check-agent-runtime-unit-states.sh",
        "deploy-helpers.sh",
    ):
        shutil.copy2(DEPLOY / name, deploy_dir / name)
    (deploy_dir / "config.env").write_text(
        "SERVER_HOST=runtime.example\n"
        "SERVER_USER=deploy\n"
        "SERVER_PORT=22\n"
        "REMOTE_APP_DIR=/remote/app\n"
        "REMOTE_BACKEND_DIR=/remote/app/backend\n",
        encoding="utf-8",
    )
    _write_unit_states(state_dir, "pre", pre_overrides)
    _write_unit_states(state_dir, "post", post_overrides)

    rsync_marker = tmp_path / "rsync-called"
    install_marker = tmp_path / "install-called"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        f"  rev-parse) echo '{RELEASE_SHA}' ;;\n"
        f"  ls-remote) echo '{RELEASE_SHA} refs/heads/main' ;;\n"
        "  status) exit 0 ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        "#!/bin/bash\n"
        "if [ \"${1:-}\" = -p ]; then shift 2; fi\n"
        "shift\n"
        "if [ \"$#\" -eq 1 ]; then\n"
        "  touch \"$INSTALL_MARKER\"\n"
        "  exit 0\n"
        "fi\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    fake_rsync = fake_bin / "rsync"
    fake_rsync.write_text(
        "#!/bin/sh\ntouch \"$RSYNC_MARKER\"\n",
        encoding="utf-8",
    )
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        "#!/bin/sh\n"
        "if [ \"$2\" = everydayai-agent-model-gateway ]; then "
        "[ \"$1\" = is-active ] && echo inactive || echo not-found; exit 0; fi\n"
        "phase=pre\n"
        "test ! -f \"$INSTALL_MARKER\" || phase=post\n"
        "cat \"$STATE_DIR/${phase}.$2.$1\"\n",
        encoding="utf-8",
    )
    for command in (fake_git, fake_ssh, fake_rsync, fake_systemctl):
        command.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "STATE_DIR": str(state_dir),
        "RSYNC_MARKER": str(rsync_marker),
        "INSTALL_MARKER": str(install_marker),
    }
    return release_root, env, rsync_marker, install_marker


def _run_release_harness(
    release_root: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "deploy/runtime-flags-off-install.sh", "--runtime-flags-off-install"],
        cwd=release_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_installer(
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    backend_dir = tmp_path / "backend-env"
    runtime_env_dir = tmp_path / "runtime-env"
    systemd_dir = tmp_path / "systemd"
    libexec_dir = tmp_path / "libexec"
    _write_backend_envs(backend_dir)
    _render_worker_envs(runtime_env_dir)
    systemd_dir.mkdir()
    fake_bin, calls = fake_installer_commands(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SYSTEMD_UNIT_DIR": str(systemd_dir),
        "AGENT_RUNTIME_ENV_DIR": str(runtime_env_dir),
        "LIBEXEC_DIR": str(libexec_dir),
        **(extra_env or {}),
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
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "is-active everydayai-agent-model-gateway",
        "is-enabled everydayai-agent-model-gateway",
        "daemon-reload",
    ]
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
    fake_bin, calls = fake_installer_commands(tmp_path)

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
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "is-active everydayai-agent-model-gateway",
        "is-enabled everydayai-agent-model-gateway",
    ]


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
    fake_bin, _ = fake_installer_commands(tmp_path)

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
    precheck = FLAGS_OFF_SCRIPT.index("check_remote_unit_states pre-install")
    sync = FLAGS_OFF_SCRIPT.index("rsync -avz --relative", precheck)
    install = FLAGS_OFF_SCRIPT.index("agent-runtime-only", sync)
    postcheck = FLAGS_OFF_SCRIPT.index("check_remote_unit_states post-install", install)

    assert precheck < sync < install < postcheck
    assert 'exec bash deploy/runtime-flags-off-install.sh "$@"' in DEPLOY_SCRIPT
    assert "< deploy/check-agent-runtime-unit-states.sh" in FLAGS_OFF_SCRIPT
    assert "--runtime-flags-off-install 不能与其他部署模式组合" in FLAGS_OFF_SCRIPT
    assert "run-migrations" not in FLAGS_OFF_SCRIPT
    assert "systemctl restart" not in FLAGS_OFF_SCRIPT
    assert "transfer-agent-runtime-ownership" not in FLAGS_OFF_SCRIPT
    assert "--runtime-flags-off-install" in RELEASE_SCRIPT
    assert "不能同时选择多个部署范围" in RELEASE_SCRIPT


def test_preinstall_state_contract_accepts_all_units_not_found(
    tmp_path: Path,
) -> None:
    release_root, env, rsync_marker, install_marker = _make_release_harness(
        tmp_path
    )

    result = subprocess.run(
        ["bash", "deploy/check-agent-runtime-unit-states.sh", "pre-install"],
        cwd=release_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "pre-install 状态合同通过" in result.stdout
    assert not rsync_marker.exists()
    assert not install_marker.exists()


@pytest.mark.parametrize(
    ("bad_state", "expected_pair"),
    (
        (("active", "not-found"), "active:not-found"),
        (("failed", "not-found"), "failed:not-found"),
        (("inactive", "enabled"), "inactive:enabled"),
        (("inactive", "static"), "inactive:static"),
    ),
)
def test_preinstall_unsafe_state_stops_before_rsync(
    tmp_path: Path,
    bad_state: tuple[str, str],
    expected_pair: str,
) -> None:
    release_root, env, rsync_marker, install_marker = _make_release_harness(
        tmp_path,
        pre_overrides={RUNTIME_SERVICES[0]: bad_state},
    )

    result = _run_release_harness(release_root, env)

    assert result.returncode == 1
    assert expected_pair in result.stderr
    assert not rsync_marker.exists()
    assert not install_marker.exists()


@pytest.mark.parametrize(
    ("bad_state", "expected_pair"),
    (
        (("failed", "disabled"), "failed:disabled"),
        (("inactive", "enabled"), "inactive:enabled"),
    ),
)
def test_postinstall_requires_strict_inactive_disabled(
    tmp_path: Path,
    bad_state: tuple[str, str],
    expected_pair: str,
) -> None:
    release_root, env, rsync_marker, install_marker = _make_release_harness(
        tmp_path,
        post_overrides={RUNTIME_SERVICES[-1]: bad_state},
    )

    result = _run_release_harness(release_root, env)

    assert result.returncode == 1
    assert expected_pair in result.stderr
    assert rsync_marker.exists()
    assert install_marker.exists()


def test_first_install_passes_pre_and_post_state_contracts(tmp_path: Path) -> None:
    release_root, env, rsync_marker, install_marker = _make_release_harness(
        tmp_path
    )

    result = _run_release_harness(release_root, env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("状态合同通过") == 2
    assert rsync_marker.exists()
    assert install_marker.exists()


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
    wecom_runtime = (
        DEPLOY / "env-templates/wecom-runtime.env.template"
    ).read_text()
    projection = (DEPLOY / "everydayai-agent-projection.service").read_text()

    assert "AGENT_RUNTIME_RELEASE_REVISION=<git-sha>" in sandbox
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_ENABLED=false" in wecom_runtime
    assert "redis.service" in projection
    assert "redis-server.service" not in projection
