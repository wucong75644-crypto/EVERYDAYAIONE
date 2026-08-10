from pathlib import Path

from core.db_scope import _rpc_sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_46_agent_runtime_scheduled_wecom_dispatch_payload.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_46_agent_runtime_scheduled_wecom_dispatch_payload_rollback.sql",
).read_text()
FUNCTION = "read_agent_runtime_scheduled_wecom_dispatch_payload_v1"


def _body() -> str:
    start = MIGRATION.index(f"CREATE FUNCTION {FUNCTION}(")
    end = MIGRATION.index("\nEND $$;", start)
    return MIGRATION[start:end + len("\nEND $$;")]


def test_payload_rpc_reuses_exact_live_gate_and_fences_item_and_versions() -> None:
    body = _body()
    assert "_assert_agent_runtime_scheduled_wecom_actor()" in body
    assert "read_agent_runtime_scheduled_wecom_dispatch_context_v1(" in body
    for fence in (
        "p_intent_id", "p_item_id", "p_claim_request_id", "p_lease_token",
        "p_worker_id", "p_expected_delivery_state_version",
        "p_expected_item_state_version", "item.intent_id", "item.state_version",
    ):
        assert fence in body
    assert "RETURN context" not in body
    assert "context||" not in body and "||context" not in body
    assert "FOR SHARE" in body
    assert "FOR UPDATE" not in body


def test_success_uses_only_safe_summary_and_transport_minimal_target() -> None:
    body = _body()
    success = body[body.index("payload_hash:="):]
    assert "scheduled_task_runs" in body
    assert "JOIN agent_model_results model_result_fact" in body
    assert "model_result_fact.id=content.model_result_id" in body
    assert "model_result_fact.content_hash=content.result_hash" in body
    assert "_agent_runtime_scheduled_safe_summary(CASE" in body
    assert "summary IS DISTINCT FROM derived_summary" in body
    assert "length(summary) NOT BETWEEN 1 AND 500" in body
    assert "q.status" in body and "'success'" in body
    assert "agent_runtime_scheduled_finalization_intents" in body
    assert "agent_runtime_scheduled_delivery_contents" in body
    assert "agent_runtime_scheduled_delivery_intents" in body
    assert "target->>'org_id' IS DISTINCT FROM context->>'org_id'" in body
    assert "'org_id'" in body and "'corp_id'" in body and "'wecom_userid'" in body
    assert "'chatid'" in body and "'org_id',(target->>'org_id')::UUID" in success
    for forbidden in (
        "mapping_id", "target_id", "mapping_user_id", "internal_user_id",
        "artifact_manifest", "storage_ref", "object_path", "inline_content",
        "result_files", "last_result",
        "secret", "password", "credential", "access_token", "refresh_token",
        "http://", "https://", "/private/",
    ):
        assert forbidden not in MIGRATION.lower()
    returned = success[success.index("RETURN jsonb_build_object('outcome','payload'"):]
    assert "text_content" not in returned and "structured_content" not in returned
    assert "model_result_id" not in returned
    assert "'payload_hash'" in success and "_agent_runtime_scheduled_canonical_json" in success


def test_unsupported_and_non_success_outcomes_disclose_no_payload_or_versions() -> None:
    body = _body()
    for reason in (
        "wecom_artifact_identity_unsupported",
        "wecom_failed_content_unsupported",
        "wecom_cancelled_content_unsupported",
        "wecom_non_completed_content_unsupported",
    ):
        marker = f"'reason_code','{reason}'"
        assert marker in body
        prefix = body[:body.index(marker)]
        result = prefix[prefix.rfind("RETURN jsonb_build_object("):]
        assert "payload" not in result
        assert "state_version" not in result
    assert "notification" not in MIGRATION.lower()
    assert "fixed text" not in MIGRATION.lower()


def test_acl_is_wecom_only_and_rollback_drops_only_227_46_object() -> None:
    body = _body()
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in body
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker" in MIGRATION
    for privilege in ("GRANT SELECT", "GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"):
        assert privilege not in MIGRATION
    for mutation in ("UPDATE ", "INSERT ", "DELETE ", "CREATE TABLE", "ALTER TABLE"):
        assert mutation not in body
    assert "CREATE OR REPLACE" not in MIGRATION
    assert ROLLBACK.count("DROP FUNCTION") == 1
    assert f"DROP FUNCTION {FUNCTION}" in ROLLBACK
    assert "DROP TABLE" not in ROLLBACK and "ALTER TABLE" not in ROLLBACK
    assert len(MIGRATION.splitlines()) <= 500
    assert len(ROLLBACK.splitlines()) <= 500


def test_scoped_rpc_sql_casts_new_uuid_and_version_signature_only() -> None:
    sql, _ = _rpc_sql(FUNCTION, {
        "p_intent_id": "11111111-1111-1111-1111-111111111111",
        "p_item_id": "22222222-2222-2222-2222-222222222222",
        "p_claim_request_id": "33333333-3333-3333-3333-333333333333",
        "p_lease_token": "44444444-4444-4444-4444-444444444444",
        "p_worker_id": "worker",
        "p_expected_delivery_state_version": 1,
        "p_expected_item_state_version": 0,
    })
    for key in ("p_intent_id", "p_item_id", "p_claim_request_id", "p_lease_token"):
        assert f"{key} := %s::uuid" in sql
    assert "p_expected_delivery_state_version := %s::bigint" in sql
    assert "p_expected_item_state_version := %s::bigint" in sql
    legacy_sql, _ = _rpc_sql(
        "claim_ready_agent_actions_v2", {"p_claim_request_id": "text-request"},
    )
    assert "p_claim_request_id := %s::uuid" not in legacy_sql
