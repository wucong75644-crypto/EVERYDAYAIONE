#!/usr/bin/env python3
"""Read the loopback-only Unix health contract."""

from __future__ import annotations

import json
import socket
import sys


def main(path: str, expected_role: str) -> int:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    try:
        client.connect(path)
        client.sendall(b"health\n")
        payload = json.loads(client.makefile("rb").readline())
    finally:
        client.close()
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return (
        0 if payload.get("role") == expected_role
        and payload.get("ready") is True
        and payload.get("draining") is False else 1
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: runtime-healthcheck.py SOCKET EXPECTED_ROLE")
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
