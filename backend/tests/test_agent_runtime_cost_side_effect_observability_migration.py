from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_12_agent_runtime_cost_side_effect_observability.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_12_agent_runtime_cost_side_effect_observability_rollback.sql").read_text()


def test_cost_side_effect_observability_is_read_only_and_failure_closed() -> None:
    assert "227_02" not in SQL and "227_11" not in SQL
    assert "get_agent_runtime_cost_side_effect_snapshot" in SQL
    assert "credits_minor_integer" in SQL
    assert "production_ready',FALSE" in SQL
    assert "ENABLE ROW LEVEL SECURITY" not in SQL
    assert "INSERT INTO" not in SQL
    assert "UPDATE " not in SQL
    assert "DELETE " not in SQL
    assert "force settle" not in SQL.lower()
    assert "force refund" not in SQL.lower()
    assert "AR_17_6_ROLLBACK_BLOCKED_LEDGER_FACTS" in ROLLBACK
