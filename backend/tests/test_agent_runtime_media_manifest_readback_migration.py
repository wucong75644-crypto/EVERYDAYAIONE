from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql"
ROLLBACK = ROOT / "migrations/rollback/228_05_agent_runtime_media_manifest_readback_rollback.sql"


def test_manifest_readback_migration_is_additive_and_worker_scoped():
    sql = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()

    assert "CREATE FUNCTION _agent_runtime_media_resolved_images_v1" in sql
    assert "CREATE FUNCTION prepare_agent_runtime_media_dispatch_v1" in sql
    assert "CREATE FUNCTION read_agent_runtime_media_provider_request_v1" in sql
    assert "user_assets" in sql
    assert "conversation_attachment_refs" in sql
    assert "AGENT_RUNTIME_PREPARED_VIDEO_UNAVAILABLE" in sql
    assert "COALESCE((task.delivery_context->>'runtime')::BOOLEAN,FALSE) IS FALSE" in sql
    assert "agent_runtime_prepared_media_action_bindings binding" in sql
    assert "INSERT INTO agent_runtime_media_action_bindings" in sql
    assert "'slot_status','pending'" in sql
    assert "TO everydayai_agent_runtime_worker" in sql
    assert "DROP TABLE" not in sql
    assert "DROP TABLE agent_runtime_prepared_media_action_bindings" in rollback
    assert "AGENT_RUNTIME_228_05_PREPARED_BINDINGS_IN_USE" in rollback
