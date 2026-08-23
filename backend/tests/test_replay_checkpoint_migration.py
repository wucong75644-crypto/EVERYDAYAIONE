from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "239_conversation_actor_production_contract_compat.sql"
ROLLBACK = MIGRATIONS / "rollback" / "239_conversation_actor_production_contract_compat_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_model_separates_replay_boundaries_from_delivery_progress():
    sql = _read(MIGRATION)

    assert "conversation_turn_checkpoints" in sql
    assert "save_generation_checkpoint" in sql
    assert "load_generation_checkpoint" in sql
    assert "conversation_replay_checkpoints" not in sql


def test_write_is_fenced_and_idempotent():
    sql = _read(MIGRATION)
    write = sql[sql.index("CREATE OR REPLACE FUNCTION public.save_generation_checkpoint") :]

    assert "v_task.execution_token IS DISTINCT FROM p_execution_token" in write
    assert "v_task.lease_expires_at IS NULL OR v_task.lease_expires_at <= NOW()" in write
    assert "ACTOR_CHECKPOINT_ARGUMENT_INVALID" in write
    assert "'outcome', 'saved'" in write


def test_read_is_user_scoped_and_rollback_preserves_facts():
    sql = _read(MIGRATION)
    rollback = _read(ROLLBACK)

    assert "p_execution_token" in sql
    assert "'outcome', 'empty'" in sql
    assert "non-destructive" in rollback
    assert "DROP TABLE" not in rollback
