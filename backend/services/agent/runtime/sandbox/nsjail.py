"""Linux-only nsjail subprocess delegate for remote security contracts."""

from __future__ import annotations

import asyncio
import errno
import os
import signal
from pathlib import Path

from .launcher import (
    IsolationProbe,
    SandboxLaunchRequest,
    SandboxLaunchResult,
    SandboxProcessPort,
)


class NsJailSubprocessLauncher:
    """One process group and one nsjail invocation per Sandbox Job."""

    def __init__(
        self, *, rootfs: str | Path, python_path: str,
        seccomp_policy: str | Path,
    ) -> None:
        root = Path(rootfs)
        policy = Path(seccomp_policy)
        if not root.is_absolute():
            raise ValueError("SANDBOX_ROOTFS_MUST_BE_ABSOLUTE")
        if not policy.is_absolute():
            raise ValueError("SANDBOX_SECCOMP_POLICY_MUST_BE_ABSOLUTE")
        if not python_path.startswith("/"):
            raise ValueError("SANDBOX_PYTHON_PATH_MUST_BE_ABSOLUTE")
        self._rootfs = root.resolve()
        self._seccomp_policy = policy.resolve()
        self._python_path = python_path
        self._processes: dict[str, _NsJailProcess] = {}

    def probe(self) -> IsolationProbe:
        probe = IsolationProbe.inspect()
        if not probe.ready:
            return probe
        if not self._rootfs.is_dir():
            return IsolationProbe(
                ready=False, code="SANDBOX_ROOTFS_REQUIRED",
                nsjail_path=probe.nsjail_path,
                cgroup_root=probe.cgroup_root,
            )
        if not self._seccomp_policy.is_file():
            return IsolationProbe(
                ready=False, code="SANDBOX_SECCOMP_POLICY_REQUIRED",
                nsjail_path=probe.nsjail_path,
                cgroup_root=probe.cgroup_root,
            )
        return probe

    async def launch(self, request: SandboxLaunchRequest) -> SandboxProcessPort:
        probe = self.probe()
        if not probe.ready or probe.nsjail_path is None:
            raise RuntimeError(probe.code)
        code_path = request.input_dir / "code.py"
        _exclusive_write(code_path, request.code)
        command = _command(
            nsjail=probe.nsjail_path, rootfs=self._rootfs,
            python_path=self._python_path,
            seccomp_policy=self._seccomp_policy, request=request,
        )
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        handle = _NsJailProcess(
            process=process, timeout=request.limits.timeout_seconds,
        )
        self._processes[request.job_id] = handle
        return handle

    async def query(self, job_id: str) -> SandboxLaunchResult | None:
        handle = self._processes.get(job_id)
        if handle is None or handle.running:
            return None
        return handle.result


class _NsJailProcess:
    def __init__(
        self, *, process: asyncio.subprocess.Process, timeout: int,
    ) -> None:
        self._process = process
        self._timeout = timeout
        self._pgid = process.pid
        self.result: SandboxLaunchResult | None = None

    @property
    def running(self) -> bool:
        return self._process.returncode is None

    async def wait(self) -> SandboxLaunchResult:
        if self.result is not None:
            return self.result
        try:
            stdout, stderr = await asyncio.wait_for(
                self._process.communicate(), timeout=self._timeout + 5,
            )
            outcome = "succeeded" if self._process.returncode == 0 else "failed"
        except asyncio.TimeoutError:
            await self.request_cancel()
            await self._process.wait()
            stdout, stderr = b"", b""
            outcome = "timed_out"
        self.result = SandboxLaunchResult(
            outcome=outcome, stdout=stdout, stderr=stderr,
            exit_code=self._process.returncode,
            process_tree_terminated=await self.prove_terminated(),
        )
        return self.result

    async def request_cancel(self) -> bool:
        try:
            os.killpg(self._pgid, signal.SIGKILL)
            return True
        except ProcessLookupError:
            return True
        except OSError:
            return False

    async def prove_terminated(self) -> bool:
        if self._process.returncode is None:
            return False
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return True
        except OSError as error:
            return error.errno == errno.ESRCH
        return False


def _command(
    *, nsjail: str, rootfs: Path, python_path: str,
    seccomp_policy: Path, request: SandboxLaunchRequest,
) -> list[str]:
    limits = request.limits
    return [
        nsjail, "--mode", "o", "--quiet",
        "--chroot", str(rootfs),
        "--hostname", "sandbox-job",
        "--cwd", "/job/output",
        "--user", "65534:65534:1",
        "--group", "65534:65534:1",
        "--disable_proc",
        "--use_cgroupv2",
        "--seccomp_policy", str(seccomp_policy),
        "--env", "HOME=/tmp",
        "--env", "LANG=C.UTF-8",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--time_limit", str(limits.timeout_seconds),
        "--rlimit_nofile", str(limits.file_count + 32),
        "--rlimit_fsize", str(limits.disk_bytes),
        "--cgroup_mem_max", str(limits.memory_bytes),
        "--cgroup_pids_max", str(limits.pids),
        "--cgroup_cpu_ms_per_sec", str(limits.cpu_millis),
        "-R", f"{request.input_dir}:/job/input",
        "-B", f"{request.output_dir}:/job/output",
        "--", python_path, "-I", "/job/input/code.py",
    ]


def _exclusive_write(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
