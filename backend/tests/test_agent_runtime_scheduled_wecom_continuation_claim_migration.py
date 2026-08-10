from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_42_agent_runtime_scheduled_wecom_continuation_claim.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_42_agent_runtime_scheduled_wecom_continuation_claim_rollback.sql",
).read_text()


def _function_body(name: str) -> str:
    marker = f"CREATE FUNCTION {name}("
    start = MIGRATION.find(marker)
    assert start >= 0, name
    end = MIGRATION.find("\nEND $$;", start)
    assert end >= 0, name
    return MIGRATION[start:end + len("\nEND $$;")]


def test_initial_and_continuation_candidates_share_strict_unattempted_next_item() -> None:
    claim = _function_body("claim_agent_runtime_scheduled_wecom_delivery_v2")
    for contract in (
        "candidate_delivery.status IN('pending','retry_wait')",
        "candidate_delivery.status='claimed'",
        "candidate_delivery.lease_expires_at<=clock_timestamp()",
        "candidate_delivery.reconcile_token IS NULL",
        "candidate_item.status IN('pending','retry_wait')",
        "current_attempt.item_id=candidate_item.id",
        "unsafe_attempt.status NOT IN('accepted','rejected')",
        "unsafe_attempt.dispatch_phase<>'receipt_recorded'",
        "earlier.ordinal<candidate_item.ordinal",
        "earlier.status NOT IN('accepted','failed','cancelled')",
        "later.status NOT IN('pending','retry_wait','cancelled')",
        "FOR UPDATE OF candidate_delivery SKIP LOCKED LIMIT 1",
    ):
        assert contract in claim
    assert "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts" not in MIGRATION
    assert "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts" not in MIGRATION
    assert "CASE WHEN has_attempts THEN 'continuation' ELSE 'initial' END" in claim


def test_unavailable_candidate_is_durably_terminalized_without_attempt_mutation() -> None:
    claim = _function_body("claim_agent_runtime_scheduled_wecom_delivery_v2")
    terminalize = _function_body(
        "_agent_runtime_scheduled_wecom_terminalize_unavailable_continuation",
    )
    assert "_agent_runtime_scheduled_wecom_cancel_unavailable" in claim
    assert "_agent_runtime_scheduled_wecom_terminalize_unavailable_continuation" in claim
    assert "status='cancelled'" in terminalize
    assert "WHEN accepted_count=item_count THEN 'completed'" in terminalize
    assert "WHEN accepted_count>0 THEN 'partial' ELSE 'failed'" in terminalize
    assert "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts" not in terminalize


def test_ledger_is_append_only_owner_only_and_response_loss_readable() -> None:
    assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert "FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "FOR ALL TO everydayai_owner" in MIGRATION
    assert "BEFORE UPDATE OR DELETE" in MIGRATION
    assert "WHERE(item.id,item.intent_id)=(NEW.item_id,NEW.intent_id)" in MIGRATION
    claim = _function_body("claim_agent_runtime_scheduled_wecom_delivery_v2")
    assert "WHERE request_id=p_claim_request_id" in claim
    assert "THEN 'readback' ELSE 'fenced' END" in claim
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT" in claim
    for forbidden in ("result ledger", "reconcile_result", "payload", "secret", "redis_key"):
        assert forbidden not in MIGRATION.lower()


def test_global_namespace_v1_revoke_and_exact_rollback_contract() -> None:
    for table in (
        "agent_runtime_scheduled_wecom_deliveries",
        "agent_runtime_scheduled_wecom_dispatch_attempts",
        "agent_runtime_scheduled_wecom_prepared_recovery_requests",
        "agent_runtime_scheduled_wecom_outcome_requests",
        "agent_runtime_scheduled_wecom_reconcile_claim_requests",
        "agent_runtime_scheduled_wecom_continuation_claim_requests",
    ):
        assert table in MIGRATION
    assert "CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_request_guard" in MIGRATION
    assert "CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_legacy_request_guard" in MIGRATION
    assert "REVOKE EXECUTE ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v1" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v1" in ROLLBACK
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_wecom_continuation_claim_requests" in ROLLBACK
    assert "CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_request_guard" in ROLLBACK
    assert "CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_legacy_request_guard" in ROLLBACK


def test_rpc_is_worker_scoped_fixed_search_path_and_narrowly_granted() -> None:
    claim = _function_body("claim_agent_runtime_scheduled_wecom_delivery_v2")
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in claim
    assert "_assert_agent_runtime_scheduled_wecom_actor()" in claim
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION
    assert len(MIGRATION.splitlines()) <= 500
