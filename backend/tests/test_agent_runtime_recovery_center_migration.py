from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_11_agent_runtime_recovery_center.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_11_agent_runtime_recovery_center_rollback.sql").read_text()


def test_recovery_center_is_additive_and_failure_closed() -> None:
    assert "227_02" not in SQL and "227_10" not in SQL
    assert "agent_runtime_recovery_intents" in SQL
    assert "agent_runtime_recovery_audit" in SQL
    assert "ENABLE ROW LEVEL SECURITY" in SQL
    assert "FORCE ROW LEVEL SECURITY" in SQL
    assert SQL.count("SET search_path = pg_catalog, public") >= 5
    assert "list_agent_runtime_recovery_snapshot" in SQL
    assert "request_agent_runtime_recovery" in SQL
    assert "claim_agent_runtime_recovery" in SQL
    assert "resubmit" not in SQL.lower()
    assert "force complete" not in SQL.lower()
    assert "RUNTIME_RECOVERY_AUDIT_IMMUTABLE" in SQL
    assert "AR_17_5_ROLLBACK_BLOCKED_RECOVERY_FACTS" in ROLLBACK
