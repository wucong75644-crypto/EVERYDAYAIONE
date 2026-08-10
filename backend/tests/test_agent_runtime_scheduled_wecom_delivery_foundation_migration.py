from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_37_agent_runtime_scheduled_wecom_delivery.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_37_agent_runtime_scheduled_wecom_delivery_rollback.sql",
).read_text()


def test_foundation_freezes_required_states_and_safe_identity_only_items() -> None:
    for state in (
        "pending", "claimed", "dispatching", "accepted", "unknown",
        "reconcile_required", "retry_wait", "partial", "completed", "failed",
        "cancelled", "unavailable", "prepared", "dispatch_started", "rejected",
    ):
        assert f"'{state}'" in MIGRATION
    for identity in (
        "intent_id", "content_identity_hash", "source_id", "source_revision",
        "source_identity_hash", "provider_request_id", "idempotency_key",
        "provider_revision", "receipt_type", "receipt_hash", "was_ambiguous",
    ):
        assert identity in MIGRATION
    for forbidden in (
        "storage_ref", "object_path", "secret_encrypted", "raw_body",
        "ws_outbound", "PushDispatcher", "Redis", "delivery_worker",
    ):
        assert forbidden not in MIGRATION


def test_foundation_is_owner_only_and_has_failure_closed_rollback() -> None:
    assert MIGRATION.count("ENABLE ROW LEVEL SECURITY") == 3
    assert MIGRATION.count("FORCE ROW LEVEL SECURITY") == 3
    assert MIGRATION.count("TO everydayai_owner") == 3
    assert "GRANT EXECUTE" not in MIGRATION
    assert "read_agent_runtime_scheduled_wecom_reconcile_v1" not in MIGRATION
    assert "claim_agent_runtime_scheduled_wecom" not in MIGRATION
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_BACKFILL_REQUIRED" in MIGRATION
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "DROP TABLE agent_runtime_scheduled_delivery_intents" not in ROLLBACK
    for role in (
        "PUBLIC", "everydayai_wecom_runtime", "everydayai_worker",
        "everydayai_agent_runtime_worker", "everydayai_projection_worker",
        "everydayai_authorization_worker", "everydayai_sandbox_worker",
    ):
        assert role in MIGRATION


def test_attempt_evidence_has_strict_transition_and_immutability_guards() -> None:
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_IDENTITY_IMMUTABLE" in MIGRATION
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_ATTEMPT_TRANSITION_INVALID" in MIGRATION
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_RECEIPT_IMMUTABLE" in MIGRATION
    assert "AGENT_RUNTIME_SCHEDULED_WECOM_EVIDENCE_IMMUTABLE" in MIGRATION
    assert "('prepared','dispatch_started')" in MIGRATION
    assert "('dispatch_started','unknown')" in MIGRATION
    assert "('unknown','accepted')" in MIGRATION
    assert "('unknown','prepared')" not in MIGRATION
    assert len(MIGRATION.splitlines()) <= 500
