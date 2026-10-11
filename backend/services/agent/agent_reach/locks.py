"""Same-host, cross-worker account serialization with cancellable acquisition."""
import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile


@asynccontextmanager
async def account_lock(identity):
    root = Path(tempfile.gettempdir()) / f'everydayai-reach-locks-{os.getuid()}'
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise PermissionError('Agent Reach锁目录权限无效')
    filename = hashlib.sha256(identity.encode()).hexdigest()
    fd = os.open(root / filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        yield
    finally:
        os.close(fd)
