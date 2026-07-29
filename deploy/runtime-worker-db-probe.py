#!/usr/bin/env python3
"""Verify the dedicated worker role and migration-223 control capability."""

from __future__ import annotations

import json
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


def main(role: str) -> int:
    from core.database import close_worker_db, get_worker_db
    from core.db_scope import (
        DatabaseAccessKind,
        DatabaseScope,
        ScopedDatabaseClient,
    )

    kind = {
        "agent_runtime": DatabaseAccessKind.AGENT_RUNTIME,
        "projection": DatabaseAccessKind.PROJECTION,
        "authorization": DatabaseAccessKind.AUTHORIZATION,
        "sandbox": DatabaseAccessKind.SANDBOX_WORKER,
    }.get(role)
    if kind is None:
        raise RuntimeError("AGENT_RUNTIME_PROCESS_ROLE_INVALID")
    database = ScopedDatabaseClient(
        get_worker_db(),
        DatabaseScope(
            actor_user_id=None,
            org_id=None,
            access_kind=kind,
            request_id=f"startup-probe:{role}",
        ),
    )
    try:
        response = database.rpc(
            "get_agent_runtime_worker_control",
            {"p_process_role": role},
        ).execute()
        data = response.data
        if not isinstance(data, dict) or "enabled" not in data:
            raise RuntimeError("AGENT_RUNTIME_CONTROL_PROBE_INVALID")
        print(json.dumps({
            "role": role,
            "database_ready": True,
            "gate_enabled": bool(data["enabled"]),
        }, separators=(",", ":")))
        return 0
    finally:
        close_worker_db()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: runtime-worker-db-probe.py ROLE")
    raise SystemExit(main(sys.argv[1]))
