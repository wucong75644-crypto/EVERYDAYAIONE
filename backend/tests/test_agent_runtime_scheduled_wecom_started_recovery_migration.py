from pathlib import Path
import re

from core.db_scope import _rpc_sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_48_agent_runtime_scheduled_wecom_started_recovery.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_48_agent_runtime_scheduled_wecom_started_recovery_rollback.sql",
).read_text()
PREDECESSOR = ROOT.joinpath(
    "migrations/227_47_agent_runtime_scheduled_wecom_unsupported_terminalization.sql",
).read_text()
FUNCTION = "recover_agent_runtime_scheduled_wecom_started_dispatch_v1"
SIGNATURE = f"{FUNCTION}(UUID,TEXT)"
LEDGER = "agent_runtime_scheduled_wecom_started_recovery_requests"
GUARDS = (
    "_agent_runtime_scheduled_wecom_unsupported_request_guard",
    "_agent_runtime_scheduled_wecom_reconcile_request_guard",
    "_agent_runtime_scheduled_wecom_continuation_request_guard",
    "_agent_runtime_scheduled_wecom_reconcile_result_request_guard",
    "_agent_runtime_scheduled_wecom_reconcile_definitive_request_guard",
    "_agent_runtime_scheduled_wecom_legacy_request_guard",
)


def _function(source: str, name: str) -> str:
    match = re.search(rf"CREATE (?:OR REPLACE )?FUNCTION {re.escape(name)}\(", source)
    assert match is not None
    end = source.index("$$;", match.start()) + len("$$;")
    definition = source[match.start():end]
    return " ".join(definition.split()).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")


def test_additive_identity_ledger_is_owner_only_force_rls_and_immutable() -> None:
    assert "227_48:" in MIGRATION
    assert f"CREATE TABLE {LEDGER}(" in MIGRATION
    for field in (
        "request_id UUID PRIMARY KEY", "recovery_worker_id TEXT NOT NULL",
        "org_id UUID NOT NULL", "intent_id UUID NOT NULL", "item_id UUID NOT NULL",
        "attempt_id UUID NOT NULL UNIQUE", "claim_request_id UUID NOT NULL",
        "lease_token UUID NOT NULL", "claim_worker_id TEXT NOT NULL",
        "provider_request_id TEXT NOT NULL", "idempotency_key TEXT NOT NULL",
        "provider_revision BIGINT NOT NULL", "outcome_request_id UUID NOT NULL UNIQUE",
        "original_delivery_state_version BIGINT NOT NULL",
        "original_item_state_version BIGINT NOT NULL",
        "result_delivery_state_version BIGINT NOT NULL",
        "result_item_state_version BIGINT NOT NULL", "recovered_at TIMESTAMPTZ NOT NULL",
    ):
        assert field in MIGRATION
    assert f"ALTER TABLE {LEDGER} ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert f"ALTER TABLE {LEDGER} FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE)" in MIGRATION
    assert "started_recovery_immutable BEFORE UPDATE OR DELETE" in MIGRATION


def test_rpc_selects_only_expired_started_ambiguity_and_reuses_outcome_rpc() -> None:
    body = _function(MIGRATION, FUNCTION)
    assert "_assert_agent_runtime_scheduled_wecom_actor()" in body
    assert "FOR UPDATE OF cd SKIP LOCKED LIMIT 1" in body
    for condition in (
        "cd.status='dispatching'", "cd.lease_expires_at<=clock_timestamp()",
        "ci.status='dispatching'", "ca.status='dispatch_started'",
        "ca.dispatch_phase='external_request_started'", "ca.dispatch_started_at IS NOT NULL",
        "ca.receipt_type IS NULL", "ca.receipt_hash IS NULL", "ca.receipt_code IS NULL",
        "ca.unknown_at IS NULL", "ca.resolved_at IS NULL", "NOT ca.was_ambiguous",
        "cd.reconcile_request_id IS NULL", "cd.reconcile_token IS NULL",
    ):
        assert condition in body
    assert body.count("record_agent_runtime_scheduled_wecom_dispatch_outcome_v1(") == 1
    assert "_agent_runtime_scheduled_wecom_live_context" not in body
    assert "cd.state_version=ca.prepared_delivery_state_version+2" in body
    assert "ci.state_version=ca.prepared_item_state_version+2" in body
    assert "'unknown',NULL,NULL,NULL,'{}'::JSONB" in body
    assert "result->>'outcome' NOT IN('recorded','readback')" in body
    assert "outcome_request:=gen_random_uuid()" in body
    for forbidden in (
        "prepare_agent_runtime_scheduled_wecom_dispatch",
        "start_agent_runtime_scheduled_wecom_dispatch",
        "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts",
        "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts SET",
    ):
        assert forbidden not in body


def test_response_storage_and_sql_are_sensitive_content_free() -> None:
    response = _function(MIGRATION, "_agent_runtime_scheduled_wecom_started_recovery_json")
    for key in (
        "outcome", "request_id", "recovery_worker_id", "org_id", "intent_id", "item_id",
        "attempt_id", "outcome_request_id", "dispatch_outcome", "attempt_status",
        "dispatch_phase", "item_status", "delivery_status", "delivery_state_version",
        "item_state_version", "recovered_at",
    ):
        assert f"'{key}'" in response
    for forbidden in (
        "text_content", "structured_content", "target_snapshot", "mapping_id", "target_id",
        "user_id", "password", "credential", "access_token", "refresh_token", "object_path",
        "inline_content", "http://", "https://", "/private/", "free_text",
    ):
        assert forbidden not in MIGRATION.lower()


def test_global_namespace_is_bidirectional_and_rollback_restores_227_47() -> None:
    for guard in GUARDS:
        assert LEDGER in _function(MIGRATION, guard)
        assert _function(ROLLBACK, guard) == _function(PREDECESSOR, guard)
    new_guard = _function(MIGRATION, "_agent_runtime_scheduled_wecom_started_recovery_request_guard")
    for namespace in (
        "deliveries", "dispatch_attempts", "prepared_recovery_requests", "outcome_requests",
        "reconcile_claim_requests", "continuation_claim_requests", "reconcile_result_requests",
        "reconcile_definitive_requests", "unsupported_requests",
    ):
        assert f"agent_runtime_scheduled_wecom_{namespace}" in new_guard
    assert "STARTED_RECOVERY_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "agent_runtime_scheduled_wecom_outcome_requests" in ROLLBACK
    assert "agent_runtime_scheduled_wecom_dispatch_attempts" in ROLLBACK
    assert "DELETE FROM" not in ROLLBACK
    assert ROLLBACK.count("DROP TABLE") == 1 and f"DROP TABLE {LEDGER}" in ROLLBACK


def test_acl_search_path_scoped_uuid_and_historical_identity_limits() -> None:
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in MIGRATION
    assert f"GRANT EXECUTE ON FUNCTION {FUNCTION}(UUID,TEXT)" in MIGRATION
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker" in MIGRATION
    for privilege in ("GRANT SELECT", "GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"):
        assert privilege not in MIGRATION
    sql, _ = _rpc_sql(FUNCTION, {
        "p_request_id": "11111111-1111-1111-1111-111111111111",
        "p_recovery_worker_id": "recovery-worker",
    })
    assert "p_request_id := %s::uuid" in sql
    legacy, _ = _rpc_sql("request_agent_runtime_scheduled_execution_v1", {
        "p_request_id": "text-request",
    })
    assert "p_request_id := %s::uuid" not in legacy
    assert len(MIGRATION.splitlines()) <= 500 and len(ROLLBACK.splitlines()) <= 500
    assert "CREATE OR REPLACE FUNCTION recover_" not in MIGRATION
    for identity in range(37, 48):
        assert f"227_{identity}" not in MIGRATION
