from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/231_03_agent_runtime_media_cost_projection_repair.sql").read_text()


def test_cost_projection_repair_is_narrow_and_replayable() -> None:
    assert "CREATE TABLE agent_runtime_media_cost_projection_repairs" in SQL
    assert "ON CONFLICT (outbox_id) DO NOTHING" in SQL
    assert "previous_last_error_code" in SQL
    assert "status='dead'" in SQL
    assert "last_error_code='apply_invalidparametervalue'" in SQL
    assert "action.cost.reserve" in SQL
    assert "action.cost.settle" in SQL
    assert "status='pending'" in SQL
    assert "agent_runtime_media_action_bindings" in SQL
    assert "agent_runtime_prepared_media_action_bindings" in SQL
