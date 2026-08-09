from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_24_agent_runtime_provider_cancel_handoff.sql"
ROLLBACK = ROOT / "migrations/rollback/227_24_agent_runtime_provider_cancel_handoff_rollback.sql"
PARENT = ROOT / "migrations/220_25_agent_runtime_authorization_recovery.sql"


def _claim_next_body(path: Path) -> str:
    sql = path.read_text(encoding="utf-8")
    prefix = "CREATE OR REPLACE " if path == ROLLBACK else "CREATE "
    section = sql.split(
        prefix + "FUNCTION claim_next_agent_action_reconciliation", 1,
    )[1]
    return "".join(section.split("AS $$", 1)[1].split("$$;", 1)[0].split())


def test_b3_migration_closes_cancel_handoff_contract() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "'operation',operation" in sql
    assert "ADD COLUMN reconciliation_operation" in sql
    assert "AGENT_ACTION_RECOVERY_RUN_CHANGED" in sql
    assert "dispatch_intent_outcome_unproven" in sql
    assert "JOIN agent_action_dispatch_intents intent" in sql
    assert sql.index("attempt.status='dispatching'") < sql.index(
        "attempt.status IN ('accepted','unknown')",
    )
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
    assert "dispatch_intent_outcome_unproven" in sql
    rollback_claim = sql.split(
        "CREATE OR REPLACE FUNCTION claim_next_agent_action_reconciliation", 1,
    )[1].split("CREATE OR REPLACE FUNCTION get_claimed", 1)[0]
    assert "AGENT_ACTION_RECONCILE_SCAN_INVALID" not in rollback_claim
    assert _claim_next_body(ROLLBACK) == _claim_next_body(PARENT)
