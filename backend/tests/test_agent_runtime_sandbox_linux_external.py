"""Real Linux/nsjail contracts; never part of the default test selection."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
from types import SimpleNamespace
from typing import Iterator

import pytest


pytestmark = pytest.mark.external


@contextmanager
def _isolated_sandbox_modules() -> Iterator[ModuleType]:
    """Load the Linux launcher without importing or replacing production packages."""
    sandbox_root = (
        Path(__file__).resolve().parents[1]
        / "services" / "agent" / "runtime" / "sandbox"
    )
    package_name = "_agent_runtime_linux_contract"
    package = ModuleType(package_name)
    package.__path__ = [str(sandbox_root)]
    loaded = [package_name]
    sys.modules[package_name] = package
    try:
        for module_name in ("contracts", "launcher", "nsjail"):
            qualified_name = f"{package_name}.{module_name}"
            spec = importlib.util.spec_from_file_location(
                qualified_name,
                sandbox_root / f"{module_name}.py",
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("SANDBOX_LINUX_CONTRACT_LOAD_FAILED")
            module = importlib.util.module_from_spec(spec)
            sys.modules[qualified_name] = module
            loaded.append(qualified_name)
            spec.loader.exec_module(module)
        contracts = sys.modules[f"{package_name}.contracts"]
        launcher = sys.modules[f"{package_name}.launcher"]
        nsjail = sys.modules[f"{package_name}.nsjail"]
        yield SimpleNamespace(
            IsolationProbe=launcher.IsolationProbe,
            NsJailSubprocessLauncher=nsjail.NsJailSubprocessLauncher,
            SandboxWorkerIdentity=nsjail.SandboxWorkerIdentity,
            SandboxLaunchRequest=launcher.SandboxLaunchRequest,
            SandboxResourceLimits=contracts.SandboxResourceLimits,
        )
    finally:
        for module_name in reversed(loaded):
            sys.modules.pop(module_name, None)


@pytest.fixture(scope="module")
def linux_api():
    if os.getenv("RUN_SANDBOX_LINUX_EXTERNAL_TESTS") != "1":
        pytest.skip("explicit Linux external contract opt-in is required")
    with _isolated_sandbox_modules() as api:
        yield api


@pytest.fixture
def linux_contract(linux_api):
    if os.geteuid() == 0 or os.getegid() == 0:
        pytest.fail("SANDBOX_LINUX_EXTERNAL_NONROOT_REQUIRED")
    rootfs = Path(os.environ["SANDBOX_ROOTFS"]).resolve()
    policy = Path(os.environ["SANDBOX_SECCOMP_POLICY"]).resolve()
    if not rootfs.is_dir() or not policy.is_file():
        pytest.fail("SANDBOX_LINUX_EXTERNAL_FIXTURE_REQUIRED")
    marker = Path(os.environ["SANDBOX_HOST_MARKER"])
    contract_root = Path(tempfile.mkdtemp(prefix="everydayai-sandbox-contract-"))
    contract_root.chmod(0o755)
    try:
        yield linux_api, rootfs, policy, marker, contract_root
    finally:
        shutil.rmtree(contract_root, ignore_errors=True)


def _launcher(api, rootfs: Path, policy: Path):
    identity = api.SandboxWorkerIdentity.capture_current_process()
    return api.NsJailSubprocessLauncher(
        rootfs=rootfs,
        python_path="/usr/bin/python3",
        seccomp_policy=policy,
        nsjail_path=os.environ["SANDBOX_NSJAIL_PATH"],
        nsjail_sha256=os.environ["SANDBOX_NSJAIL_SHA256"],
        rootfs_manifest=os.environ["SANDBOX_ROOTFS_MANIFEST"],
        rootfs_sha256=os.environ["SANDBOX_ROOTFS_SHA256"],
        seccomp_sha256=os.environ["SANDBOX_SECCOMP_SHA256"],
        cgroup_v2_mount=os.environ["SANDBOX_CGROUP_V2_MOUNT"],
        worker_identity=identity,
        quiet=False,
    )


def _request(
    api,
    tmp_path: Path,
    *,
    job_id: str,
    code: str,
    limits: dict[str, int] | None = None,
):
    input_dir = tmp_path / job_id / "input"
    output_dir = tmp_path / job_id / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    output_dir.chmod(0o700)
    return api.SandboxLaunchRequest(
        job_id=job_id,
        code=code.encode("utf-8"),
        input_dir=input_dir,
        output_dir=output_dir,
        limits=api.SandboxResourceLimits.from_request(limits),
    )


def test_linux_probe_requires_real_nsjail_and_cgroup_v2(linux_api) -> None:
    probe = linux_api.IsolationProbe.inspect()
    assert probe.ready, probe.code
    assert probe.nsjail_path
    assert probe.cgroup_root == "/sys/fs/cgroup"


@pytest.mark.asyncio
async def test_nsjail_blocks_host_network_and_readonly_input(
    linux_contract,
) -> None:
    api, rootfs, policy, marker, tmp_path = linux_contract
    code = f"""
import json
import os
import pathlib
import socket

observed = {{}}
observed["effective_gid"] = os.getegid()
observed["effective_uid"] = os.geteuid()
observed["host_marker_visible"] = pathlib.Path({str(marker)!r}).exists()
try:
    pathlib.Path("/job/input/code.py").write_text("changed")
    observed["input_writable"] = True
except OSError:
    observed["input_writable"] = False
try:
    pathlib.Path("/job/output/seccomp-mkdir").mkdir()
    observed["seccomp_mkdir_allowed"] = True
except OSError:
    observed["seccomp_mkdir_allowed"] = False
try:
    pathlib.Path("/tmp/rootfs-write").write_text("changed")
    observed["rootfs_writable"] = True
except OSError:
    observed["rootfs_writable"] = False
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
        api, tmp_path,
        job_id="11111111-1111-1111-1111-111111111111",
        code=code,
    )
    process = await _launcher(api, rootfs, policy).launch(request)
    result = await process.wait()
    assert result.outcome == "succeeded", result.stderr.decode(errors="replace")
    observed = json.loads((request.output_dir / "result.json").read_text())
    assert observed == {
        "effective_gid": 65534,
        "effective_uid": 65534,
        "host_marker_visible": False,
        "input_writable": False,
        "network_reachable": False,
        "rootfs_writable": False,
        "seccomp_mkdir_allowed": False,
    }
    assert request.output_dir.stat().st_uid == os.getuid()
    assert request.output_dir.stat().st_gid == os.getgid()
    assert result.process_tree_terminated


@pytest.mark.asyncio
async def test_nsjail_memory_limit_is_enforced(linux_contract) -> None:
    api, rootfs, policy, _, tmp_path = linux_contract
    request = _request(
        api, tmp_path,
        job_id="22222222-2222-2222-2222-222222222222",
        code=(
            "import time\n"
            "payload = bytearray(256 * 1024 * 1024)\n"
            "for offset in range(0, len(payload), 4096):\n"
            "    payload[offset] = 1\n"
            "open('/job/output/memory-ready', 'w').write('ready')\n"
            "print(len(payload), flush=True)\n"
            "time.sleep(30)\n"
        ),
        limits={"memory_bytes": 64 * 1024 * 1024},
    )
    process = await _launcher(api, rootfs, policy).launch(request)
    wait_task = asyncio.create_task(process.wait())
    for _ in range(50):
        if wait_task.done() or (request.output_dir / "memory-ready").exists():
            break
        await asyncio.sleep(0.1)
    if (request.output_dir / "memory-ready").exists():
        evidence = []
        for cgroup in Path("/sys/fs/cgroup").glob("NSJAIL.*"):
            evidence.append({
                field: (cgroup / field).read_text().strip()
                for field in ("cgroup.procs", "memory.current", "memory.max")
            })
        await process.request_cancel()
        await wait_task
        pytest.fail(f"SANDBOX_MEMORY_LIMIT_NOT_ENFORCED:{evidence}")
    result = await wait_task
    assert result.outcome != "succeeded", result.stderr.decode(errors="replace")
    assert "unrecognized option" not in result.stderr.decode(errors="replace")
    assert result.process_tree_terminated


@pytest.mark.asyncio
async def test_nsjail_output_file_limit_is_enforced(linux_contract) -> None:
    api, rootfs, policy, _, tmp_path = linux_contract
    request = _request(
        api, tmp_path,
        job_id="33333333-3333-3333-3333-333333333333",
        code=(
            "from pathlib import Path\n"
            "for index in range(101):\n"
            "    Path(f'/job/output/{index:03d}').write_text('x')\n"
        ),
        limits={"file_count": 100},
    )
    process = await _launcher(api, rootfs, policy).launch(request)
    result = await process.wait()
    assert result.outcome == "resource_limit"
    assert result.process_tree_terminated


@pytest.mark.asyncio
async def test_nsjail_stdout_is_bounded(linux_contract) -> None:
    api, rootfs, policy, _, tmp_path = linux_contract
    request = _request(
        api, tmp_path,
        job_id="44444444-4444-4444-4444-444444444444",
        code="import sys\nsys.stdout.write('x' * (2 * 1024 * 1024))\n",
    )
    process = await _launcher(api, rootfs, policy).launch(request)
    result = await process.wait()
    assert result.outcome == "resource_limit"
    assert len(result.stdout) == 1024 * 1024
    assert result.process_tree_terminated


@pytest.mark.asyncio
async def test_nsjail_cancel_terminates_process_tree(linux_contract) -> None:
    api, rootfs, policy, _, tmp_path = linux_contract
    request = _request(
        api, tmp_path,
        job_id="33333333-3333-3333-3333-333333333333",
        code=(
            "import subprocess, time\n"
            "subprocess.Popen(['/usr/bin/python3', '-c', "
            "'import time; time.sleep(30)'])\n"
            "time.sleep(30)\n"
        ),
        limits={"timeout_seconds": 45},
    )
    process = await _launcher(api, rootfs, policy).launch(request)
    await asyncio.sleep(0.2)
    assert await process.prove_terminated() is False
    wait_task = asyncio.create_task(process.wait())
    assert await process.request_cancel()
    result = await asyncio.wait_for(wait_task, timeout=10)
    assert result.outcome != "succeeded"
    assert result.process_tree_terminated
