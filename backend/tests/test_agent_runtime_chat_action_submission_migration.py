from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_63_agent_runtime_chat_action_submission.sql"
ROLLBACK = ROOT / "migrations/rollback/227_63_agent_runtime_chat_action_submission_rollback.sql"


def test_chat_action_submission_is_additive_and_fail_closed():
    sql = MIGRATION.read_text()
    rollback = ROLLBACK.read_text()
    assert "CREATE FUNCTION submit_agent_runtime_chat_action_v1" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "_assert_agent_runtime_actor(FALSE)" in sql
    assert "_agent_action_json_is_safe(p_arguments)" in sql
    assert "agent_actions" in sql
    assert "agent_policy_receipts" in sql
    assert "dispatch_policy_receipt_id" in sql
    assert "DROP FUNCTION submit_agent_runtime_chat_action_v1" in rollback
    assert "DROP TABLE" not in sql


def test_chat_action_submission_has_no_worker_table_grant():
    sql = MIGRATION.read_text()
    assert "TO everydayai_worker" not in sql
    assert "TO everydayai_runtime, everydayai_wecom_runtime" in sql
