from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_10_agent_runtime_operations_center.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_10_agent_runtime_operations_center_rollback.sql").read_text()


def test_operations_center_is_additive_and_failure_closed() -> None:
    assert "227_02" not in SQL and "227_09" not in SQL
    assert "ENABLE ROW LEVEL SECURITY" in SQL
    assert "FORCE ROW LEVEL SECURITY" in SQL
    assert "SET search_path = pg_catalog, public" in SQL
    assert "list_agent_runtime_provider_operations" in SQL
    assert "request_agent_runtime_provider_operation" in SQL
    assert "claim_agent_runtime_provider_operation" in SQL
    assert "resubmit" not in SQL.lower()
    assert "RUNTIME_PROVIDER_OPERATION_IMMUTABLE" in SQL
    assert "AR_17_4_ROLLBACK_BLOCKED_OPERATION_INTENTS" in ROLLBACK
