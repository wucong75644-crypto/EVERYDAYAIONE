#!/usr/bin/env python3
"""Transactional PostgreSQL migration runner with immutable checksums."""

from __future__ import annotations

import argparse
import hashlib
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
) -> list[Migration]:
    """Reject checksum drift, failed history, and unknown ledger identities."""
    discovered = {migration.identity: migration for migration in migrations}
    unknown = sorted(set(rows) - set(discovered))
    if unknown:
        raise MigrationError(f"ledger identities missing from repository: {unknown}")

    for identity, row in rows.items():
        migration = discovered[identity]
        if row["checksum_sha256"] != migration.checksum:
            raise MigrationError(f"checksum drift: {identity}")
        if row["status"] != "applied":
            raise MigrationError(f"failed migration requires reconciliation: {identity}")
    return [migration for migration in migrations if migration.identity not in rows]


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
    parser.add_argument("command", choices=("check", "plan", "apply", "baseline"))
    parser.add_argument("--through")
    parser.add_argument(
        "--acknowledge-existing-schema",
        action="store_true",
        help="required for baseline; confirms an external schema audit",
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
            pending = run(
                connection,
                args.command,
                args.applied_by,
                through=args.through,
            )
        for identity in pending:
            print(identity)
        return 0
    except (MigrationError, psycopg.Error) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
