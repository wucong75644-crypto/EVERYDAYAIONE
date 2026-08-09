from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_29_agent_runtime_scheduled_execution_owner.sql"
ROLLBACK = ROOT / "migrations/rollback/227_29_agent_runtime_scheduled_execution_owner_rollback.sql"


def test_owner_facts_are_additive_private_and_failure_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "agent_runtime_scheduled_execution_profiles" in sql
    assert "agent_runtime_scheduled_run_bindings" in sql
    assert sql.count("ENABLE ROW LEVEL SECURITY") == 2
    assert sql.count("FORCE ROW LEVEL SECURITY") == 2
    assert sql.count("SET search_path = pg_catalog, public") >= 9
    assert "session_user <> 'everydayai_agent_runtime_worker'" in sql
    assert "app.access_kind" in sql
    assert "TO everydayai_agent_runtime_worker" in sql
    assert "TO everydayai_worker" not in sql
    assert "tool->>'safety_level' IS DISTINCT FROM 'safe'" in sql
    assert "tool->>'side_effect' IS DISTINCT FROM 'none'" in sql
    assert "tool->>'authorization_requirement' IS DISTINCT FROM 'none'" in sql
    assert "runtime_submit_ingress" not in sql
    assert "INSERT INTO agent_session_commands" not in sql
    assert "INSERT INTO agent_runs" not in sql
    assert "production_ready" not in sql


def test_owner_facts_rollback_is_exact_and_guarded() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AR_18_B7_S2_A1_ROLLBACK_OWNER_FACTS_EXIST" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduled_run_bindings" in rollback
    assert "DROP TABLE IF EXISTS agent_runtime_scheduled_execution_profiles" in rollback
    assert "DROP TABLE IF EXISTS scheduled_tasks" not in rollback
    assert "GRANT" not in rollback
    assert "227_28" not in rollback
