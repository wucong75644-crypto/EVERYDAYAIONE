from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_26_agent_runtime_sandbox_cancel_handoff.sql"
ROLLBACK = ROOT / "migrations/rollback/227_26_agent_runtime_sandbox_cancel_handoff_rollback.sql"


def test_b5_migration_has_narrow_fenced_cancel_and_proof_finalizer() -> None:
    sql = MIGRATION.read_text()
    assert "request_agent_runtime_sandbox_cancel_v1" in sql
    assert "finalize_agent_action_sandbox_cancel_v1" in sql
    assert "claim_next_sandbox_cancel_v1" in sql
    assert "job.lease_expires_at<=clock_timestamp()" in sql
    assert "OR (job.status='unknown' AND job.claim_token IS NULL)" in sql
    assert "SANDBOX_CANCEL_OWNER_TAKEOVER" in sql
    assert "reconciliation_worker_id=NULL,reconciliation_token=NULL" in sql
    assert "attempt.reconciliation_operation IS DISTINCT FROM 'cancel'" in sql
    assert "_agent_runtime_kill_epoch_context" in sql
    assert "reconciliation_operation IS DISTINCT FROM 'cancel'" in sql
    assert "reconciliation_parent_run_state_version" in sql
    assert "reconciliation_lease_expires_at<=clock_timestamp()" in sql
    assert "job.cancel_confirmed_at IS NULL" in sql
    assert "job.receipt_hash IS DISTINCT FROM p_receipt_hash" in sql
    assert "job.cleanup_status NOT IN ('not_required','completed')" in sql
    assert "NOT _agent_sandbox_evidence_is_valid(job.cleanup_evidence)" in sql
    assert "agent_sandbox_cancel_terminal_fence" in sql
    assert "NEW.status IN ('succeeded','failed','timed_out')" in sql
    assert "REVOKE EXECUTE ON FUNCTION request_sandbox_job_cancel" in sql
    assert "TO everydayai_agent_runtime_worker" in sql
    assert "TO everydayai_sandbox_worker" in sql


def test_b5_rollback_is_guarded_and_restores_prior_acl() -> None:
    sql = ROLLBACK.read_text()
    assert "AGENT_SANDBOX_CANCEL_HANDOFF_ROLLBACK_PENDING_FACTS" in sql
    assert "job.cancel_requested_at IS NOT NULL" in sql
    assert "job.reconciliation_token IS NOT NULL" in sql
    assert sql.index("DROP FUNCTION finalize_agent_action_sandbox_cancel_v1") < sql.index(
        "DROP TRIGGER agent_sandbox_cancel_terminal_fence"
    )
    assert "GRANT EXECUTE ON FUNCTION request_sandbox_job_cancel" in sql
    assert "TO everydayai_agent_runtime_worker" in sql


def test_b5_historical_sandbox_migrations_are_not_redefined() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION request_sandbox_job_cancel" not in sql
    assert "CREATE OR REPLACE FUNCTION finish_sandbox_job" not in sql
    assert "CREATE OR REPLACE FUNCTION resolve_sandbox_job_reconciliation" not in sql
