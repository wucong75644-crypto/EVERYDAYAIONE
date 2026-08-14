from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_64_agent_runtime_wecom_owner_closure.sql"
ROLLBACK = ROOT / "migrations/rollback/227_64_agent_runtime_wecom_owner_closure_rollback.sql"


def test_runtime_required_wecom_owner_closure_is_fail_closed() -> None:
    sql = MIGRATION.read_text()
    assert "WECOM_RUNTIME_LEGACY_OWNER_DISABLED" in sql
    assert "NOT task.delivery_context @> '{\"runtime_required\": true}'::JSONB" in sql
    assert "ACTOR_RUNTIME_REQUIRED_TASK" in sql


def test_owner_closure_has_exact_restore_path() -> None:
    sql = ROLLBACK.read_text()
    assert "restore_prepared_task_to_legacy_actor" in sql
    assert "discover_generation_turn_candidates" in sql
    assert "_assert_actor_worker_task_scope" in sql
