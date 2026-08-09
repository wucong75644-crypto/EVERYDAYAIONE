from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_24_agent_runtime_provider_cancel_handoff.sql"
ROLLBACK = ROOT / "migrations/rollback/227_24_agent_runtime_provider_cancel_handoff_rollback.sql"


def test_b3_migration_closes_cancel_handoff_contract() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "'operation',operation" in sql
    assert "ADD COLUMN reconciliation_operation" in sql
    assert "AGENT_ACTION_RECOVERY_RUN_CHANGED" in sql
    assert "'parent_run_status',run.status" in sql
    assert "_finalize_agent_action_cancelled_run_v1" in sql
    assert "reconciliation_lease_expires_at<=clock_timestamp()" in sql
    assert "AGENT_CANCEL_CONFIRMATION_FACT_MISMATCH" in sql
    assert "blocking_action_count=blocking_action_count-1" not in sql
    assert "request_agent_runtime_provider_cancel" in sql
    assert "TO everydayai_agent_runtime_worker" in sql


def test_b3_rollback_is_guarded_and_restores_acl() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_ACTION_CANCEL_HANDOFF_ROLLBACK_PENDING_FACTS" in sql
    assert "reconciliation_lease_expires_at > clock_timestamp()" in sql
    assert "cancel_confirmed_at IS NULL" in sql
    assert "CREATE OR REPLACE FUNCTION finalize_agent_action_provider_v2" in sql
    assert "everydayai_agent_runtime_worker, everydayai_worker" in sql
    assert "DROP FUNCTION _finalize_agent_action_cancelled_run_v1" in sql
    assert "DROP COLUMN reconciliation_operation" in sql
