from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_59_agent_runtime_scheduled_adoption_preflight.sql"
ROLLBACK = ROOT / "migrations/rollback/227_59_agent_runtime_scheduled_adoption_preflight_rollback.sql"


def test_preflight_is_read_only_owner_only_and_secret_free() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "read_agent_runtime_scheduled_adoption_plan_v1" in sql
    assert "CREATE TABLE" not in sql
    assert "INSERT INTO scheduled_tasks" not in sql
    assert "UPDATE scheduled_tasks" not in sql
    assert "DELETE FROM scheduled_tasks" not in sql
    assert "current_user <> 'everydayai_owner'" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "safe_to_adopt', FALSE" in sql
    assert "runtime_source_action_attempt_run_missing" in sql
    assert "prompt" in sql
    assert "push_target::TEXT" in sql
    assert "GRANT EXECUTE" not in sql


def test_rollback_only_removes_the_new_preflight_functions() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_adoption_plan_v1" in rollback
    assert "DROP FUNCTION IF EXISTS _agent_runtime_scheduled_adoption_target_shape" in rollback
    assert "DROP TABLE" not in rollback
    assert "scheduled_tasks" not in rollback
