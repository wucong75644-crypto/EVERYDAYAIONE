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
    kek = backend_dir / ".env.kek"
    kek.write_text(
        "CONFIG_KEK_CURRENT_VERSION=v1\n"
        "CONFIG_KEK_KEYRING_JSON='"
        '{"v1":"BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ="}'
        "'\n",
        encoding="utf-8",
    )
    kek.chmod(0o600)
    return query
def _read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())
def test_provisioner_defaults_flags_off_and_explicitly_enables_runtime_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    provisioner = _load_provisioner()
    backend_dir = tmp_path / "backend"
    env_dir = tmp_path / "etc-everydayai"
    transaction_root = tmp_path / "transactions"
    env_dir.mkdir()
    query = _write_source_envs(backend_dir)
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(provisioner, "_resolve_owner", lambda _name=None: (uid, gid))
    monkeypatch.setattr(provisioner, "_resolve_transaction_owner", lambda: (uid, gid))
    monkeypatch.setattr(provisioner.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provisioner.os, "chown", lambda path, owner, group: None)
    argv = [
        "prepare",
        "--backend-dir", str(backend_dir), "--env-dir", str(env_dir),
        "--release-sha", RELEASE_SHA, "--transaction-root", str(transaction_root),
    ]
    assert provisioner.main(argv) == 0
    publish_argv = [
        "publish", "--env-dir", str(env_dir), "--release-sha", RELEASE_SHA,
        "--transaction-root", str(transaction_root),
    ]
    assert provisioner.main(publish_argv) == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for secret in (*SECRETS.values(), MIGRATOR_SECRET, "redis!secret#value"):
        assert secret not in argv
        assert secret not in output
    assert sorted(path.name for path in env_dir.iterdir()) == sorted((
        "agent-runtime-worker.env", "agent-runtime-model.env",
        "agent-projection-worker.env",
        "agent-authorization-worker.env",
    ))
    for path in env_dir.iterdir():
        assert path.stat().st_mode & 0o777 == 0o640
        assert (path.stat().st_uid, path.stat().st_gid) == (uid, gid)
        values = _read_env(path)
        if path.name == "agent-runtime-model.env":
            assert set(values) == {"CONFIG_KEK_CURRENT_VERSION", "CONFIG_KEK_KEYRING_JSON"}
            continue
        assert values["AGENT_RUNTIME_RELEASE_REVISION"] == RELEASE_SHA
        dsn = values["WORKER_DATABASE_URL"]
        assert "@[::1]:5544/everydayai?" in dsn
        assert dsn.endswith(query)
        assert "%40%3A%2F%3F%23%5B%5D%25%2B%20space-" in dsn
    runtime = _read_env(env_dir / "agent-runtime-worker.env")
    projection = _read_env(env_dir / "agent-projection-worker.env")
    assert runtime["AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED"] == "false"
    assert runtime["AGENT_RUNTIME_MEDIA_ENABLED"] == "false"
    assert runtime["AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED"] == "false"
    assert runtime["SANDBOX_RUNTIME_REVISION"] == "unprovisioned"
    assert projection["REDIS_HOST"] == "redis.internal"
    assert projection["REDIS_PASSWORD"] == "redis!secret#value"
    assert projection["AGENT_RUNTIME_MEDIA_ENABLED"] == "false"
    assert projection["AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED"] == "false"
    assert projection["MEDIA_WORKSPACE_ROOT"] == "/mnt/nas-workspace"
    assert projection["MEDIA_CDN_DOMAIN"] == ""
    assert projection["MEDIA_RESULT_ALLOWED_HOSTS"] == ""
    assert projection["SENTRY_DSN"] == "https://public@example.invalid/42"
    assert projection["ENVIRONMENT"] == "production-c7"
    assert not (env_dir / "sandbox-worker.env").exists()

    runtime_release = "d" * 40
    on_argv = [
        "prepare",
        "--backend-dir", str(backend_dir), "--env-dir", str(env_dir),
        "--release-sha", runtime_release, "--transaction-root", str(transaction_root),
        "--media-on", "--runtime-on",
    ]
    assert provisioner.main(on_argv) == 0
    assert provisioner.main([
        "publish", "--env-dir", str(env_dir), "--release-sha", runtime_release,
        "--transaction-root", str(transaction_root),
    ]) == 0
    runtime_enabled = _read_env(env_dir / "agent-runtime-worker.env")
    projection_enabled = _read_env(env_dir / "agent-projection-worker.env")
    assert runtime_enabled["AGENT_RUNTIME_PRODUCTION_COMPOSITION_ENABLED"] == "true"
    assert runtime_enabled["AGENT_RUNTIME_MEDIA_ENABLED"] == "true"
    assert runtime_enabled["AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED"] == "true"
    assert projection_enabled["AGENT_RUNTIME_MEDIA_ENABLED"] == "true"
    assert projection_enabled["AGENT_RUNTIME_MEDIA_PROVIDER_PROBE_PASSED"] == "true"
def _write_fake_commands(fake_bin: Path, calls: Path) -> None:
    fake_bin.mkdir()
    install = fake_bin / "install"
    install.write_text(
        "#!/bin/bash\n"
        "printf 'install %s\\n' \"$*\" >> \"$CALLS\"\n"
        "if [ -n \"${FAIL_SERVICE:-}\" ] && [[ \"$*\" == *\"${FAIL_SERVICE}.service.c7-\"* ]] && [ ! -e \"$FAIL_MARKER\" ]; then\n"
        "  touch \"$FAIL_MARKER\"\n"
        "  exit 42\n"
        "fi\n"
        "exec /usr/bin/install \"$@\"\n",
        encoding="utf-8",
    )
    install.chmod(0o755)
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/bash\n"
        "printf 'systemctl %s\\n' \"$*\" >> \"$CALLS\"\n"
        "if [ \"$2\" = everydayai-agent-model-gateway ]; then "
        "if [ \"${LEGACY_GATEWAY_INSTALLED:-false}\" = true ]; then "
        "[ \"$1\" = is-active ] && echo inactive || echo disabled; "
        "else [ \"$1\" = is-active ] && echo inactive || echo not-found; fi; exit 0; fi\n"
        "if [ \"$1\" = daemon-reload ]; then\n"
        "  count=0; test ! -f \"$RELOAD_COUNT\" || count=$(cat \"$RELOAD_COUNT\")\n"
        "  echo $((count + 1)) > \"$RELOAD_COUNT\"\n"
        "  if [ \"${FAIL_RELOAD:-false}\" = true ] && [ \"$count\" -eq 0 ]; then exit 43; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${PRE_ACTIVE:-false}\" = true ] && [ \"$1\" = is-active ]; then echo active; exit 0; fi\n"
        "if [ \"${POST_ACTIVE:-false}\" = true ] && [ -f \"$RELOAD_COUNT\" ] && [ \"$1\" = is-active ]; then echo active; exit 0; fi\n"
        "if [ \"$1\" = is-active ]; then echo inactive; else echo disabled; fi\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
def _write_fake_env_tool(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse, os, pathlib, sys\n"
        "p=argparse.ArgumentParser(); p.add_argument('op'); p.add_argument('--env-dir'); "
        "p.add_argument('--release-sha'); p.add_argument('--transaction-root'); a=p.parse_args()\n"
        "names=('agent-runtime-worker.env','agent-runtime-model.env','agent-projection-worker.env','agent-authorization-worker.env')\n"
        "root=pathlib.Path(a.transaction_root)/a.release_sha; env=pathlib.Path(a.env_dir); state=root/'fake-env-state'\n"
        "with open(os.environ['CALLS'],'a') as calls: calls.write('env '+a.op+'\\n')\n"
        "def same(x,y): return x.exists() and y.exists() and x.read_bytes()==y.read_bytes()\n"
        "if a.op=='preflight': sys.exit(0 if state.read_text()=='prepared' and all(same(env/n,root/'env-old'/n) for n in names) else 1)\n"
        "if a.op=='publish':\n"
        " for n in names: (env/n).write_bytes((root/'env-new'/n).read_bytes())\n"
        " state.write_text('published'); sys.exit(0)\n"
        "if a.op=='verify':\n"
        " if os.environ.get('FAIL_ENV_VERIFY')=='true': sys.exit(44)\n"
        " sys.exit(0 if state.read_text()=='published' and all(same(env/n,root/'env-new'/n) for n in names) else 1)\n"
        "if a.op=='rollback-preflight': sys.exit(0 if all(same(env/n,root/'env-old'/n) or same(env/n,root/'env-new'/n) for n in names) else 1)\n"
        "if a.op=='rollback':\n"
        " for n in names: (env/n).write_bytes((root/'env-old'/n).read_bytes())\n"
        " state.write_text('restored'); sys.exit(0)\n",
        encoding="utf-8",
    )
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
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    release_dir = backup_root / RELEASE_SHA
    old_env_dir = release_dir / "env-old"
    new_env_dir = release_dir / "env-new"
    old_env_dir.mkdir(parents=True, mode=0o700)
    new_env_dir.mkdir(mode=0o700)
    release_dir.chmod(0o700)
    for name in (
        "agent-runtime-worker.env", "agent-runtime-model.env",
        "agent-projection-worker.env",
        "agent-authorization-worker.env",
    ):
        (env_dir / name).write_text(f"old:{name}\n", encoding="utf-8")
        (old_env_dir / name).write_text(f"old:{name}\n", encoding="utf-8")
        (new_env_dir / name).write_text(f"new:{name}\n", encoding="utf-8")
    (release_dir / "fake-env-state").write_text("prepared", encoding="utf-8")
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
    env_tool = tmp_path / "fake-env-tool.py"
    _write_fake_env_tool(env_tool)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "RELOAD_COUNT": str(reload_count),
        "FAIL_MARKER": str(fail_marker),
        "SYSTEMD_UNIT_DIR": str(target_dir),
        "CONTROL_PLANE_DEPLOY_DIR": str(source_dir),
        "CONTROL_PLANE_ENV_DIR": str(env_dir),
        "CONTROL_PLANE_ENV_TOOL": str(env_tool),
        "CONTROL_PLANE_TRANSACTION_ROOT": str(backup_root),
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
    assert not (backup_root / RELEASE_SHA / "units").exists()
def test_review_manifest_content_is_never_logged(tmp_path: Path) -> None:
    env, _, _, backup_root, manifest = _unit_harness(tmp_path)
    marker = "manifest-must-not-leak-secret"
    manifest.write_text(marker + "\n", encoding="utf-8")
    result = _run_updater(env, manifest, operation="preflight")
    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
    assert not (backup_root / RELEASE_SHA / "units").exists()


def test_installed_legacy_gateway_blocks_update_before_writes(tmp_path: Path) -> None:
    env, _, target_dir, backup_root, manifest = _unit_harness(tmp_path)
    env["LEGACY_GATEWAY_INSTALLED"] = "true"
    before = {path.name: path.read_bytes() for path in target_dir.iterdir()}
    result = _run_updater(env, manifest)
    assert result.returncode != 0
    assert "legacy Model Gateway" in result.stderr
    assert {path.name: path.read_bytes() for path in target_dir.iterdir()} == before
    assert not (backup_root / RELEASE_SHA / "units").exists()
@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate"))
def test_manifest_missing_extra_or_duplicate_is_rejected_before_writes(
    tmp_path: Path, mutation: str,
) -> None:
    env, _, _, backup_root, manifest = _unit_harness(tmp_path)
    lines = manifest.read_text(encoding="utf-8").splitlines()
    if mutation == "missing":
        lines.pop()
    elif mutation == "extra":
        lines.append(f"{'1' * 64}  everydayai-sandbox-worker.service")
    else:
        lines.append(lines[0])
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = _run_updater(env, manifest)
    assert result.returncode != 0
    assert not (backup_root / RELEASE_SHA / "units").exists()
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
    assert not (backup_root / RELEASE_SHA / "units").exists()
def test_all_units_are_backed_up_before_any_atomic_replacement(tmp_path: Path) -> None:
    env, source_dir, target_dir, backup_root, manifest = _unit_harness(tmp_path)
    result = _run_updater(env, manifest)
    assert result.returncode == 0, result.stderr
    calls = Path(env["CALLS"]).read_text().splitlines()
    backup_calls = [
        index for index, call in enumerate(calls) if "/.units.prepare." in call
    ]
    replace_calls = [index for index, call in enumerate(calls) if ".c7-" in call]
    publish_call = calls.index("env publish")
    assert len(backup_calls) >= 3
    assert max(backup_calls[:3]) < publish_call < min(replace_calls)
    for service in SERVICES:
        assert (target_dir / f"{service}.service").read_bytes() == (
            source_dir / f"{service}.service"
        ).read_bytes()
        assert (backup_root / RELEASE_SHA / "units" / f"{service}.service").read_text().startswith(
            "reviewed-old:"
        )
    assert all(path.read_text().startswith("new:") for path in Path(
        env["CONTROL_PLANE_ENV_DIR"]
    ).iterdir())
def test_unified_rollback_is_idempotent_and_unit_hash_fenced(tmp_path: Path) -> None:
    idempotent_root = tmp_path / "idempotent"
    idempotent_root.mkdir()
    env, _, target_dir, _, manifest = _unit_harness(idempotent_root)
    old_units = {path.name: path.read_bytes() for path in target_dir.iterdir()}
    assert _run_updater(env, manifest).returncode == 0
    assert _run_updater(env, manifest, "rollback").returncode == 0
    assert _run_updater(env, manifest, "rollback").returncode == 0
    assert {path.name: path.read_bytes() for path in target_dir.iterdir()} == old_units
    wrong_release = subprocess.run(
        ["bash", str(UPDATER), "rollback", "e" * 40], env=env,
        capture_output=True, text=True, check=False,
    )
    assert wrong_release.returncode != 0
    assert {path.name: path.read_bytes() for path in target_dir.iterdir()} == old_units
    fenced_root = tmp_path / "fenced"
    fenced_root.mkdir()
    env2, _, target2, _, manifest2 = _unit_harness(fenced_root)
    assert _run_updater(env2, manifest2).returncode == 0
    (target2 / f"{SERVICES[0]}.service").write_text("foreign unit\n", encoding="utf-8")
    env_dir = Path(env2["CONTROL_PLANE_ENV_DIR"])
    before = {
        **{f"unit:{p.name}": p.read_bytes() for p in target2.iterdir()},
        **{f"env:{p.name}": p.read_bytes() for p in env_dir.iterdir()},
    }
    result = _run_updater(env2, manifest2, "rollback")
    after = {
        **{f"unit:{p.name}": p.read_bytes() for p in target2.iterdir()},
        **{f"env:{p.name}": p.read_bytes() for p in env_dir.iterdir()},
    }
    assert result.returncode != 0
    assert before == after
@pytest.mark.parametrize("failure", (*SERVICES, "reload", "postcheck", "env-postcheck"))
def test_update_failure_restores_all_reviewed_units(
    tmp_path: Path, failure: str
) -> None:
    env, _, target_dir, backup_root, manifest = _unit_harness(tmp_path)
    before = {path.name: path.read_bytes() for path in target_dir.iterdir()}
    if failure in SERVICES:
        env["FAIL_SERVICE"] = failure
    else:
        env[{"reload": "FAIL_RELOAD", "postcheck": "POST_ACTIVE",
             "env-postcheck": "FAIL_ENV_VERIFY"}[failure]] = "true"
    result = _run_updater(env, manifest)
    assert result.returncode != 0
    assert {path.name: path.read_bytes() for path in target_dir.iterdir()} == before
    assert all((backup_root / RELEASE_SHA / "units" / name).exists() for name in before)
    env_dir = Path(env["CONTROL_PLANE_ENV_DIR"])
    assert all((env_dir / name).read_text().startswith("old:") for name in (
        "agent-runtime-worker.env", "agent-runtime-model.env",
        "agent-projection-worker.env",
        "agent-authorization-worker.env",
    ))
    if failure not in {"env-postcheck", SERVICES[0]}:
        assert int(Path(env["RELOAD_COUNT"]).read_text()) >= 1
    assert "已统一恢复" in result.stderr
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
    assert 'local env_python="${backend_dir}/venv/bin/python"' in INSTALLER
    assert 'CONTROL_PLANE_ENV_PYTHON="$env_python"' in INSTALLER
    updater = UPDATER.read_text(encoding="utf-8")
    assert "env_python=${CONTROL_PLANE_ENV_PYTHON:-python3}" in updater
    assert '"$env_python" "$env_tool"' in updater
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
