#!/usr/bin/env python3
"""Plan or atomically apply migration 161 legacy configuration import."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import psycopg


BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:  # pragma: no cover - direct script only
    sys.path.insert(0, str(BACKEND_ROOT))

from services.configuration.envelope import LocalKEKProvider
from services.configuration.legacy_import import (
    LegacyImportPlanError,
    LegacyImportPlanner,
)
from services.configuration.legacy_import_executor import (
    LegacyImportExecutionError,
    apply_legacy_import,
    required_confirmation,
)
from services.configuration.legacy_import_source import LegacyImportSourceError
from services.configuration.legacy_import_source_executor import (
    read_legacy_import_snapshot,
)
from services.configuration.material_service import SecretMaterialService


SOURCE_DATABASE_ENV = "LEGACY_CONFIG_SOURCE_DATABASE_URL"
MIGRATOR_DATABASE_ENV = "MIGRATION_DATABASE_URL"
LEGACY_KEY_ENV = "ORG_CONFIG_ENCRYPT_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--import-id")
    parser.add_argument("--confirm")
    return parser


def _read_snapshot(source_database_url: str):
    with psycopg.connect(source_database_url) as connection:
        return read_legacy_import_snapshot(
            connection,
            global_encrypt_key=os.getenv(LEGACY_KEY_ENV),
        )


def _build_plan(snapshot, import_id: str):
    material_service = SecretMaterialService(
        LocalKEKProvider.from_environment()
    )
    return LegacyImportPlanner(material_service).build(
        import_id=import_id,
        organizations=snapshot.organizations,
        preflight_reports=snapshot.preflight_reports,
    )


def _summary(plan, mode: str) -> dict[str, object]:
    return {
        "config_keys": list(plan.config_keys),
        "import_id": plan.import_id,
        "item_count": plan.item_count,
        "mode": mode,
        "org_count": plan.org_count,
    }


def _preflight_summary(snapshot) -> dict[str, object]:
    organizations = []
    for org_id, report in sorted(snapshot.preflight_reports.items()):
        counts: dict[str, int] = {}
        for item in report.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        organizations.append({
            "can_migrate": report.can_migrate,
            "org_id": org_id,
            "status_counts": counts,
            "unknown_keys": list(report.unknown_keys),
        })
    return {
        "can_migrate": all(
            item["can_migrate"] for item in organizations
        ),
        "organizations": organizations,
    }


def main() -> int:
    args = _parser().parse_args()
    source_database_url = os.getenv(SOURCE_DATABASE_ENV)
    if not source_database_url:
        print(f"{SOURCE_DATABASE_ENV} is required", file=sys.stderr)
        return 2
    if args.apply and not args.import_id:
        print("--import-id is required with --apply", file=sys.stderr)
        return 2
    import_id = args.import_id or str(uuid4())
    try:
        snapshot = _read_snapshot(source_database_url)
        preflight = _preflight_summary(snapshot)
        if not preflight["can_migrate"]:
            print(json.dumps(preflight, sort_keys=True))
            return 1
        plan = _build_plan(snapshot, import_id)
        if not args.apply:
            print(json.dumps(_summary(plan, "dry-run"), sort_keys=True))
            print(
                f"apply confirmation: {required_confirmation(plan.import_id)}"
            )
            return 0
        migrator_database_url = os.getenv(MIGRATOR_DATABASE_ENV)
        if not migrator_database_url:
            print(f"{MIGRATOR_DATABASE_ENV} is required", file=sys.stderr)
            return 2
        if source_database_url == migrator_database_url:
            print(
                "source and migrator database URLs must differ",
                file=sys.stderr,
            )
            return 2
        with psycopg.connect(migrator_database_url) as connection:
            result = apply_legacy_import(
                connection,
                plan,
                confirmation=args.confirm or "",
            )
        output = {
            **_summary(plan, "apply"),
            "database_result": dict(result),
        }
        print(json.dumps(output, sort_keys=True))
        return 0
    except (
        LegacyImportExecutionError,
        LegacyImportPlanError,
        LegacyImportSourceError,
        ValueError,
        psycopg.Error,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
