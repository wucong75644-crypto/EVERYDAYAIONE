from pathlib import Path

import psycopg
import pytest

from scripts.migration_runner import discover_migrations
from tests.test_agent_runtime_ar17_postgres_external import database


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_01_z_agent_runtime_pgcrypto.sql"
ROLLBACK = ROOT / "migrations/rollback/227_01_z_agent_runtime_pgcrypto_rollback.sql"


def test_pgcrypto_prerequisite_precedes_catalog_seed() -> None:
    identities = [item.identity for item in discover_migrations()]
    assert identities.index(MIGRATION.name) < identities.index(
        "227_02_agent_runtime_production_catalog_seed.sql"
    )


def test_pgcrypto_migration_has_matching_rollback() -> None:
    migration_sql = MIGRATION.read_text(encoding="utf-8")
    rollback_sql = ROLLBACK.read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in migration_sql
    assert "DROP EXTENSION" not in rollback_sql.upper()
    assert "Intentional no-op" in rollback_sql
    assert "database platform" in rollback_sql


@pytest.mark.external
def test_pgcrypto_rollback_preserves_preexisting_extension(database: str) -> None:
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT extname FROM pg_extension WHERE extname = 'pgcrypto'"
        ).fetchone() == ("pgcrypto",)

        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))

        assert connection.execute(
            "SELECT digest('shared-capability', 'sha256') IS NOT NULL"
        ).fetchone() == (True,)
