#!/usr/bin/env python3
"""Transactional PostgreSQL migration runner with immutable checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"
RETIRED_LEDGER_PATH = MIGRATIONS_DIR / "retired_ledger_checksums.json"
LEDGER_IDENTITY = "000_migration_ledger.sql"
LOCK_KEY = "everydayai:schema-migrations:v1"
ERROR_LIMIT = 2000
PREFIX_RE = re.compile(r"^(\d+)_")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class MigrationError(RuntimeError):
    """Fail-closed migration validation or execution error."""


@dataclass(frozen=True)
class Migration:
    identity: str
    path: Path
    checksum: str
    rollback_identity: str | None


def _sort_key(path: Path) -> tuple[int, str]:
    match = PREFIX_RE.match(path.name)
    return (int(match.group(1)) if match else sys.maxsize, path.name)


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Discover SQL migrations using full filenames as immutable identities."""
    rollback_dir = directory / "rollback"
    paths = sorted(
        (
            path
            for pattern in ("*.sql", "*.py")
            for path in directory.glob(pattern)
            if path.is_file()
        ),
        key=_sort_key,
    )
    identities = [path.name for path in paths]
    if len(identities) != len(set(identities)):
        raise MigrationError("duplicate migration identity")

    migrations: list[Migration] = []
    for path in paths:
        rollback_matches = sorted(rollback_dir.glob(f"{path.stem}_rollback.sql"))
        rollback_identity = rollback_matches[0].name if rollback_matches else None
        migrations.append(Migration(
            identity=path.name,
            path=path,
            checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
            rollback_identity=rollback_identity,
        ))
    return migrations


def load_retired_ledger_checksums(
    path: Path = RETIRED_LEDGER_PATH,
) -> dict[str, str]:
    """Load applied migration identities whose executable SQL was retired."""
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise MigrationError("invalid retired migration ledger registry")
    migrations = payload.get("migrations")
    if not isinstance(migrations, dict):
        raise MigrationError("invalid retired migration ledger entries")
    checksums: dict[str, str] = {}
    for identity, checksum in migrations.items():
        if (
            not isinstance(identity, str)
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        ):
            raise MigrationError("invalid retired migration checksum")
        checksums[identity] = checksum
    return checksums


def _bootstrap(connection: Any, migration: Migration) -> None:
    with connection.cursor() as cursor:
        cursor.execute(migration.path.read_text(encoding="utf-8"))
    connection.commit()


def _ledger_rows(connection: Any) -> dict[str, dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT identity, checksum_sha256, status, execution_kind "
            "FROM schema_migration_ledger"
        )
        return {row["identity"]: row for row in cursor.fetchall()}


def validate_ledger(
    migrations: Sequence[Migration],
    rows: dict[str, dict[str, Any]],
    retired: dict[str, str] | None = None,
) -> list[Migration]:
    """Reject checksum drift, failed history, and unregistered ledger identities."""
    discovered = {migration.identity: migration for migration in migrations}
    retired = load_retired_ledger_checksums() if retired is None else retired
    overlap = sorted(set(discovered) & set(retired))
    if overlap:
        raise MigrationError(f"migration is both active and retired: {overlap}")
    known = set(discovered) | set(retired)
    unknown = sorted(set(rows) - known)
    if unknown:
        raise MigrationError(f"ledger identities missing from repository: {unknown}")

    for identity, row in rows.items():
        expected_checksum = (
            discovered[identity].checksum
            if identity in discovered
            else retired[identity]
        )
        if row["checksum_sha256"] != expected_checksum:
            raise MigrationError(f"checksum drift: {identity}")
        if row["status"] != "applied":
            raise MigrationError(f"failed migration requires reconciliation: {identity}")
    return [migration for migration in migrations if migration.identity not in rows]


def reconcile_failed(
    connection: Any,
    migrations: Sequence[Migration],
    identity: str,
    applied_by: str,
    acknowledge_transaction_rollback: bool,
) -> None:
    """Remove one failed ledger marker after an operator confirms rollback."""
    if not acknowledge_transaction_rollback:
        raise MigrationError(
            "reconcile-failed requires transaction rollback acknowledgement"
        )
    migration = next(
        (item for item in migrations if item.identity == identity), None
    )
    if migration is None:
        raise MigrationError(f"unknown migration identity: {identity}")
    rows = _ledger_rows(connection)
    row = rows.get(identity)
    if row is None or row["status"] != "failed":
        raise MigrationError(f"migration is not failed: {identity}")
    if row["checksum_sha256"] != migration.checksum:
        raise MigrationError(f"checksum drift: {identity}")
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM schema_migration_ledger
                WHERE identity = %s AND status = 'failed'
                  AND checksum_sha256 = %s
                RETURNING identity
                """,
                (identity, migration.checksum),
            )
            if cursor.fetchone() is None:
                raise MigrationError(f"failed marker changed: {identity}")
    del applied_by  # retained in the signature for audit-compatible callers


def _record_failure(
    connection: Any,
    migration: Migration,
    applied_by: str,
    error: Exception,
) -> None:
    summary = f"{type(error).__name__}: {error}"[:ERROR_LIMIT]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO schema_migration_ledger(
                identity, checksum_sha256, status, execution_kind,
                rollback_identity, applied_by, error_summary
            ) VALUES (%s, %s, 'failed', 'migration', %s, %s, %s)
            ON CONFLICT (identity) DO UPDATE SET
                status = 'failed', error_summary = EXCLUDED.error_summary,
                finished_at = NULL
            """,
            (
                migration.identity,
                migration.checksum,
                migration.rollback_identity,
                applied_by,
                summary,
            ),
        )
    connection.commit()


def apply_pending(
    connection: Any,
    pending: Sequence[Migration],
    applied_by: str,
) -> None:
    """Apply each migration and its ledger record in one transaction."""
    unsupported = [
        migration.identity
        for migration in pending
        if migration.path.suffix != ".sql"
    ]
    if unsupported:
        raise MigrationError(
            f"non-SQL migrations must be explicitly baselined: {unsupported}"
        )
    missing_rollbacks = [
        migration.identity
        for migration in pending
        if migration.identity != LEDGER_IDENTITY
        and migration.rollback_identity is None
    ]
    if missing_rollbacks:
        raise MigrationError(f"pending migrations missing rollback: {missing_rollbacks}")

    for migration in pending:
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(migration.path.read_text(encoding="utf-8"))
                    cursor.execute(
                        """
                        INSERT INTO schema_migration_ledger(
                            identity, checksum_sha256, status, execution_kind,
                            rollback_identity, finished_at, applied_by
                        ) VALUES (%s, %s, 'applied', 'migration', %s, NOW(), %s)
                        """,
                        (
                            migration.identity,
                            migration.checksum,
                            migration.rollback_identity,
                            applied_by,
                        ),
                    )
        except Exception as error:
            connection.rollback()
            _record_failure(connection, migration, applied_by, error)
            raise MigrationError(f"migration failed: {migration.identity}") from error


def baseline_through(
    connection: Any,
    migrations: Sequence[Migration],
    through: str,
    applied_by: str,
) -> None:
    """Record an explicitly audited legacy schema without replaying SQL."""
    identities = [migration.identity for migration in migrations]
    if through not in identities:
        raise MigrationError(f"unknown baseline boundary: {through}")
    if _ledger_rows(connection):
        raise MigrationError("baseline requires an empty ledger")

    selected = migrations[:identities.index(through) + 1]
    with connection.transaction():
        with connection.cursor() as cursor:
            for migration in selected:
                cursor.execute(
                    """
                    INSERT INTO schema_migration_ledger(
                        identity, checksum_sha256, status, execution_kind,
                        rollback_identity, finished_at, applied_by
                    ) VALUES (%s, %s, 'applied', 'baseline', %s, NOW(), %s)
                    """,
                    (
                        migration.identity,
                        migration.checksum,
                        migration.rollback_identity,
                        applied_by,
                    ),
                )


def run(
    connection: Any,
    command: str,
    applied_by: str,
    through: str | None = None,
    identity: str | None = None,
    acknowledge_transaction_rollback: bool = False,
) -> list[str]:
    """Run one migration command while holding the database advisory lock."""
    migrations = discover_migrations()
    bootstrap = next(
        item for item in migrations if item.identity == LEDGER_IDENTITY
    )
    _bootstrap(connection, bootstrap)

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_KEY,))
    connection.commit()
    try:
        if command == "baseline":
            if not through:
                raise MigrationError("baseline requires --through")
            baseline_through(connection, migrations, through, applied_by)
            return []

        if command == "reconcile-failed":
            if not identity:
                raise MigrationError("reconcile-failed requires --identity")
            reconcile_failed(
                connection,
                migrations,
                identity,
                applied_by,
                acknowledge_transaction_rollback,
            )
            return []

        pending = validate_ledger(migrations, _ledger_rows(connection))
        connection.commit()
        if command == "apply":
            apply_pending(connection, pending, applied_by)
        return [migration.identity for migration in pending]
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))
        connection.commit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("check", "plan", "apply", "baseline", "reconcile-failed"),
    )
    parser.add_argument("--through")
    parser.add_argument("--identity")
    parser.add_argument(
        "--acknowledge-existing-schema",
        action="store_true",
        help="required for baseline; confirms an external schema audit",
    )
    parser.add_argument(
        "--acknowledge-transaction-rollback",
        action="store_true",
        help="required for reconcile-failed; confirms failed SQL rolled back",
    )
    parser.add_argument("--applied-by", default=os.getenv("USER", "unknown"))
    return parser


def _database_url() -> str | None:
    return os.getenv("MIGRATION_DATABASE_URL")


def main() -> int:
    args = _parser().parse_args()
    if args.command == "baseline" and not args.acknowledge_existing_schema:
        print("baseline requires --acknowledge-existing-schema", file=sys.stderr)
        return 2

    database_url = _database_url()
    if not database_url:
        print("MIGRATION_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url) as connection:
            run_kwargs = {"through": args.through}
            if args.identity is not None:
                run_kwargs["identity"] = args.identity
            if args.acknowledge_transaction_rollback:
                run_kwargs["acknowledge_transaction_rollback"] = True
            pending = run(
                connection, args.command, args.applied_by, **run_kwargs
            )
        for identity in pending:
            print(identity)
        return 0
    except (MigrationError, psycopg.Error) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
