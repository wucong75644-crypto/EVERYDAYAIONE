"""Private immutable input/output store for Sandbox Jobs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, kw_only=True)
class WorkspaceObject:
    reference: str
    content_sha256: str
    size_bytes: int
    media_type: str


class SandboxWorkspaceStore:
    """Path authority retained by Worker composition, never by an Executor."""

    def __init__(self, root: str | Path) -> None:
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("SANDBOX_WORKSPACE_ROOT_MUST_BE_ABSOLUTE")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ValueError("SANDBOX_WORKSPACE_ROOT_UNSAFE")
        self._root = path.resolve(strict=True)

    async def stage_code(
        self, *, action_id: str, attempt_id: str,
        content: bytes, expected_sha256: str,
    ) -> None:
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_sha256:
            raise ValueError("SANDBOX_CODE_HASH_CONFLICT")
        target = self._input_root(action_id, attempt_id) / digest
        await _atomic_content_write(target, content)

    async def stage_artifact(
        self, *, action_id: str, attempt_id: str, reference: str,
        content: bytes, expected_sha256: str,
    ) -> None:
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_sha256:
            raise ValueError("SANDBOX_INPUT_HASH_CONFLICT")
        identity = hashlib.sha256(reference.encode()).hexdigest()
        target = self._input_root(
            action_id, attempt_id,
        ) / "artifacts" / identity
        await _atomic_content_write(target, content)

    def read_code(
        self, *, action_id: str, attempt_id: str, expected_sha256: str,
    ) -> bytes:
        target = self._input_root(action_id, attempt_id) / expected_sha256
        content = _safe_read(target)
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ValueError("SANDBOX_CODE_HASH_CONFLICT")
        return content

    def prepare_job(self, job_id: str) -> tuple[Path, Path]:
        job_root = self._root / "jobs" / _uuid(job_id)
        input_dir, output_dir = job_root / "input", job_root / "output"
        input_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return input_dir, output_dir

    def materialize_inputs(
        self, *, action_id: str, attempt_id: str,
        manifest: dict, input_dir: Path,
    ) -> None:
        items = manifest.get("items", [])
        for index, item in enumerate(items):
            reference = str(item["artifact_ref"])
            identity = hashlib.sha256(reference.encode()).hexdigest()
            source = (
                self._input_root(action_id, attempt_id)
                / "artifacts" / identity
            )
            content = _safe_read(source)
            if hashlib.sha256(content).hexdigest() != item["content_sha256"]:
                raise ValueError("SANDBOX_INPUT_HASH_CONFLICT")
            _write(input_dir / f"artifact-{index:03d}", content)

    async def materialize_outputs(
        self, *, job_id: str, output_dir: Path,
        max_bytes: int, max_files: int,
    ) -> tuple[WorkspaceObject, ...]:
        files = _validated_files(output_dir, max_bytes, max_files)
        objects: list[WorkspaceObject] = []
        for source in files:
            content = _safe_read(source)
            digest = hashlib.sha256(content).hexdigest()
            target = self._root / "objects" / "sha256" / digest
            await _atomic_content_write(target, content)
            objects.append(WorkspaceObject(
                reference=f"workspace-object:sha256:{digest}",
                content_sha256=digest,
                size_bytes=len(content),
                media_type="application/octet-stream",
            ))
        return tuple(objects)

    def quarantine(
        self, job_id: str, output_dir: Path, *,
        max_bytes: int, max_files: int,
    ) -> tuple[dict, ...]:
        files = _validated_files(output_dir, max_bytes, max_files)
        manifests = []
        for source in files:
            content = _safe_read(source)
            manifests.append({
                "temporary_object_ref": f"sandbox-temp:{_uuid(job_id)}",
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "media_type": "application/octet-stream",
            })
        quarantine = self._root / "quarantine" / _uuid(job_id)
        quarantine.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if output_dir.exists() and not quarantine.exists():
            os.replace(output_dir, quarantine)
        return tuple(manifests)

    def cleanup_job(self, job_id: str) -> bool:
        for category in ("jobs", "quarantine"):
            target = self._root / category / _uuid(job_id)
            if target.exists():
                shutil.rmtree(target)
        return not any(
            (self._root / category / _uuid(job_id)).exists()
            for category in ("jobs", "quarantine")
        )

    async def write_terminal_checkpoint(
        self, *, job_id: str, checkpoint: dict[str, object],
    ) -> None:
        encoded = json.dumps(
            checkpoint, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()
        await _atomic_content_write(self._checkpoint_path(job_id), encoded)

    def read_terminal_checkpoint(
        self, job_id: str,
    ) -> dict[str, object] | None:
        path = self._checkpoint_path(job_id)
        if not path.exists():
            return None
        value = json.loads(_safe_read(path))
        if not isinstance(value, dict):
            raise ValueError("SANDBOX_CHECKPOINT_INVALID")
        return value

    def cleanup_terminal_checkpoint(self, job_id: str) -> bool:
        path = self._checkpoint_path(job_id)
        if path.exists():
            path.unlink()
        return not path.exists()

    def cleanup_staged_attempt(
        self, *, action_id: str, attempt_id: str,
    ) -> bool:
        target = self._input_root(action_id, attempt_id)
        if target.exists():
            shutil.rmtree(target)
        return not target.exists()

    def _input_root(self, action_id: str, attempt_id: str) -> Path:
        return (
            self._root / "inputs" / _uuid(action_id) / _uuid(attempt_id)
        )

    def _checkpoint_path(self, job_id: str) -> Path:
        return self._root / "checkpoints" / f"{_uuid(job_id)}.json"

    def quarantined_job_ids(self) -> tuple[str, ...]:
        root = self._root / "quarantine"
        if not root.exists():
            return ()
        identities: list[str] = []
        for entry in root.iterdir():
            if not entry.is_dir() or entry.is_symlink():
                continue
            try:
                identities.append(_uuid(entry.name))
            except ValueError:
                continue
        return tuple(sorted(identities))


async def _atomic_content_write(target: Path, content: bytes) -> None:
    import asyncio

    await asyncio.to_thread(_write, target, content)


def _write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        if _safe_read(target) != content:
            raise ValueError("SANDBOX_IMMUTABLE_OBJECT_CONFLICT")
        return
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_read(path: Path) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("SANDBOX_FILE_IDENTITY_UNSAFE")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def _validated_files(root: Path, max_bytes: int, max_files: int) -> list[Path]:
    resolved = root.resolve()
    files: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("SANDBOX_OUTPUT_LINK_REJECTED")
        if not stat.S_ISREG(metadata.st_mode):
            continue
        if not path.resolve().is_relative_to(resolved):
            raise ValueError("SANDBOX_OUTPUT_ESCAPE_REJECTED")
        files.append(path)
        total += metadata.st_size
        if len(files) > max_files or total > max_bytes:
            raise ValueError("SANDBOX_OUTPUT_LIMIT_EXCEEDED")
    return files


def _uuid(value: str) -> str:
    return str(UUID(value))
