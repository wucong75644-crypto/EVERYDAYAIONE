"""C7-D0-A control-plane flags-off provisioning/update contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
PROVISIONER = DEPLOY / "provision-control-plane-worker-envs.py"
UPDATER = DEPLOY / "update-control-plane-units.sh"
MANIFEST_CHECKER = DEPLOY / "check-control-plane-unit-manifest.sh"
INSTALLER = (DEPLOY / "install-service-units.sh").read_text(encoding="utf-8")
FLAGS_OFF = (DEPLOY / "runtime-flags-off-install.sh").read_text(encoding="utf-8")
DEPLOY_SCRIPT = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
RELEASE_SCRIPT = (DEPLOY / "release.sh").read_text(encoding="utf-8")
RELEASE_SHA = "c" * 40
SERVICES = (
    "everydayai-agent-runtime",
    "everydayai-agent-projection",
    "everydayai-agent-authorization",
)
SECRETS = {
    "EVERYDAYAI_AGENT_RUNTIME_WORKER_PASSWORD": "Agent!@:/?#[]%+ space-0123456789",
    "EVERYDAYAI_PROJECTION_WORKER_PASSWORD": "Projection!@:/?#[]%+ space-0123456789",
    "EVERYDAYAI_AUTHORIZATION_WORKER_PASSWORD": "Authorization!@:/?#[]%+ space-0123456789",
}
MIGRATOR_SECRET = "migrator-source-secret-0123456789"


def _load_provisioner():
    spec = importlib.util.spec_from_file_location("control_plane_provisioner", PROVISIONER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_source_envs(backend_dir: Path) -> str:
    backend_dir.mkdir()
    backend_lines = [f'{key}="{value}"' for key, value in SECRETS.items()]
    backend_lines.extend(
        (
            "REDIS_HOST=redis.internal",
            "REDIS_PORT=6380",
            'REDIS_PASSWORD="redis!secret#value"',
            "REDIS_DB=7",
            "REDIS_SSL=true",
            "SENTRY_DSN=https://public@example.invalid/42",
            "ENVIRONMENT=production-c7",
        )
    )
    backend_env = backend_dir / ".env"
    backend_env.write_text("\n".join(backend_lines) + "\n", encoding="utf-8")
    backend_env.chmod(0o640)
    query = "sslmode=require&connect_timeout=9&application_name=migrator"
    migrator = backend_dir / ".env.migrator"
    migrator.write_text(
        "MIGRATION_DATABASE_URL="
        f'"postgresql://everydayai_migrator:{MIGRATOR_SECRET}'
        f'@[::1]:5544/everydayai?{query}"\n',
        encoding="utf-8",
    )
    migrator.chmod(0o600)
    return query


def _read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_provisioner_keeps_secrets_off_argv_and_output_and_encodes_dsn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    provisioner = _load_provisioner()
    backend_dir = tmp_path / "backend"
    env_dir = tmp_path / "etc-everydayai"
    env_dir.mkdir()
    query = _write_source_envs(backend_dir)
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(provisioner, "_resolve_owner", lambda: (uid, gid))
    monkeypatch.setattr(provisioner.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provisioner.os, "chown", lambda path, owner, group: None)
    argv = [
        "--backend-dir", str(backend_dir), "--env-dir", str(env_dir),
        "--release-sha", RELEASE_SHA,
    ]

    assert provisioner.main(argv) == 0

    captured = capsys.readouterr()
    output = captured.out + captured.err
    for secret in (*SECRETS.values(), MIGRATOR_SECRET, "redis!secret#value"):
        assert secret not in argv
        assert secret not in output
    assert sorted(path.name for path in env_dir.iterdir()) == sorted(
        f"{service.removeprefix('everydayai-')}-worker.env"
        if service != "everydayai-agent-runtime"
        else "agent-runtime-worker.env"
        for service in SERVICES
    )
    for path in env_dir.iterdir():
        assert path.stat().st_mode & 0o777 == 0o640
        assert (path.stat().st_uid, path.stat().st_gid) == (uid, gid)
        values = _read_env(path)
        assert values["AGENT_RUNTIME_RELEASE_REVISION"] == RELEASE_SHA
        dsn = values["WORKER_DATABASE_URL"]
        assert "@[::1]:5544/everydayai?" in dsn
        assert dsn.endswith(query)
        assert "%40%3A%2F%3F%23%5B%5D%25%2B%20space-" in dsn
    runtime = _read_env(env_dir / "agent-runtime-worker.env")
    projection = _read_env(env_dir / "agent-projection-worker.env")
    assert runtime["AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED"] == "false"
    assert runtime["SANDBOX_RUNTIME_REVISION"] == "unprovisioned"
    assert projection["REDIS_HOST"] == "redis.internal"
    assert projection["REDIS_PASSWORD"] == "redis!secret#value"
    assert projection["SENTRY_DSN"] == "https://public@example.invalid/42"
    assert projection["ENVIRONMENT"] == "production-c7"
    assert not (env_dir / "sandbox-worker.env").exists()


def _write_fake_commands(fake_bin: Path, calls: Path) -> None:
    fake_bin.mkdir()
    sudo = fake_bin / "sudo"
    sudo.write_text(
        "#!/bin/bash\n"
        "printf 'sudo %s\\n' \"$*\" >> \"$CALLS\"\n"
        "if [ \"${FAIL_MIDDLE:-false}\" = true ] && [[ \"$*\" == install*agent-projection.service.c7-* ]] && [ ! -e \"$FAIL_MARKER\" ]; then\n"
        "  touch \"$FAIL_MARKER\"\n"
        "  exit 42\n"
        "fi\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/bash\n"
        "printf 'systemctl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "if [ \"$1\" = daemon-reload ]; then\n"
        "  count=0; test ! -f \"$RELOAD_COUNT\" || count=$(cat \"$RELOAD_COUNT\")\n"
        "  echo $((count + 1)) > \"$RELOAD_COUNT\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${PRE_ACTIVE:-false}\" = true ] && [ \"$1\" = is-active ]; then echo active; exit 0; fi\n"
        "if [ \"${POST_ACTIVE:-false}\" = true ] && [ -f \"$RELOAD_COUNT\" ] && [ \"$1\" = is-active ]; then echo active; exit 0; fi\n"
        "if [ \"$1\" = is-active ]; then echo inactive; else echo disabled; fi\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)


def _unit_harness(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path, Path]:
    source_dir = tmp_path / "candidate"
    target_dir = tmp_path / "systemd"
    backup_root = tmp_path / "backups"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "calls"
    reload_count = tmp_path / "reload-count"
    fail_marker = tmp_path / "fail-marker"
    source_dir.mkdir()
    target_dir.mkdir()
    manifest_lines = []
    for service in SERVICES:
        source = source_dir / f"{service}.service"
        target = target_dir / f"{service}.service"
        source.write_text(f"candidate:{service}\n", encoding="utf-8")
        target.write_text(f"reviewed-old:{service}\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest_lines.append(f"{digest}  {service}.service")
    manifest = tmp_path / "reviewed.sha256"
    manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    _write_fake_commands(fake_bin, calls)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "RELOAD_COUNT": str(reload_count),
        "FAIL_MARKER": str(fail_marker),
        "SYSTEMD_UNIT_DIR": str(target_dir),
        "CONTROL_PLANE_DEPLOY_DIR": str(source_dir),
        "CONTROL_PLANE_UNIT_BACKUP_ROOT": str(backup_root),
    }
    return env, source_dir, target_dir, backup_root, manifest


def _run_updater(
    env: dict[str, str], manifest: Path, operation: str = "apply"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(UPDATER), operation, RELEASE_SHA, str(manifest)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("unsafe", ("hash", "state"))
def test_preflight_failure_has_zero_target_or_backup_writes(
    tmp_path: Path, unsafe: str
) -> None:
    env, _, target_dir, backup_root, manifest = _unit_harness(tmp_path)
    before = {
        f"{service}.service": (target_dir / f"{service}.service").read_bytes()
        for service in SERVICES
    }
    if unsafe == "hash":
        manifest.write_text("0" * 64 + manifest.read_text()[64:], encoding="utf-8")
    else:
        env["PRE_ACTIVE"] = "true"
        staged = target_dir / f"{SERVICES[0]}.service.c7-{RELEASE_SHA}.tmp"
        staged.write_text("preexisting-staging\n", encoding="utf-8")

    result = _run_updater(env, manifest)

    assert result.returncode != 0
    assert {
        name: (target_dir / name).read_bytes() for name in before
    } == before
    if unsafe == "state":
        assert staged.read_text(encoding="utf-8") == "preexisting-staging\n"
    assert not backup_root.exists()


def test_review_manifest_content_is_never_logged(tmp_path: Path) -> None:
    env, _, _, backup_root, manifest = _unit_harness(tmp_path)
    marker = "manifest-must-not-leak-secret"
    manifest.write_text(marker + "\n", encoding="utf-8")

    result = _run_updater(env, manifest, operation="preflight")

    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
    assert not backup_root.exists()


def test_streamed_manifest_checker_rejects_mismatch_without_writes(tmp_path: Path) -> None:
    env, _, _, backup_root, manifest = _unit_harness(tmp_path)
    args = [part for line in manifest.read_text().splitlines() for part in line.split()]
    args[0] = "0" * 64

    result = subprocess.run(
        ["bash", str(MANIFEST_CHECKER), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "SHA-256" in result.stderr
    assert not backup_root.exists()


def test_all_units_are_backed_up_before_any_atomic_replacement(tmp_path: Path) -> None:
    env, source_dir, target_dir, backup_root, manifest = _unit_harness(tmp_path)

    result = _run_updater(env, manifest)

    assert result.returncode == 0, result.stderr
    calls = Path(env["CALLS"]).read_text().splitlines()
    backup_calls = [index for index, call in enumerate(calls) if str(backup_root) in call]
    replace_calls = [index for index, call in enumerate(calls) if ".c7-" in call]
    assert len(backup_calls) >= 4
    assert max(backup_calls[:4]) < min(replace_calls)
    for service in SERVICES:
        assert (target_dir / f"{service}.service").read_bytes() == (
            source_dir / f"{service}.service"
        ).read_bytes()
        assert (backup_root / RELEASE_SHA / f"{service}.service").read_text().startswith(
            "reviewed-old:"
        )


@pytest.mark.parametrize("failure", ("middle", "postcheck"))
def test_update_failure_restores_all_reviewed_units(
    tmp_path: Path, failure: str
) -> None:
    env, _, target_dir, backup_root, manifest = _unit_harness(tmp_path)
    before = {path.name: path.read_bytes() for path in target_dir.iterdir()}
    env["FAIL_MIDDLE" if failure == "middle" else "POST_ACTIVE"] = "true"

    result = _run_updater(env, manifest)

    assert result.returncode != 0
    assert {path.name: path.read_bytes() for path in target_dir.iterdir()} == before
    assert all((backup_root / RELEASE_SHA / name).exists() for name in before)
    assert int(Path(env["RELOAD_COUNT"]).read_text()) >= 1
    assert "已恢复" in result.stderr


def test_control_plane_state_scope_and_release_routes_never_touch_sandbox(
    tmp_path: Path
) -> None:
    env, _, _, _, _ = _unit_harness(tmp_path)
    result = subprocess.run(
        ["bash", str(DEPLOY / "check-agent-runtime-unit-states.sh"), "pre-install", "control-plane"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = Path(env["CALLS"]).read_text()
    assert "sandbox" not in calls.lower()
    control_branch = FLAGS_OFF.split('if [ "$runtime_mode" = control-plane ]', 1)[1]
    control_branch = control_branch.split('log_info "确认 Agent Runtime unit', 1)[0]
    assert "everydayai-sandbox-worker" not in control_branch
    assert "sandbox-worker.env" not in control_branch
    assert "control-plane-only" in INSTALLER
    assert "--runtime-control-plane-flags-off-update" in DEPLOY_SCRIPT
    assert "--runtime-control-plane-flags-off-update" in RELEASE_SCRIPT
    release_check = FLAGS_OFF.index("check_release_source")
    state_check = FLAGS_OFF.index("check_remote_unit_states pre-install control-plane")
    hash_check = FLAGS_OFF.index("check-control-plane-unit-manifest.sh")
    sync = FLAGS_OFF.index("rsync -avz --relative", hash_check)
    assert release_check < state_check < hash_check < sync
    for forbidden in ("run-migrations", "systemctl restart", "systemctl enable", "transfer-agent-runtime-ownership"):
        assert forbidden not in control_branch


def test_control_plane_deploy_mode_rejects_mixed_scope_before_release_checks(
    tmp_path: Path
) -> None:
    manifest = tmp_path / "reviewed.sha256"
    manifest.write_text("reviewed\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash", str(DEPLOY / "deploy.sh"),
            "--runtime-control-plane-flags-off-update",
            "--expected-unit-manifest", str(manifest),
            "--backend-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "不能与其他部署模式组合" in result.stderr


def _control_plane_release_harness(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    release_root = tmp_path / "release"
    deploy_dir = release_root / "deploy"
    fake_bin = tmp_path / "release-bin"
    deploy_dir.mkdir(parents=True)
    fake_bin.mkdir()
    for name in (
        "runtime-flags-off-install.sh",
        "check-agent-runtime-unit-states.sh",
        "check-control-plane-unit-manifest.sh",
        "deploy-helpers.sh",
    ):
        (deploy_dir / name).write_bytes((DEPLOY / name).read_bytes())
    (deploy_dir / "config.env").write_text(
        "SERVER_HOST=runtime.example\nSERVER_USER=deploy\nSERVER_PORT=22\n"
        "REMOTE_APP_DIR=/remote/app\nREMOTE_BACKEND_DIR=/remote/app/backend\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "reviewed.sha256"
    manifest.write_text(
        "\n".join(f"{'1' * 64}  {service}.service" for service in SERVICES) + "\n",
        encoding="utf-8",
    )
    calls = tmp_path / "release-calls"
    install_marker = tmp_path / "remote-install"
    rollback_marker = tmp_path / "remote-rollback"
    git = fake_bin / "git"
    git.write_text(
        "#!/bin/sh\n"
        f"case \"$1\" in rev-parse) echo '{RELEASE_SHA}';; ls-remote) echo '{RELEASE_SHA} refs/heads/main';; status) exit 0;; *) exit 1;; esac\n",
        encoding="utf-8",
    )
    rsync = fake_bin / "rsync"
    rsync.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/bin/bash\n"
        "test \"${1:-}\" != -p || shift 2\n"
        "shift\n"
        "if [[ \"${4:-}\" =~ ^[0-9a-f]{64}$ ]]; then exit 0; fi\n"
        "if [ \"$#\" -eq 1 ]; then\n"
        "  printf '%s\\n' \"$1\" >> \"$RELEASE_CALLS\"\n"
        "  if [[ \"$1\" == *' control-plane-only '* ]]; then touch \"$INSTALL_MARKER\"; exit 0; fi\n"
        "  if [[ \"$1\" == *' rollback '* ]]; then touch \"$ROLLBACK_MARKER\"; exit 0; fi\n"
        "fi\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$RELEASE_CALLS\"\n"
        "if [ -f \"$INSTALL_MARKER\" ] && [ \"$1\" = is-active ]; then echo active; exit 0; fi\n"
        "if [ \"$1\" = is-active ]; then echo inactive; else echo disabled; fi\n",
        encoding="utf-8",
    )
    for command in (git, rsync, ssh, systemctl):
        command.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RELEASE_CALLS": str(calls),
        "INSTALL_MARKER": str(install_marker),
        "ROLLBACK_MARKER": str(rollback_marker),
    }
    return release_root, env, manifest


def test_outer_postcheck_failure_invokes_release_bound_rollback(tmp_path: Path) -> None:
    release_root, env, manifest = _control_plane_release_harness(tmp_path)
    result = subprocess.run(
        [
            "bash", "deploy/runtime-flags-off-install.sh",
            "--runtime-control-plane-flags-off-update",
            "--expected-unit-manifest", str(manifest),
        ],
        cwd=release_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert Path(env["ROLLBACK_MARKER"]).exists()
    calls = Path(env["RELEASE_CALLS"]).read_text()
    assert f"rollback {RELEASE_SHA}" in calls
    assert "everydayai-sandbox-worker" not in calls
