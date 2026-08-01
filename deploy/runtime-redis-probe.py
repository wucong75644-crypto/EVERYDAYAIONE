#!/usr/bin/env python3
"""Read/write/delete the isolated Tool Confirmation V3 probe key."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


REPO_BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(REPO_BACKEND))


async def main() -> int:
    from services.tool_confirmation.capability_probe import (
        probe_tool_confirmation_redis,
    )

    result = await probe_tool_confirmation_redis()
    print(json.dumps(
        {"ready": result.ready, "code": result.code},
        separators=(",", ":"),
    ))
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
