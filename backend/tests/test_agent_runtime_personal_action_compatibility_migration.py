from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_NAME = "230_12_agent_runtime_personal_action_compatibility.sql"
ROLLBACK_NAME = (
    "230_12_agent_runtime_personal_action_compatibility_rollback.sql"
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


def test_personal_actions_keep_cas_but_skip_only_tenant_fence() -> None:
    assert "IF x.org_id IS NULL THEN" in SQL
    assert "'outcome','allowed','tenant_kill_epoch',0" in SQL
    assert "IF x.org_id IS NULL THEN\n        RETURN;" in SQL
    assert "PERFORM set_config('app.agent_runtime_claim_scope','tenant',TRUE)" not in SQL
    assert "IF x.id IS NULL OR x.org_id IS NULL THEN" in ROLLBACK
    assert "PERFORM set_config('app.agent_runtime_claim_scope','tenant',TRUE)" in ROLLBACK
