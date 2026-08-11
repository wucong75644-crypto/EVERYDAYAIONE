from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_56_agent_runtime_resource_manifest_facade.sql"
ROLLBACK = ROOT / "migrations/rollback/227_56_agent_runtime_resource_manifest_facade_rollback.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
UNDO = ROLLBACK.read_text(encoding="utf-8")


def test_resource_manifest_facade_is_additive_attempt_fenced_and_narrow() -> None:
    matches = [
        item for item in discover_migrations(ROOT / "migrations")
        if item.identity == MIGRATION.name
    ]
    assert [item.path for item in matches] == [MIGRATION]
    assert "CREATE TABLE" not in SQL
    assert "ALTER TABLE" not in SQL
    for contract in (
        "get_agent_runtime_resource_manifest_v1",
        "a.worker_id IS DISTINCT FROM btrim(p_worker_id)",
        "a.execution_token IS DISTINCT FROM p_execution_token",
        "a.request_hash IS DISTINCT FROM p_request_hash",
        "a.state_version<>p_expected_attempt_version",
        "runtime_artifact_job:file_analyze",
        "intent.policy_revision=receipt.policy_revision",
        "receipt.session_id=ss.id AND receipt.run_id=r.id",
        "receipt.user_id IS NOT DISTINCT FROM ss.user_id",
        "_agent_runtime_assert_facts_epoch",
        "task_attachment_refs",
        "conversation_attachment_refs",
        "AGENT_RUNTIME_RESOURCE_MANIFEST_INCOMPLETE",
        "r.context_receipt->>'through_message_id'",
        "c.payload->'run_envelope'->'request_identity'",
        "JOIN user_assets asset",
        "asset.storage_owner_key=ss.user_id::TEXT",
        "pg_input_is_valid(source.part->>'asset_id','uuid')",
        "asset.workspace_path!~'(^|/)\\.{1,2}(/|$)'",
        "task.delivery_context->>'corp_id'",
        "task.delivery_context->>'chatid'",
        "SECURITY DEFINER",
        "SET search_path=pg_catalog,public",
        "TO everydayai_agent_runtime_worker",
    ):
        assert contract in SQL
    assert "TO everydayai_worker" not in SQL
    assert "GRANT SELECT" not in SQL


def test_resource_manifest_facade_has_exact_rollback() -> None:
    assert "DROP FUNCTION get_agent_runtime_resource_manifest_v1" in UNDO
    assert "DROP TABLE" not in UNDO
    assert "DROP COLUMN" not in UNDO
