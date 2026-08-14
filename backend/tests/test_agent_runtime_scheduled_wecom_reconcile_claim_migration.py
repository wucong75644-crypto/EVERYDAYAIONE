from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_41_agent_runtime_scheduled_wecom_reconcile_claim.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_41_agent_runtime_scheduled_wecom_reconcile_claim_rollback.sql",
).read_text()


def test_reconcile_candidate_is_unknown_due_and_lease_fenced() -> None:
    candidate = MIGRATION.split(
        "SELECT candidate_delivery.intent_id,candidate_item.id,candidate_attempt.id", 1,
    )[1].split("ORDER BY candidate_attempt.unknown_at", 1)[0]
    for predicate in (
        "candidate_delivery.status IN('unknown','reconcile_required')",
        "candidate_item.status IN('unknown','reconcile_required')",
        "candidate_attempt.status='unknown'",
        "candidate_attempt.dispatch_phase='ambiguous'",
        "COALESCE(candidate_delivery.next_attempt_at,'-infinity'::TIMESTAMPTZ)<=clock_timestamp()",
        "COALESCE(candidate_item.next_attempt_at,'-infinity'::TIMESTAMPTZ)<=clock_timestamp()",
        "candidate_delivery.reconcile_token IS NULL",
        "candidate_delivery.reconcile_lease_expires_at<=clock_timestamp()",
    ):
        assert predicate in candidate
    assert candidate.count("candidate_delivery.reconcile_lease_expires_at<=clock_timestamp()") == 1
    assert "FOR UPDATE OF candidate_delivery SKIP LOCKED LIMIT 1" in MIGRATION


def test_reconcile_contract_is_identity_safe_owner_only_and_failure_closed() -> None:
    assert (
        "agent_runtime_scheduled_wecom_dispatch_attempts\n  WHERE claim_request_id=p_request_id"
        in MIGRATION
    )
    assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert "FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "FOR ALL TO everydayai_owner" in MIGRATION
    assert "TO everydayai_wecom_runtime" in MIGRATION
    for forbidden in ("payload", "secret_encrypted", "dispatch_agent_runtime", "accepted_result"):
        assert forbidden not in MIGRATION.lower()
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "agent_runtime_scheduled_wecom_reconcile_claim_requests" in ROLLBACK
    assert "reconcile_token IS NOT NULL" in ROLLBACK


def test_global_request_namespace_is_bidirectionally_guarded_and_rollback_exact() -> None:
    assert "scheduled-wecom-global-request:" in MIGRATION
    assert "runtime_scheduled_wecom_reconcile_global_request_guard BEFORE INSERT" in MIGRATION
    assert "runtime_scheduled_wecom_delivery_global_request_guard BEFORE UPDATE" in MIGRATION
    assert "runtime_scheduled_wecom_recovery_global_request_guard BEFORE INSERT" in MIGRATION
    assert "runtime_scheduled_wecom_outcome_global_request_guard BEFORE INSERT" in MIGRATION
    assert "WHERE request_id=guard_request_id" in MIGRATION
    for trigger in (
        "runtime_scheduled_wecom_reconcile_global_request_guard",
        "runtime_scheduled_wecom_delivery_global_request_guard",
        "runtime_scheduled_wecom_recovery_global_request_guard",
        "runtime_scheduled_wecom_outcome_global_request_guard",
    ):
        assert f"DROP TRIGGER {trigger}" in ROLLBACK
    for helper in (
        "_agent_runtime_scheduled_wecom_global_request_lock(UUID)",
        "_agent_runtime_scheduled_wecom_reconcile_request_guard()",
        "_agent_runtime_scheduled_wecom_legacy_request_guard()",
    ):
        assert f"DROP FUNCTION {helper}" in ROLLBACK
