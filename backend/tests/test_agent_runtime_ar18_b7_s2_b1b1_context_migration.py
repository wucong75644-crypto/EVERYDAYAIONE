from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_33_agent_runtime_scheduled_finalization_context.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_33_agent_runtime_scheduled_finalization_context_rollback.sql").read_text()


def test_context_rpc_is_worker_only_and_side_effect_free() -> None:
    assert "STABLE SECURITY DEFINER SET search_path=pg_catalog,public" in SQL
    assert "session_user<>'everydayai_agent_runtime_worker'" in SQL
    assert "current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'agent_runtime'" in SQL
    assert "GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1" in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "prompt" not in SQL
    assert "push_target" not in SQL
    assert "last_result" not in SQL
    assert "claim_token'," not in SQL
    assert not any(statement in SQL for statement in ("UPDATE ", "INSERT INTO ", "DELETE FROM "))


def test_context_rollback_only_removes_the_new_rpc() -> None:
    assert "REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_finalization_context_v1" in ROLLBACK
    assert "DROP FUNCTION read_agent_runtime_scheduled_finalization_context_v1(UUID,UUID)" in ROLLBACK
    assert "DROP TABLE" not in ROLLBACK
    assert "DELETE FROM" not in ROLLBACK
