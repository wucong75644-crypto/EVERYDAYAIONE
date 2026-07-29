"""Injectable launcher boundary and Linux isolation preflight."""

from __future__ import annotations

import os
import platform
import shutil
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .contracts import SandboxResourceLimits


REQUIRED_CONTROLLERS = frozenset({"cpu", "memory", "pids"})


@dataclass(frozen=True, kw_only=True)
class IsolationProbe:
    ready: bool
    code: str
    nsjail_path: str | None = None
    cgroup_root: str | None = None

    @classmethod
    def inspect(cls, nsjail_path: str | None = None) -> "IsolationProbe":
        if platform.system() != "Linux":
            return cls(ready=False, code="SANDBOX_LINUX_REQUIRED")
        nsjail = nsjail_path or shutil.which("nsjail")
        if nsjail is None:
            return cls(ready=False, code="SANDBOX_NSJAIL_REQUIRED")
        cgroup = Path("/sys/fs/cgroup")
        controllers = cgroup / "cgroup.controllers"
        try:
            available = set(controllers.read_text().split())
        except OSError:
            return cls(ready=False, code="SANDBOX_CGROUP_V2_REQUIRED")
        if not REQUIRED_CONTROLLERS.issubset(available):
            return cls(ready=False, code="SANDBOX_CGROUP_CONTROLLERS_MISSING")
        if not os.access(cgroup, os.R_OK):
            return cls(ready=False, code="SANDBOX_CGROUP_UNAVAILABLE")
        return cls(
            ready=True, code="SANDBOX_ISOLATION_READY",
            nsjail_path=nsjail, cgroup_root=str(cgroup),
        )


def verify_sha256(path: str | Path, expected: str) -> bool:
    if len(expected) != 64:
        return False
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected.lower()


@dataclass(frozen=True, kw_only=True)
class SandboxLaunchRequest:
    job_id: str
    code: bytes
    input_dir: Path
    output_dir: Path
    limits: SandboxResourceLimits


@dataclass(frozen=True, kw_only=True)
class SandboxLaunchResult:
    outcome: str
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int | None = None
    process_tree_terminated: bool = False


class SandboxProcessPort(Protocol):
    async def wait(self) -> SandboxLaunchResult: ...
    async def request_cancel(self) -> bool: ...
    async def prove_terminated(self) -> bool: ...


class SandboxLauncherPort(Protocol):
    def probe(self) -> IsolationProbe: ...
    async def launch(self, request: SandboxLaunchRequest) -> SandboxProcessPort: ...
    async def query(self, job_id: str) -> SandboxLaunchResult | None: ...


class FailClosedNsJailLauncher:
    """Production launcher shell; command execution requires a passing probe."""

    def __init__(self, delegate: SandboxLauncherPort | None = None) -> None:
        self._delegate = delegate

    def probe(self) -> IsolationProbe:
        probe = IsolationProbe.inspect()
        if not probe.ready or self._delegate is None:
            return probe if not probe.ready else IsolationProbe(
                ready=False, code="SANDBOX_LAUNCHER_NOT_COMPOSED",
                nsjail_path=probe.nsjail_path,
                cgroup_root=probe.cgroup_root,
            )
        delegated = self._delegate.probe()
        return delegated if delegated.ready else delegated

    async def launch(self, request: SandboxLaunchRequest) -> SandboxProcessPort:
        probe = self.probe()
        if not probe.ready or self._delegate is None:
            raise RuntimeError(probe.code)
        return await self._delegate.launch(request)

    async def query(self, job_id: str) -> SandboxLaunchResult | None:
        probe = self.probe()
        if not probe.ready or self._delegate is None:
            raise RuntimeError(probe.code)
        return await self._delegate.query(job_id)
