"""Fail-fast health-socket readiness for the hosted daemon harness."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from subprocess import Popen


async def wait_ready(
    process: Popen, socket_path: str, daemon_log: Path, timeout: float = 10,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = daemon_log.read_text(
                encoding="utf-8", errors="replace",
            )[-4000:]
            raise RuntimeError(
                f"SANDBOX_DAEMON_START_FAILED:{process.returncode}\n{tail}"
            )
        if Path(socket_path).exists():
            reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.write(b"health\n")
            await writer.drain()
            payload = await reader.readline()
            writer.close()
            await writer.wait_closed()
            if json.loads(payload).get("ready"):
                return
        await asyncio.sleep(0.1)
    raise RuntimeError("SANDBOX_DAEMON_HEALTH_TIMEOUT")
