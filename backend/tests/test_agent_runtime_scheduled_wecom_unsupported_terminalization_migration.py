from pathlib import Path
import re

from core.db_scope import _rpc_sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_47_agent_runtime_scheduled_wecom_unsupported_terminalization.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_47_agent_runtime_scheduled_wecom_unsupported_terminalization_rollback.sql",
).read_text()
PREDECESSOR = ROOT.joinpath(
    "migrations/227_44_agent_runtime_scheduled_wecom_reconcile_definitive.sql",
).read_text()
FUNCTION = "terminalize_agent_runtime_scheduled_wecom_unsupported_item_v1"
SIGNATURE = f"{FUNCTION}(UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT)"
LEDGER = "agent_runtime_scheduled_wecom_unsupported_requests"
GUARDS = (
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


def test_additive_identity_ledger_rls_and_immutable_contract() -> None:
    assert "227_47:" in MIGRATION
    assert f"CREATE TABLE {LEDGER}(" in MIGRATION
    for field in (
        "request_id UUID PRIMARY KEY", "intent_id UUID NOT NULL", "item_id UUID NOT NULL",
        "claim_request_id UUID NOT NULL UNIQUE", "lease_token UUID NOT NULL",
        "worker_id TEXT NOT NULL", "expected_delivery_state_version BIGINT NOT NULL",
        "expected_item_state_version BIGINT NOT NULL", "reason_code TEXT NOT NULL",
        "result_item_status TEXT NOT NULL", "result_delivery_status TEXT NOT NULL",
        "result_delivery_state_version BIGINT NOT NULL",
        "result_item_state_version BIGINT NOT NULL", "terminalized_at TIMESTAMPTZ NOT NULL",
    ):
        assert field in MIGRATION
    assert f"ALTER TABLE {LEDGER} ENABLE ROW LEVEL SECURITY" in MIGRATION
    assert f"ALTER TABLE {LEDGER} FORCE ROW LEVEL SECURITY" in MIGRATION
    assert "runtime_scheduled_wecom_unsupported_immutable BEFORE UPDATE OR DELETE" in MIGRATION
    assert "FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE)" in MIGRATION


def test_rpc_is_fenced_server_derived_and_does_not_create_transport_or_attempts() -> None:
    body = _function(MIGRATION, FUNCTION)
    assert "_assert_agent_runtime_scheduled_wecom_actor()" in body
    assert "_agent_runtime_scheduled_wecom_global_request_lock(p_request_id)" in body
    assert "agent_runtime_scheduled_wecom_continuation_claim_requests" in body
    assert "claim.delivery_state_version" not in body
    assert "d.state_version) IS DISTINCT FROM('claimed'" in body
    assert "p_expected_delivery_state_version)" in body
    assert "claim.item_state_version" in body
    assert "read_agent_runtime_scheduled_wecom_dispatch_payload_v1(" in body
    assert "gate->>'outcome'<>'unsupported'" in body
    assert "p_reason" not in body and "p_free" not in body
    assert "item.status NOT IN('pending','retry_wait')" in body
    assert "agent_runtime_scheduled_wecom_dispatch_attempts a WHERE a.item_id=item.id" in body
    assert "UPDATE agent_runtime_scheduled_wecom_dispatch_attempts" not in body
    assert "INSERT INTO agent_runtime_scheduled_wecom_dispatch_attempts" not in body
    assert "state_version=state_version+1" in body
    assert "claim_worker_id=NULL,claim_request_id=NULL,lease_token=NULL" in body
    assert "status='pending'" in body
    for reason in (
        "wecom_artifact_identity_unsupported", "wecom_failed_content_unsupported",
        "wecom_cancelled_content_unsupported", "wecom_non_completed_content_unsupported",
    ):
        assert reason in body


def test_response_and_storage_are_exact_and_sensitive_content_free() -> None:
    json_body = _function(MIGRATION, "_agent_runtime_scheduled_wecom_unsupported_json")
    assert set(re.findall(r"'([a-z_]+)'", json_body)) >= {
        "outcome", "request_id", "intent_id", "item_id", "reason_code", "item_status",
        "delivery_status", "delivery_state_version", "item_state_version", "terminalized_at",
    }
    for forbidden in (
        "text_content", "structured_content", "model_result", "target_snapshot", "mapping_id",
        "target_id", "user_id", "secret", "password", "credential", "access_token",
        "refresh_token", "storage_ref", "object_path", "inline_content", "http://", "https://",
        "/private/", "receipt_metadata", "free_text",
    ):
        assert forbidden not in MIGRATION.lower()


def test_global_namespace_is_bidirectional_and_rollback_restores_predecessors() -> None:
    for guard in GUARDS:
        migrated = _function(MIGRATION, guard)
        assert LEDGER in migrated
        assert _function(ROLLBACK, guard) == _function(PREDECESSOR, guard)
    new_guard = _function(MIGRATION, "_agent_runtime_scheduled_wecom_unsupported_request_guard")
    for namespace in (
        "deliveries", "dispatch_attempts", "prepared_recovery_requests", "outcome_requests",
        "reconcile_claim_requests", "continuation_claim_requests", "reconcile_result_requests",
        "reconcile_definitive_requests",
    ):
        assert f"agent_runtime_scheduled_wecom_{namespace}" in new_guard
    assert "UNSUPPORTED_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "terminal_reason_code IN(" in ROLLBACK
    assert ROLLBACK.count("DROP TABLE") == 1 and f"DROP TABLE {LEDGER}" in ROLLBACK
    assert "DELETE FROM" not in ROLLBACK


def test_acl_search_path_and_scoped_rpc_types_are_exact() -> None:
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in MIGRATION
    assert f"GRANT EXECUTE ON FUNCTION {FUNCTION}(" in MIGRATION
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker" in MIGRATION
    for privilege in ("GRANT SELECT", "GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"):
        assert privilege not in MIGRATION
    sql, _ = _rpc_sql(FUNCTION, {
        "p_request_id": "11111111-1111-1111-1111-111111111111",
        "p_intent_id": "22222222-2222-2222-2222-222222222222",
        "p_item_id": "33333333-3333-3333-3333-333333333333",
        "p_claim_request_id": "44444444-4444-4444-4444-444444444444",
        "p_lease_token": "55555555-5555-5555-5555-555555555555",
        "p_worker_id": "worker", "p_expected_delivery_state_version": 1,
        "p_expected_item_state_version": 0,
    })
    for key in (
        "p_request_id", "p_intent_id", "p_item_id", "p_claim_request_id", "p_lease_token",
    ):
        assert f"{key} := %s::uuid" in sql
    assert "p_expected_delivery_state_version := %s::bigint" in sql
    assert "p_expected_item_state_version := %s::bigint" in sql
    legacy, _ = _rpc_sql("claim_ready_agent_actions_v2", {"p_claim_request_id": "text"})
    assert "p_claim_request_id := %s::uuid" not in legacy


def test_size_and_historical_identity_limits() -> None:
    assert len(MIGRATION.splitlines()) <= 500
    assert len(ROLLBACK.splitlines()) <= 500
    assert "CREATE OR REPLACE FUNCTION terminalize_" not in MIGRATION
    for identity in range(37, 47):
        assert f"227_{identity}" not in MIGRATION
