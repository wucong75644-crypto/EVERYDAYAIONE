#!/usr/bin/env python3
"""Create a secret-free adoption report from an exported scheduled_tasks JSON list.

This command never connects to PostgreSQL and never mutates task data. The
input may contain full task rows, but the report only emits IDs, classifications
and hashes of task semantics/delivery targets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from services.agent.runtime.scheduled_adoption import build_adoption_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JSON array of scheduled task rows")
    parser.add_argument(
        "--profiles",
        type=Path,
        help="Optional JSON array of task IDs already bound to Runtime profiles",
    )
    args = parser.parse_args(argv)
    tasks = _load_json(args.input)
    profiles = _load_json(args.profiles) if args.profiles else []
    if not isinstance(tasks, list) or not all(isinstance(item, dict) for item in tasks):
        raise SystemExit("input must be a JSON array of objects")
    if not isinstance(profiles, list):
        raise SystemExit("profiles must be a JSON array")
    json.dump(
        build_adoption_report(tasks, profile_task_ids=(str(item) for item in profiles)),
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())

