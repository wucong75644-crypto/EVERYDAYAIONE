from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_01_z_agent_runtime_pgcrypto.sql"
ROLLBACK = ROOT / "migrations/rollback/227_01_z_agent_runtime_pgcrypto_rollback.sql"


def test_pgcrypto_prerequisite_precedes_catalog_seed() -> None:
    identities = [item.identity for item in discover_migrations()]
    assert identities.index(MIGRATION.name) < identities.index(
        "227_02_agent_runtime_production_catalog_seed.sql"
    )


def test_pgcrypto_migration_has_matching_rollback() -> None:
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in MIGRATION.read_text()
    assert "DROP EXTENSION IF EXISTS pgcrypto" in ROLLBACK.read_text()
