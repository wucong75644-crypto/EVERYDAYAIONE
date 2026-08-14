from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / (
    "migrations/228_08f1_agent_runtime_media_prepared_image_batch_projection.sql"
)
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08f1_agent_runtime_media_prepared_image_batch_projection_rollback.sql"
)


def test_prepared_image_batch_projection_is_additive_and_scoped() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_prepared_image_batch_slots" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert "_agent_runtime_media_projection_scope_v1()" in sql
    assert "message.generation_params->>'type'<>'image'" in sql
    assert "task.delivery_context->>'channel'<>'web'" in sql
    assert "IF batch_size=1" in sql
    assert "BETWEEN 2 AND 10" in sql
    assert "ORDER BY batch_slot.slot_index" in sql
    assert "event.sequence>current_slot.slot_revision" in sql
    assert "current_slot.slot_status NOT IN ('completed','failed','cancelled')" in sql
    assert "WHEN terminal_count<batch_size THEN 'pending'" in sql
    assert "WHEN completed_count>0 THEN 'completed'" in sql
    assert "BEFORE INSERT ON agent_runtime_media_projection_results" in sql
    assert "DROP TABLE" not in sql


def test_prepared_image_batch_projection_has_exact_guarded_rollback() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PROJECTION_IN_USE" in rollback
    assert "DROP TRIGGER agent_runtime_prepared_image_batch_result_v1" in rollback
    assert "DROP FUNCTION _merge_agent_runtime_prepared_image_batch_projection_v1" in rollback
    assert "DROP TABLE agent_runtime_prepared_image_batch_slots" in rollback


def test_prepared_image_batch_projection_identity_is_unique() -> None:
    assert list((ROOT / "migrations").glob("228_08f1_*.sql")) == [MIGRATION]
    assert list((ROOT / "migrations/rollback").glob("228_08f1_*.sql")) == [ROLLBACK]
