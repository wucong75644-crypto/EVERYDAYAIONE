from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_33_agent_runtime_scheduled_finalization_context.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_33_agent_runtime_scheduled_finalization_context_rollback.sql").read_text()


def test_context_rpc_is_worker_only_and_side_effect_free() -> None:
    context_sql = SQL.split("CREATE FUNCTION read_agent_runtime_scheduled_finalization_context_v1", 1)[1]
    context_sql = context_sql.split("CREATE FUNCTION apply_agent_runtime_scheduled_finalization_v2", 1)[0]
    assert "STABLE SECURITY DEFINER SET search_path=pg_catalog,public" in SQL
    assert "session_user<>'everydayai_agent_runtime_worker'" in SQL
    assert "current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'agent_runtime'" in SQL
    assert "GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1" in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "prompt" not in context_sql
    assert "push_target" not in context_sql
    assert "last_result" not in context_sql
    assert "claim_token'," not in context_sql
    assert not any(statement in context_sql for statement in ("UPDATE ", "INSERT INTO ", "DELETE FROM "))


def test_apply_v2_is_local_only_and_does_not_consult_current_kill_state() -> None:
    apply_sql = SQL.split("CREATE FUNCTION apply_agent_runtime_scheduled_finalization_v2", 1)[1]
    assert "agent_runtime_tenant_gate_controls" not in apply_sql
    assert "tenant_kill_epoch<0" in apply_sql
    assert "provider_kill_epoch<0" in apply_sql
    assert "capability_kill_epoch<0" in apply_sql
    assert "profile_state_version" in apply_sql
    assert "provider_revision" in apply_sql and "capability_revision" in apply_sql
    assert "UPDATE scheduled_task_runs" in apply_sql
    assert "UPDATE scheduled_tasks" in apply_sql
    assert "UPDATE agent_runtime_scheduled_run_bindings" in apply_sql
    assert "UPDATE agent_runtime_scheduled_finalization_intents" in apply_sql
    for forbidden in ("users SET", "provider_submission", "http", "redis", "push_target"):
        assert forbidden not in apply_sql.lower()


def test_context_rollback_only_removes_the_new_rpc() -> None:
    assert "REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1" in ROLLBACK
    assert "DROP FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID)" in ROLLBACK
    assert "DROP FUNCTION apply_agent_runtime_scheduled_finalization_v2" in ROLLBACK
    assert "DROP TABLE" not in ROLLBACK
    assert "DELETE FROM" not in ROLLBACK
