"""Outer release rollback contract for the single-Runtime control plane."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
RELEASE_SHA = "c" * 40
SERVICES = (
    "everydayai-agent-runtime",
    "everydayai-agent-projection", "everydayai-agent-authorization",
)


def _release_harness(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    release_root = tmp_path / "release"
    deploy_dir = release_root / "deploy"
    fake_bin = tmp_path / "release-bin"
    deploy_dir.mkdir(parents=True)
    fake_bin.mkdir()
    for name in (
        "runtime-flags-off-install.sh", "check-agent-runtime-unit-states.sh",
        "check-control-plane-unit-manifest.sh", "deploy-helpers.sh",
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
        f"case \"$1\" in rev-parse) echo '{RELEASE_SHA}';; "
        f"ls-remote) echo '{RELEASE_SHA} refs/heads/main';; "
        "status) exit 0;; *) exit 1;; esac\n",
        encoding="utf-8",
    )
    rsync = fake_bin / "rsync"
    rsync.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/bin/bash\n"
        "test \"${1:-}\" != -p || shift 2\nshift\n"
        "if [[ \"${4:-}\" =~ ^[0-9a-f]{64}$ ]]; then exit 0; fi\n"
        "if [ \"$#\" -eq 1 ]; then\n"
        "  printf '%s\\n' \"$1\" >> \"$RELEASE_CALLS\"\n"
        "  if [[ \"$1\" == *' control-plane-only '* ]]; then touch \"$INSTALL_MARKER\"; exit 0; fi\n"
        "  if [[ \"$1\" == *' rollback '* ]]; then touch \"$ROLLBACK_MARKER\"; exit 0; fi\n"
        "fi\nexec \"$@\"\n",
        encoding="utf-8",
    )
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$RELEASE_CALLS\"\n"
        "if [ \"$2\" = everydayai-agent-model-gateway ]; then "
        "[ \"$1\" = is-active ] && echo inactive || echo not-found; exit 0; fi\n"
        "if [ -f \"$INSTALL_MARKER\" ] && [ \"$1\" = is-active ]; then echo active; exit 0; fi\n"
        "if [ \"$1\" = is-active ]; then echo inactive; else echo disabled; fi\n",
        encoding="utf-8",
    )
    for command in (git, rsync, ssh, systemctl):
        command.chmod(0o755)
    env = {
        **os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RELEASE_CALLS": str(calls), "INSTALL_MARKER": str(install_marker),
        "ROLLBACK_MARKER": str(rollback_marker),
    }
    return release_root, env, manifest


def test_outer_postcheck_failure_invokes_release_bound_rollback(tmp_path: Path) -> None:
    release_root, env, manifest = _release_harness(tmp_path)
    result = subprocess.run(
        ["bash", "deploy/runtime-flags-off-install.sh",
         "--runtime-control-plane-flags-off-update",
         "--expected-unit-manifest", str(manifest)],
        cwd=release_root, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert Path(env["ROLLBACK_MARKER"]).exists()
    calls = Path(env["RELEASE_CALLS"]).read_text()
    assert f"rollback {RELEASE_SHA}" in calls
    assert f"update-control-plane-units.sh rollback {RELEASE_SHA}" in calls
    assert "everydayai-sandbox-worker" not in calls
