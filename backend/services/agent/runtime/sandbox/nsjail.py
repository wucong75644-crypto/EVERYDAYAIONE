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
    verify_sha256,
)
from .rootfs_manifest import verify_manifest


class NsJailSubprocessLauncher:
    """One process group and one nsjail invocation per Sandbox Job."""

    def __init__(
        self, *, rootfs: str | Path, python_path: str,
        seccomp_policy: str | Path, nsjail_path: str | Path,
        nsjail_sha256: str, rootfs_manifest: str | Path,
        rootfs_sha256: str, seccomp_sha256: str,
        cgroup_v2_mount: str | Path, quiet: bool = True,
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
        self._nsjail_path = Path(nsjail_path).resolve()
        self._nsjail_sha256 = nsjail_sha256
        self._rootfs_manifest = Path(rootfs_manifest).resolve()
        self._rootfs_sha256 = rootfs_sha256
        self._seccomp_sha256 = seccomp_sha256
        self._cgroup_v2_mount = Path(cgroup_v2_mount).resolve()
        self._python_path = python_path
        self._quiet = quiet
        self._processes: dict[str, _NsJailProcess] = {}

    def probe(self) -> IsolationProbe:
        probe = IsolationProbe.inspect(str(self._nsjail_path))
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
        if (
            not self._cgroup_v2_mount.is_dir()
            or not os.access(self._cgroup_v2_mount, os.W_OK)
        ):
            return IsolationProbe(
                ready=False, code="SANDBOX_CGROUP_DELEGATION_REQUIRED",
            )
        if not verify_sha256(self._nsjail_path, self._nsjail_sha256):
            return IsolationProbe(
                ready=False, code="SANDBOX_NSJAIL_HASH_MISMATCH",
            )
        if not verify_sha256(self._rootfs_manifest, self._rootfs_sha256):
            return IsolationProbe(
                ready=False, code="SANDBOX_ROOTFS_HASH_MISMATCH",
            )
        if not verify_manifest(self._rootfs, self._rootfs_manifest):
            return IsolationProbe(
                ready=False, code="SANDBOX_ROOTFS_CONTENT_MISMATCH",
            )
        if not verify_sha256(self._seccomp_policy, self._seccomp_sha256):
            return IsolationProbe(
                ready=False, code="SANDBOX_SECCOMP_HASH_MISMATCH",
            )
        return probe

    async def launch(self, request: SandboxLaunchRequest) -> SandboxProcessPort:
        probe = self.probe()
        if not probe.ready or probe.nsjail_path is None:
            raise RuntimeError(probe.code)
        code_path = request.input_dir / "code.py"
        _exclusive_write(code_path, request.code)
        command = _command(
            nsjail=str(self._nsjail_path), rootfs=self._rootfs,
            python_path=self._python_path,
            seccomp_policy=self._seccomp_policy, request=request,
            cgroup_v2_mount=self._cgroup_v2_mount,
            quiet=self._quiet,
        )
        cgroup_before = _nsjail_cgroups(self._cgroup_v2_mount)
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        handle = _NsJailProcess(
            process=process, timeout=request.limits.timeout_seconds,
            cgroup_root=self._cgroup_v2_mount,
            cgroup_before=cgroup_before,
            output_dir=request.output_dir,
            disk_bytes=request.limits.disk_bytes,
            file_count=request.limits.file_count,
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
        cgroup_root: Path, cgroup_before: frozenset[str],
        output_dir: Path, disk_bytes: int, file_count: int,
    ) -> None:
        self._process = process
        self._timeout = timeout
        self._pgid = process.pid
        self._cgroup_root = cgroup_root
        self._cgroup_before = cgroup_before
        self._output_dir = output_dir
        self._disk_bytes = disk_bytes
        self._file_count = file_count
        self._cancel_lock = asyncio.Lock()
        self.result: SandboxLaunchResult | None = None

    @property
    def running(self) -> bool:
        return self._process.returncode is None

    async def wait(self) -> SandboxLaunchResult:
        if self.result is not None:
            return self.result
        stdout_task = asyncio.create_task(
            self._read_bounded(self._process.stdout),
        )
        stderr_task = asyncio.create_task(
            self._read_bounded(self._process.stderr),
        )
        output_guard = asyncio.create_task(self._guard_output_limits())
        try:
            await asyncio.wait_for(
                self._process.wait(), timeout=self._timeout + 5,
            )
            outcome = "succeeded" if self._process.returncode == 0 else "failed"
        except asyncio.TimeoutError:
            await self.request_cancel()
            outcome = "timed_out"
        stdout, stdout_exceeded = await stdout_task
        stderr, stderr_exceeded = await stderr_task
        output_exceeded = await output_guard
        if outcome != "timed_out" and (
            stdout_exceeded or stderr_exceeded or output_exceeded
        ):
            outcome = "resource_limit"
        self.result = SandboxLaunchResult(
            outcome=outcome, stdout=stdout, stderr=stderr,
            exit_code=self._process.returncode,
            process_tree_terminated=await self.prove_terminated(),
        )
        return self.result

    async def request_cancel(self) -> bool:
        async with self._cancel_lock:
            if self._process.returncode is not None:
                return await self.prove_terminated()
            try:
                os.killpg(self._pgid, signal.SIGTERM)
            except ProcessLookupError:
                return True
            except OSError:
                return False
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self._pgid, signal.SIGKILL)
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except (ProcessLookupError, asyncio.TimeoutError, OSError):
                    return False
            return await self.prove_terminated()

    async def _read_bounded(
        self, stream: asyncio.StreamReader | None,
        maximum: int = 1024 * 1024,
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        content = bytearray()
        exceeded = False
        while chunk := await stream.read(64 * 1024):
            if len(content) + len(chunk) > maximum:
                remaining = max(0, maximum - len(content))
                content.extend(chunk[:remaining])
                exceeded = True
                await self.request_cancel()
            elif not exceeded:
                content.extend(chunk)
        return bytes(content), exceeded

    async def _guard_output_limits(self) -> bool:
        exceeded = False
        while self._process.returncode is None:
            if _output_limits_exceeded(
                self._output_dir, self._disk_bytes, self._file_count,
            ):
                exceeded = True
                await self.request_cancel()
                break
            await asyncio.sleep(0.02)
        return exceeded or _output_limits_exceeded(
            self._output_dir, self._disk_bytes, self._file_count,
        )

    async def prove_terminated(self) -> bool:
        if self._process.returncode is None:
            return False
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return _nsjail_cgroups(self._cgroup_root) <= self._cgroup_before
        except OSError as error:
            return (
                error.errno == errno.ESRCH
                and _nsjail_cgroups(self._cgroup_root) <= self._cgroup_before
            )
        return False


def _command(
    *, nsjail: str, rootfs: Path, python_path: str,
    seccomp_policy: Path, cgroup_v2_mount: Path,
    request: SandboxLaunchRequest, quiet: bool = True,
) -> list[str]:
    limits = request.limits
    command = [
        nsjail, "--mode", "o",
        "--chroot", str(rootfs),
        "--hostname", "sandbox-job",
        "--cwd", "/job/output",
        "--user", "65534",
        "--group", "65534",
        "--disable_proc",
        "--iface_no_lo",
        "--use_cgroupv2",
        "--cgroupv2_mount", str(cgroup_v2_mount),
        "--seccomp_policy", str(seccomp_policy),
        "--env", "HOME=/tmp",
        "--env", "LANG=C.UTF-8",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--time_limit", str(limits.timeout_seconds),
        "--rlimit_nofile", str(limits.file_count + 32),
        "--rlimit_fsize", str(limits.disk_bytes // (1024 * 1024)),
        "--cgroup_mem_max", str(limits.memory_bytes),
        "--cgroup_mem_swap_max", "0",
        "--cgroup_pids_max", str(limits.pids),
        "--cgroup_cpu_ms_per_sec", str(limits.cpu_millis),
        "-R", f"{request.input_dir}:/job/input",
        "-B", f"{request.output_dir}:/job/output",
        "--", python_path, "-I", "/job/input/code.py",
    ]
    if quiet:
        command.insert(3, "--quiet")
    return command


def _exclusive_write(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _nsjail_cgroups(root: Path) -> frozenset[str]:
    try:
        return frozenset(
            entry.name for entry in root.iterdir()
            if entry.is_dir() and entry.name.startswith("NSJAIL.")
        )
    except OSError:
        return frozenset({"CGROUP_SCAN_FAILED"})


def _output_limits_exceeded(
    root: Path, max_bytes: int, max_files: int,
) -> bool:
    total = 0
    count = 0
    try:
        for entry in root.iterdir():
            metadata = entry.lstat()
            if not entry.is_file() or entry.is_symlink():
                return True
            count += 1
            total += metadata.st_size
            if count > max_files or total > max_bytes:
                return True
    except OSError:
        return True
    return False
