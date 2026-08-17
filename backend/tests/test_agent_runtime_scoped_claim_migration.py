from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_NAME = "230_11_agent_runtime_scoped_claim_compatibility.sql"
ROLLBACK_NAME = (
    "230_11_agent_runtime_scoped_claim_compatibility_rollback.sql"
)
SQL = (ROOT / "migrations" / MIGRATION_NAME).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations" / "rollback" / ROLLBACK_NAME
).read_text(encoding="utf-8")


def test_migration_has_exact_rollback() -> None:
    discovered = {
        migration.identity: migration
        for migration in discover_migrations(ROOT / "migrations")
    }

    assert discovered[MIGRATION_NAME].rollback_identity == ROLLBACK_NAME


def test_runtime_claim_is_scoped_without_changing_default_claims() -> None:
    assert "set_config('app.agent_runtime_claim_scope','tenant',TRUE)" in SQL
    assert "action.org_id IS NOT NULL" in SQL
    assert "current_setting('app.agent_runtime_claim_scope', TRUE)" in SQL
    assert "current_setting('app.agent_runtime_claim_scope', TRUE)" not in ROLLBACK
    assert "set_config('app.agent_runtime_claim_scope','tenant',TRUE)" not in ROLLBACK
