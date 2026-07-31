from unittest.mock import AsyncMock

import pytest

from services.agent.runtime.sandbox.contracts import SandboxResourceLimits
from services.agent.runtime.sandbox.launcher import SandboxLaunchRequest
from services.agent.runtime.sandbox.nsjail import (
    SandboxWorkerIdentity, _cleanup_and_verify_cgroups, _command,
    _output_limits_exceeded,
)
from services.agent.runtime.sandbox.rootfs_manifest import (
    verify_manifest, write_manifest,
)
from services.agent.runtime.sandbox.service import SandboxJobWorkerService


def test_nsjail_command_has_readonly_input_writable_output_and_limits(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_PROCESS_ROLE", "sandbox")
    monkeypatch.setattr("os.getuid", lambda: 12345)
    monkeypatch.setattr("os.getgid", lambda: 23456)
    identity = SandboxWorkerIdentity.capture_current_process()
    input_dir, output_dir = tmp_path / "input", tmp_path / "output"
    command = _command(
        nsjail="/usr/bin/nsjail", rootfs=tmp_path / "rootfs",
        python_path="/usr/bin/python3",
        seccomp_policy=tmp_path / "sandbox.policy",
        cgroup_v2_mount=tmp_path / "cgroup",
        worker_identity=identity,
        request=SandboxLaunchRequest(
            job_id="11111111-1111-1111-1111-111111111111",
            code=b"print(1)", input_dir=input_dir, output_dir=output_dir,
            limits=SandboxResourceLimits.from_request({}),
        ),
    )
    assert ["-R", f"{input_dir}:/job/input"] == command[
        command.index("-R"):command.index("-R") + 2
    ]
    assert ["-B", f"{output_dir}:/job/output"] == command[
        command.index("-B"):command.index("-B") + 2
    ]
    assert "--disable_clone_newnet" not in command
    assert "--disable_clone_newcgroup" in command
    assert "--use_cgroupv2" in command
    assert command[command.index("--cgroupv2_mount") + 1] == str(
        tmp_path / "cgroup",
    )
    assert command[command.index("--user") + 1] == "65534:12345:1"
    assert command[command.index("--group") + 1] == "65534:23456:1"
    assert "--seccomp_policy" in command
    assert "--cgroup_mem_max" in command
    assert command[command.index("--cgroup_mem_swap_max") + 1] == "0"
    assert "--cgroup_pids_max" in command
    assert "--cgroup_cpu_ms_per_sec" in command


def test_worker_identity_is_sandbox_only_and_root_fails_closed(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_PROCESS_ROLE", "agent_runtime")
    with pytest.raises(RuntimeError, match="PROCESS_ROLE_REQUIRED"):
        SandboxWorkerIdentity.capture_current_process()
    monkeypatch.setenv("AGENT_RUNTIME_PROCESS_ROLE", "sandbox")
    monkeypatch.setattr("os.getuid", lambda: 0)
    monkeypatch.setattr("os.getgid", lambda: 0)
    with pytest.raises(RuntimeError, match="ROOT_PROCESS_FORBIDDEN"):
        SandboxWorkerIdentity.capture_current_process()


def test_rootfs_manifest_detects_content_mode_and_extra_file(tmp_path) -> None:
    root = tmp_path / "rootfs"
    root.mkdir()
    payload = root / "python"
    payload.write_bytes(b"runtime")
    payload.chmod(0o555)
    manifest = tmp_path / "rootfs.manifest"
    write_manifest(root, manifest)
    assert verify_manifest(root, manifest)
    payload.chmod(0o755)
    assert not verify_manifest(root, manifest)
    payload.chmod(0o555)
    (root / "unexpected").write_text("x")
    assert not verify_manifest(root, manifest)


def test_live_output_guard_enforces_aggregate_bytes_files_and_types(
    tmp_path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "one").write_bytes(b"1234")
    assert not _output_limits_exceeded(output, 4, 1)
    assert _output_limits_exceeded(output, 3, 1)
    (output / "two").write_bytes(b"")
    assert _output_limits_exceeded(output, 4, 1)
    (output / "two").unlink()
    (output / "link").symlink_to(output / "one")
    assert _output_limits_exceeded(output, 100, 100)


def test_terminated_process_removes_only_empty_new_cgroups(tmp_path) -> None:
    root = tmp_path / "cgroup"
    group = root / "NSJAIL.123"
    group.mkdir(parents=True)
    assert _cleanup_and_verify_cgroups(root, frozenset())
    assert not group.exists()

    group.mkdir()
    (group / "cgroup.procs").write_text("987\n")
    assert not _cleanup_and_verify_cgroups(root, frozenset())
    assert group.exists()


@pytest.mark.asyncio
async def test_worker_service_drain_stops_without_another_claim() -> None:
    worker = type("_Worker", (), {})()
    worker.drain = lambda: None
    calls = []

    async def run_once():
        calls.append("execution")
        service.stop()
        return type("_Result", (), {"worked": False})()

    worker.run_once = run_once
    worker.reconcile_next = AsyncMock()
    service = SandboxJobWorkerService(worker)
    await service.run()
    assert calls == ["execution"]
    worker.reconcile_next.assert_not_awaited()
