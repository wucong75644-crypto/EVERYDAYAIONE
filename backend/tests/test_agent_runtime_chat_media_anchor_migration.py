from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/230_13_agent_runtime_chat_media_anchor.sql"
ROLLBACK = ROOT / "migrations/rollback/230_13_agent_runtime_chat_media_anchor_rollback.sql"
COMPATIBILITY_MIGRATION = ROOT / "migrations/230_14_agent_runtime_media_direct_anchor_compatibility.sql"
COMPATIBILITY_ROLLBACK = ROOT / "migrations/rollback/230_14_agent_runtime_media_direct_anchor_compatibility_rollback.sql"


def test_chat_media_anchor_migration_carries_all_runtime_media_anchors():
    sql = MIGRATION.read_text()

    assert "CREATE OR REPLACE FUNCTION submit_agent_runtime_chat_action_v1" in sql
    assert "input_message_id UUID" in sql
    assert "AGENT_RUNTIME_CHAT_MEDIA_INPUT_ANCHOR_INVALID" in sql
    assert "'input_message_id', input_message_id" in sql
    assert "'output_message_id', p_message_id" in sql
    assert "p_catalog_revision, jsonb_build_object" in sql
    assert "p_policy_snapshot || jsonb_build_object" in sql
    assert "DROP FUNCTION" not in sql


def test_chat_media_anchor_rollback_restores_previous_chat_contract():
    rollback = ROLLBACK.read_text()

    assert "CREATE OR REPLACE FUNCTION submit_agent_runtime_chat_action_v1" in rollback
    assert "'input_message_id', input_message_id" not in rollback
    assert "p_catalog_revision, jsonb_build_object" in rollback


def test_direct_media_compatibility_migration_patches_applied_function_only():
    sql = COMPATIBILITY_MIGRATION.read_text()
    rollback = COMPATIBILITY_ROLLBACK.read_text()

    assert "pg_get_functiondef(target)" in sql
    assert "AGENT_RUNTIME_CHAT_MEDIA_ANCHOR_FUNCTION_DRIFT" in sql
    assert "p_context_receipt->>'source', '') <> 'media_ingress'" in sql
    assert "p_policy_snapshot->>'source', '') <> 'media_ingress'" in sql
    assert "pg_get_functiondef(target)" in rollback
    assert "AGENT_RUNTIME_CHAT_MEDIA_ANCHOR_FUNCTION_DRIFT" in rollback
