"""Migration Runner real PostgreSQL transaction boundary."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest

from scripts import migration_runner
from scripts.migration_runner import Migration, MigrationError, run


pytestmark = pytest.mark.external


def _database_url() -> str:
    url = os.getenv("MIGRATION_RUNNER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("MIGRATION_RUNNER_TEST_DATABASE_URL_REQUIRED")
    return url


def _migration(path: Path) -> Migration:
    return Migration(
        path.name,
        path,
        "a" * 64,
        f"{path.stem}_rollback.sql",
    )


def test_successful_migrations_survive_later_failure(
    tmp_path: Path,
) -> None:
    schema = f"runner_{uuid4().hex}"
    first_path = tmp_path / "901_first.sql"
    second_path = tmp_path / "902_second.sql"
    failed_path = tmp_path / "903_failed.sql"
    first_path.write_text(
        "CREATE TABLE runner_value(value INTEGER NOT NULL);",
        encoding="utf-8",
    )
    second_path.write_text(
        "INSERT INTO runner_value(value) VALUES (42);",
        encoding="utf-8",
    )
    failed_path.write_text(
        "ALTER TABLE missing_runner_table ADD COLUMN value INTEGER;",
        encoding="utf-8",
    )
    bootstrap = Migration(
        migration_runner.LEDGER_IDENTITY,
        tmp_path / migration_runner.LEDGER_IDENTITY,
        "b" * 64,
        None,
    )
    migrations = [
        bootstrap,
        _migration(first_path),
        _migration(second_path),
        _migration(failed_path),
    ]

    with psycopg.connect(_database_url(), autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with psycopg.connect(_database_url()) as connection:
            connection.execute(f'SET search_path TO "{schema}"')
            connection.execute(
                """
                CREATE TABLE schema_migration_ledger(
                    identity TEXT PRIMARY KEY,
                    checksum_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    execution_kind TEXT NOT NULL,
                    rollback_identity TEXT,
                    started_at TIMESTAMPTZ DEFAULT NOW(),
                    finished_at TIMESTAMPTZ,
                    applied_by TEXT,
                    error_summary TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO schema_migration_ledger(
                    identity, checksum_sha256, status, execution_kind
                ) VALUES (%s, %s, 'applied', 'baseline')
                """,
                (migration_runner.LEDGER_IDENTITY, bootstrap.checksum),
            )
            connection.commit()
            with (
                patch.object(
                    migration_runner,
                    "discover_migrations",
                    return_value=migrations,
                ),
                patch.object(migration_runner, "_bootstrap"),
                pytest.raises(MigrationError, match="903_failed.sql"),
            ):
                run(connection, "apply", "postgres-test")

        with psycopg.connect(_database_url()) as verification:
            verification.execute(f'SET search_path TO "{schema}"')
            assert verification.execute(
                "SELECT value FROM runner_value"
            ).fetchone() == (42,)
            rows = verification.execute(
                "SELECT identity, status FROM schema_migration_ledger "
                "WHERE identity LIKE '9%' ORDER BY identity"
            ).fetchall()
            assert rows == [
                ("901_first.sql", "applied"),
                ("902_second.sql", "applied"),
                ("903_failed.sql", "failed"),
            ]
    finally:
        with psycopg.connect(_database_url(), autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
