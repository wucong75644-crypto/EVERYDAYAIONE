from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_40_agent_runtime_scheduled_wecom_dispatch_outcomes.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_40_agent_runtime_scheduled_wecom_dispatch_outcomes_rollback.sql",
).read_text()


def _function_body(name: str) -> str:
    start = MIGRATION.find(f"CREATE FUNCTION {name}(")
    assert start >= 0, name
    endings = [
        value for value in (
            MIGRATION.find("\n$$;", start),
            MIGRATION.find("\nEND $$;", start),
        ) if value >= 0
    ]
    assert endings, name
    end = min(endings)
    suffix = "\nEND $$;" if MIGRATION.startswith("\nEND $$;", end) else "\n$$;"
    return MIGRATION[start:end + len(suffix)]


def test_worker_surface_is_one_rpc_with_force_rls_and_no_table_grant() -> None:
    rpc = _function_body("record_agent_runtime_scheduled_wecom_dispatch_outcome_v1")
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in rpc
    assert "_assert_agent_runtime_scheduled_wecom_actor()" in rpc
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert "FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "OUTCOME_REQUEST_IMMUTABLE" in MIGRATION
    for privilege in ("GRANT SELECT", "GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"):
        assert privilege not in MIGRATION


def test_receipt_metadata_is_typed_allowlisted_bounded_and_hash_bound() -> None:
    validator = _function_body("_agent_runtime_scheduled_wecom_receipt_metadata_valid")
    rpc = _function_body("record_agent_runtime_scheduled_wecom_dispatch_outcome_v1")
    for key in (
        "provider_message_id", "http_status", "wecom_errcode", "wecom_errmsg",
        "provider_code", "trace_id",
    ):
        assert key in validator
    assert "ELSE RETURN FALSE" in validator
    assert "pg_column_size(p_metadata)>4096" in validator
    assert "jsonb_typeof(entry.value)" in validator
    for sensitive in ("token", "secret", "authorization", "payload", "raw[_-]?body"):
        assert sensitive in validator
    assert "everydayai.scheduled_wecom.dispatch_receipt.v1" in MIGRATION
    for bound_field in (
        "p_dispatch_outcome", "p_receipt_type", "p_receipt_code", "p_receipt_metadata",
        "p_provider_request_id", "p_idempotency_key", "p_provider_revision",
    ):
        assert bound_field in _function_body("_agent_runtime_scheduled_wecom_receipt_hash")
    assert "p_receipt_hash IS DISTINCT FROM" in rpc
    assert "p_receipt_metadata<>'{}'::JSONB" in rpc
    assert "p_receipt_type NOT IN('wecom_app','wecom_smart_robot')" in rpc
    assert "ADD CONSTRAINT runtime_scheduled_wecom_outcome_receipt_typed" in MIGRATION
    assert "receipt_hash=_agent_runtime_scheduled_wecom_receipt_hash" in MIGRATION


def test_transition_fences_identity_versions_current_claim_and_ordinal() -> None:
    rpc = _function_body("record_agent_runtime_scheduled_wecom_dispatch_outcome_v1")
    for fence in (
        "d.claim_request_id", "d.lease_token", "d.claim_worker_id",
        "_agent_runtime_scheduled_wecom_attempt_identity_matches",
        "p_provider_revision IS DISTINCT FROM d.provider_revision",
        "d.state_version IS DISTINCT FROM p_expected_delivery_state_version",
        "item.state_version IS DISTINCT FROM p_expected_item_state_version",
        "earlier.ordinal<item.ordinal", "later.ordinal>item.ordinal",
    ):
        assert fence in rpc
    assert "a.status<>'dispatch_started'" in rpc
    assert "item.status<>'dispatching'" in rpc
    assert "d.status<>'dispatching'" in rpc
    assert "(a.claim_request_id,a.lease_token,a.claim_worker_id)" not in rpc
    assert "lease_expires_at<=clock_timestamp()" not in rpc
    assert "_agent_runtime_scheduled_wecom_live_context" not in rpc


def test_atomic_mappings_preserve_active_claim_or_clear_it() -> None:
    rpc = _function_body("record_agent_runtime_scheduled_wecom_dispatch_outcome_v1")
    assert "status='claimed'" in rpc
    assert "remaining.status IN('pending','retry_wait')" in rpc
    assert "accepted_count=item_count THEN 'completed'" in rpc
    assert "accepted_count>0 THEN 'partial' ELSE 'failed'" in rpc
    assert "status='unknown'" in rpc
    assert "claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL,lease_expires_at=NULL" in rpc
    assert "dispatch_phase=CASE WHEN p_dispatch_outcome='unknown' THEN 'ambiguous'" in rpc
    assert "INSERT INTO agent_runtime_scheduled_wecom_outcome_requests" in rpc


def test_request_readback_precedes_transition_and_conflicts_fail_closed() -> None:
    rpc = _function_body("record_agent_runtime_scheduled_wecom_dispatch_outcome_v1")
    readback = rpc.index("RETURN _agent_runtime_scheduled_wecom_outcome_json(request,'readback')")
    first_update = rpc.index("UPDATE agent_runtime_scheduled_wecom_dispatch_attempts")
    assert readback < first_update
    assert "pg_advisory_xact_lock" in rpc
    assert "attempt_id UUID NOT NULL UNIQUE" in MIGRATION
    assert "OUTCOME_REQUEST_CONFLICT" in rpc


def test_rollback_is_guarded_and_scope_excludes_transport_and_reconcile() -> None:
    assert "OUTCOME_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "status IN('accepted','rejected','unknown')" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_wecom_outcome_requests" in ROLLBACK
    for protected in (
        "agent_runtime_scheduled_wecom_dispatch_attempts",
        "agent_runtime_scheduled_wecom_delivery_items",
        "agent_runtime_scheduled_wecom_deliveries",
    ):
        assert f"DROP TABLE {protected}" not in ROLLBACK
    for excluded in ("transport", "secret_encrypted", "reconcile_token", "cancel_dispatch"):
        assert excluded not in MIGRATION.lower()
    assert len(MIGRATION.splitlines()) <= 500
