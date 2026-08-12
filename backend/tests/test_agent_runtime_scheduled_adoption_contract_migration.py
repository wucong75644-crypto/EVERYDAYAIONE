from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_60_agent_runtime_scheduled_adoption.sql"
ROLLBACK = ROOT / "migrations/rollback/227_60_agent_runtime_scheduled_adoption_rollback.sql"


def test_adoption_migration_has_separate_provenance_and_profile_contracts() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE TABLE agent_runtime_scheduled_adoption_provenance" in sql
    assert "CREATE TABLE agent_runtime_scheduled_adoption_profiles" in sql
    assert "ordinary_execution_history_created',FALSE" in sql
    assert "CREATE TABLE agent_runs" not in sql
    assert "INSERT INTO agent_actions" not in sql
    assert "INSERT INTO agent_action_attempts" not in sql
    assert "SCHEDULED_ADOPTION_FACT_SET_INCOMPLETE" in sql
    assert "SCHEDULED_ADOPTION_TASK_IN_FLIGHT" in sql
    assert "SCHEDULED_ADOPTION_FACT_INCOMPLETE" in sql
    assert "agent_runtime_scheduled_execution_profiles" in sql


def test_adoption_migration_is_immutable_and_rollback_is_guarded() -> None:
    sql = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "AGENT_RUNTIME_SCHEDULED_ADOPTION_FACT_IMMUTABLE" in sql
    assert "SCHEDULED_ADOPTION_ROLLBACK_SIDE_EFFECTS_EXIST" in sql
    assert "AGENT_RUNTIME_SCHEDULED_ADOPTION_FACTS_EXIST" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_profiles" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_provenance" in rollback
