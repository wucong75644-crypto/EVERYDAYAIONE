"""Shared-filesystem reader/writer coordination for workspace source files.

Locks cover a workspace, including directory moves. They are held only during
preparation or actual IO, never while waiting for user confirmation. Flock is
shared across processes on the production host; failures do not fall back
unlocked. Cross-host exclusion requires a mount with distributed flock support
(production NFS currently has local_lock=all, so it is a single-host contract).
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import tempfile
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import wraps
from dataclasses import dataclass
from pathlib import Path

_held = ContextVar("workspace_coordination", default=())


@dataclass
class _Lease:
    key: str
    write: bool
    active: bool = True


async def finish_file_io(fn, *args, **kwargs):
    """Do not release coordination while a cancelled worker still mutates files."""
    worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await drain_file_task(worker)
        raise


async def drain_file_task(task):
    """Repeated cancellation must not abandon a filesystem mutation/cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled():
        task.exception()  # observe failure; caller preserves its cancellation


async def receive_workspace_upload(upload, target, *, max_bytes, too_large):
    """Publish a complete upload without overwriting an existing source.

    Caller holds the workspace write lease through subsequent OSS publication.
    Partial bytes stay hidden; cancellation drains disk writes before cleanup.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
    stream = os.fdopen(fd, "wb")
    total = 0
    try:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise too_large(total)
            await finish_file_io(stream.write, chunk)
        await finish_file_io(stream.close)
        os.link(temporary, target)
        return total
    finally:
        if not stream.closed:
            await finish_file_io(stream.close)
        Path(temporary).unlink(missing_ok=True)


def _key(root):
    return str(Path(root).resolve())


def _open(root):
    resolved = Path(root).resolve()
    locks = resolved.parent / ".workspace-locks"
    locks.mkdir(parents=True, exist_ok=True)
    return os.open(locks / (hashlib.sha256(resolved.name.encode()).hexdigest() + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)


@asynccontextmanager
async def workspace_lock(root, *, write=False, check=lambda: None):
    key = _key(root)
    inherited = next((lease for lease in _held.get() if lease.key == key and lease.active), None)
    if inherited is not None:
        if write and not inherited.write:
            raise RuntimeError("Workspace lock upgrade is not allowed")
        check()
        yield
        return
    check()
    fd = _open(root)
    token = None
    lease = _Lease(key, write)
    try:
        while True:
            check()
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.025)
        check()
        token = _held.set((*_held.get(), lease))
        yield
    finally:
        if token is not None:
            lease.active = False
            _held.reset(token)
        os.close(fd)


@contextmanager
def workspace_lock_sync(root, *, write=True):
    """Sync writers run in worker threads; never wait on an async task's lock."""
    inherited = next((lease for lease in _held.get() if lease.key == _key(root) and lease.active), None)
    if inherited is not None:
        if write and not inherited.write:
            raise RuntimeError("Workspace lock upgrade is not allowed")
        yield
        return
    fd = _open(root)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


def coordinated_file_write(fn):
    @wraps(fn)
    async def wrapped(self, *args, **kwargs):
        async with workspace_lock(self.workspace_root, write=True):
            return await fn(self, *args, **kwargs)
    return wrapped
