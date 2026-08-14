from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / (
    "migrations/228_08f2_agent_runtime_media_atomic_image_batch_ownership.sql"
)
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08f2_agent_runtime_media_atomic_image_batch_ownership_rollback.sql"
)


def test_atomic_image_batch_ownership_is_preflighted_and_narrow() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE FUNCTION submit_agent_runtime_media_image_batch_v2" in sql
    assert "_agent_runtime_media_image_batch_ownership_v1" in sql
    assert "FOR UPDATE OF task" in sql
    assert "'ownership','none'" in sql
    assert "'ownership','full'" in sql
    assert "'outcome','partial_ownership'" in sql
    assert "PARTIAL_OWNERSHIP_RECONCILE_REQUIRED" in sql
    assert "SET search_path=pg_catalog,public" in sql
    assert "REVOKE ALL ON FUNCTION submit_agent_runtime_media_image_batch_v1" in sql
    assert "TO everydayai_runtime,everydayai_wecom_runtime" in sql
    assert "DROP TABLE" not in sql


def test_atomic_image_batch_ownership_has_exact_rollback() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MEDIA_IMAGE_BATCH_ROLLBACK_ORDER_INVALID" in rollback
    assert "AGENT_RUNTIME_MEDIA_IMAGE_BATCH_ROLLBACK_PARTIAL_OWNERSHIP" in rollback
    assert "agent_runtime_prepared_media_action_bindings" in rollback
    assert "task.delivery_context" in rollback
    assert "agent_session_commands" in rollback
    assert "command.payload->>'task_id'=task.id::TEXT" in rollback
    assert "evidence_count<>total_count" in rollback
    assert "valid_count<>total_count" in rollback
    assert "DROP FUNCTION submit_agent_runtime_media_image_batch_v2" in rollback
    assert "DROP FUNCTION _agent_runtime_media_image_batch_ownership_v1" in rollback
    assert "GRANT EXECUTE ON FUNCTION submit_agent_runtime_media_image_batch_v1" in rollback
    assert rollback.index("ROLLBACK_PARTIAL_OWNERSHIP") < rollback.index(
        "GRANT EXECUTE ON FUNCTION submit_agent_runtime_media_image_batch_v1"
    )


def test_atomic_image_batch_ownership_identity_is_unique() -> None:
    assert list((ROOT / "migrations").glob("228_08f2_*.sql")) == [MIGRATION]
    assert list((ROOT / "migrations/rollback").glob("228_08f2_*.sql")) == [ROLLBACK]
