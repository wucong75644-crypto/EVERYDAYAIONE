from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_62_agent_runtime_scheduled_owner_convergence.sql"
ROLLBACK = ROOT / "migrations/rollback/227_62_agent_runtime_scheduled_owner_convergence_rollback.sql"


def test_owner_convergence_defaults_pending_and_requires_full_runtime_coverage() -> None:
    sql = MIGRATION.read_text()

    assert "state TEXT NOT NULL DEFAULT 'pending'" in sql
    assert "SCHEDULED_ADOPTION_RUNTIME_COVERAGE_INCOMPLETE" in sql
    assert "complete_agent_runtime_scheduled_adoption_v1" in sql
    assert "profileless_tasks <> 0" in sql
    assert "SCHEDULED_ADOPTION_RUNTIME_OWNER_INCOMPLETE" in sql
    assert "SCHEDULED_LEGACY_OWNER_DISABLED" in sql


def test_owner_convergence_rollback_is_blocked_after_completion() -> None:
    sql = ROLLBACK.read_text()

    assert "SCHEDULED_OWNER_CONVERGENCE_ALREADY_COMPLETED" in sql
    assert "DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_control" in sql
