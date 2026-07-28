"""Real Linux/nsjail contracts; never part of the default test selection."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from types import ModuleType

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
for _package, _path in (
    ("services", _BACKEND_ROOT / "services"),
    ("services.agent", _BACKEND_ROOT / "services" / "agent"),
    ("services.agent.runtime", _BACKEND_ROOT / "services" / "agent" / "runtime"),
    (
        "services.agent.runtime.sandbox",
        _BACKEND_ROOT / "services" / "agent" / "runtime" / "sandbox",
    ),
):
    _module = ModuleType(_package)
    _module.__path__ = [str(_path)]
    sys.modules[_package] = _module

from services.agent.runtime.sandbox.contracts import SandboxResourceLimits
from services.agent.runtime.sandbox.launcher import (
    IsolationProbe,
    SandboxLaunchRequest,
)
from services.agent.runtime.sandbox.nsjail import NsJailSubprocessLauncher


pytestmark = pytest.mark.external


@pytest.fixture
def linux_contract(tmp_path: Path):
    if os.getenv("RUN_SANDBOX_LINUX_EXTERNAL_TESTS") != "1":
        pytest.skip("explicit Linux external contract opt-in is required")
    if os.geteuid() != 0:
        pytest.fail("SANDBOX_LINUX_EXTERNAL_ROOT_REQUIRED")
    rootfs = Path(os.environ["SANDBOX_ROOTFS"]).resolve()
    policy = Path(os.environ["SANDBOX_SECCOMP_POLICY"]).resolve()
    if not rootfs.is_dir() or not policy.is_file():
        pytest.fail("SANDBOX_LINUX_EXTERNAL_FIXTURE_REQUIRED")
    marker = Path("/sandbox-host-secret-everydayai-contract")
    marker.write_text("must-not-be-visible", encoding="utf-8")
    try:
        yield rootfs, policy, marker, tmp_path
    finally:
        marker.unlink(missing_ok=True)


def _launcher(rootfs: Path, policy: Path) -> NsJailSubprocessLauncher:
    return NsJailSubprocessLauncher(
        rootfs=rootfs,
        python_path="/usr/bin/python3",
        seccomp_policy=policy,
    )


def _request(
    tmp_path: Path,
    *,
    job_id: str,
    code: str,
    limits: dict[str, int] | None = None,
) -> SandboxLaunchRequest:
    input_dir = tmp_path / job_id / "input"
    output_dir = tmp_path / job_id / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    return SandboxLaunchRequest(
        job_id=job_id,
        code=code.encode("utf-8"),
        input_dir=input_dir,
        output_dir=output_dir,
        limits=SandboxResourceLimits.from_request(limits),
    )


def test_linux_probe_requires_real_nsjail_and_cgroup_v2() -> None:
    probe = IsolationProbe.inspect()
    assert probe.ready, probe.code
    assert probe.nsjail_path
    assert probe.cgroup_root == "/sys/fs/cgroup"


@pytest.mark.asyncio
async def test_nsjail_blocks_host_network_and_readonly_input(
    linux_contract,
) -> None:
    rootfs, policy, marker, tmp_path = linux_contract
    code = f"""
import json
import os
import pathlib
import socket

observed = {{}}
observed["host_marker_visible"] = pathlib.Path({str(marker)!r}).exists()
try:
    pathlib.Path("/job/input/code.py").write_text("changed")
    observed["input_writable"] = True
except OSError:
    observed["input_writable"] = False
try:
    os.getppid()
    observed["seccomp_getppid_allowed"] = True
except OSError:
    observed["seccomp_getppid_allowed"] = False
try:
    client = socket.create_connection(("1.1.1.1", 53), timeout=0.5)
    client.close()
    observed["network_reachable"] = True
except OSError:
    observed["network_reachable"] = False
pathlib.Path("/job/output/result.json").write_text(
    json.dumps(observed, sort_keys=True)
)
print(json.dumps(observed, sort_keys=True))
"""
    request = _request(
        tmp_path,
        job_id="11111111-1111-1111-1111-111111111111",
        code=code,
    )
    process = await _launcher(rootfs, policy).launch(request)
    result = await process.wait()
    assert result.outcome == "succeeded", result.stderr.decode(errors="replace")
    observed = json.loads((request.output_dir / "result.json").read_text())
    assert observed == {
        "host_marker_visible": False,
        "input_writable": False,
        "network_reachable": False,
        "seccomp_getppid_allowed": False,
    }
    assert result.process_tree_terminated


@pytest.mark.asyncio
async def test_nsjail_memory_limit_is_enforced(linux_contract) -> None:
    rootfs, policy, _, tmp_path = linux_contract
    request = _request(
        tmp_path,
        job_id="22222222-2222-2222-2222-222222222222",
        code='payload = bytearray(256 * 1024 * 1024); print(len(payload))',
        limits={"memory_bytes": 64 * 1024 * 1024},
    )
    result = await (await _launcher(rootfs, policy).launch(request)).wait()
    assert result.outcome != "succeeded"
    assert result.process_tree_terminated


@pytest.mark.asyncio
async def test_nsjail_cancel_terminates_process_tree(linux_contract) -> None:
    rootfs, policy, _, tmp_path = linux_contract
    request = _request(
        tmp_path,
        job_id="33333333-3333-3333-3333-333333333333",
        code=(
            "import subprocess, time\n"
            "subprocess.Popen(['/usr/bin/python3', '-c', "
            "'import time; time.sleep(30)'])\n"
            "time.sleep(30)\n"
        ),
        limits={"timeout_seconds": 45},
    )
    process = await _launcher(rootfs, policy).launch(request)
    wait_task = asyncio.create_task(process.wait())
    await asyncio.sleep(1)
    assert await process.request_cancel()
    result = await asyncio.wait_for(wait_task, timeout=10)
    assert result.outcome != "succeeded"
    assert result.process_tree_terminated
