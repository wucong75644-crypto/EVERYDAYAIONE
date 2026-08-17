from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/231_04_agent_runtime_media_cost_projection_checkpoint_repair.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/231_04_agent_runtime_media_cost_projection_checkpoint_repair_rollback.sql").read_text()


def test_checkpoint_repair_is_ordered_and_checkpoint_only() -> None:
    assert "agent_runtime_media_cost_projection_repairs" in SQL
    assert "earlier.status <> 'delivered'" in SQL
    assert "projection_action,action_id,message_id,task_id" in SQL
    assert "'checkpoint_only'" in SQL
    assert "through_sequence=item.sequence" in SQL
    assert "status='delivered'" in SQL


def test_checkpoint_repair_rollback_fails_closed() -> None:
    assert "NOT_REVERSIBLE" in ROLLBACK
