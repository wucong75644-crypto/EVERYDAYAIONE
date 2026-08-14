from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_08d_agent_runtime_media_atomic_image_batch.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_08d_agent_runtime_media_atomic_image_batch_rollback.sql"
)


def test_atomic_image_batch_migration_is_additive_and_fenced() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE FUNCTION submit_agent_runtime_media_image_batch_v1" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "_assert_agent_runtime_actor(FALSE)" in sql
    assert "FOR UPDATE OF task" in sql
    assert "EXCEPTION WHEN SQLSTATE 'PAB01'" in sql
    assert "submit_agent_runtime_media_action_v1" in sql
    assert "BETWEEN 1 AND 10" in sql
    assert "TO everydayai_runtime, everydayai_wecom_runtime" in sql
    assert "DROP TABLE" not in sql
    assert "ALTER TABLE" not in sql


def test_atomic_image_batch_rollback_is_exact() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "submit_agent_runtime_media_image_batch_v2" in rollback
    assert "_agent_runtime_media_image_batch_ownership_v1" in rollback
    assert "AGENT_RUNTIME_MEDIA_IMAGE_BATCH_ROLLBACK_08F2_REQUIRED" in rollback
    assert "ERRCODE='55000'" in rollback
    assert "DROP FUNCTION submit_agent_runtime_media_image_batch_v1" in rollback
    assert "DROP TABLE" not in rollback
    assert "submit_agent_runtime_media_action_v1" not in rollback


def test_atomic_image_batch_has_one_migration_identity() -> None:
    assert list((ROOT / "migrations").glob("228_08d_*.sql")) == [MIGRATION]
    assert list((ROOT / "migrations/rollback").glob("228_08d_*.sql")) == [ROLLBACK]
