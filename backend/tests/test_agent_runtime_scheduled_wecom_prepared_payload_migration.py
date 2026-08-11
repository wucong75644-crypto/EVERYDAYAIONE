from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_51_agent_runtime_scheduled_wecom_prepared_payload.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_51_agent_runtime_scheduled_wecom_prepared_payload_rollback.sql",
).read_text()
PREPARE_MIGRATION = ROOT.joinpath(
    "migrations/227_39_agent_runtime_scheduled_wecom_dispatch_prepare.sql",
).read_text()
FUNCTION = "read_agent_runtime_scheduled_wecom_prepared_payload_v1"
HELPER = "_agent_runtime_scheduled_wecom_safe_payload_v2"


def _function(sql: str, name: str) -> str:
    marker = f"FUNCTION {name}("
    start = sql.rfind("CREATE", 0, sql.index(marker) + 1)
    plpgsql_end = sql.find("\nEND $$;", start)
    sql_end = sql.find("\n$$;", start)
    if plpgsql_end >= 0 and (sql_end < 0 or plpgsql_end < sql_end):
        end = plpgsql_end + len("\nEND $$;")
    else:
        end = sql_end + len("\n$$;")
    return sql[start:end]


def test_prepared_readback_binds_every_recovery_and_provider_identity() -> None:
    body = _function(MIGRATION, FUNCTION)
    for identity in (
        "p_recovery_request_id", "p_intent_id", "p_item_id", "p_attempt_id",
        "p_attempt_number", "p_claim_request_id", "p_lease_token", "p_worker_id",
        "p_expected_delivery_state_version", "p_expected_item_state_version",
        "p_provider_request_id", "p_idempotency_key", "p_provider_revision",
    ):
        assert identity in body
    for state in (
        "request.lease_expires_at", "d.lease_expires_at<=clock_timestamp()",
        "'claimed'", "'dispatching'", "'prepared','prepared'",
        "dispatch_started_at IS NOT NULL", "unknown_at IS NOT NULL",
        "resolved_at IS NOT NULL", "receipt_type IS NOT NULL", "was_ambiguous",
    ):
        assert state in body
    assert "_agent_runtime_scheduled_wecom_live_context" in body
    assert "read_agent_runtime_scheduled_wecom_dispatch_context_v1" not in body
    assert "LANGUAGE plpgsql VOLATILE SECURITY DEFINER" in body
    assert "attempt.prepared_delivery_state_version" in body
    assert "attempt.prepared_item_state_version" in body


def test_readback_and_helper_are_read_only_and_return_only_revision_two_allowlist() -> None:
    body = _function(MIGRATION, FUNCTION)
    helper = _function(MIGRATION, HELPER)
    for mutation in ("UPDATE ", "INSERT ", "DELETE ", "FOR UPDATE", "FOR SHARE"):
        assert mutation not in body
        assert mutation not in helper
    assert "p_payload_delivery_state_version" in helper
    assert "p_payload_item_state_version" in helper
    success = helper[helper.index("RETURN jsonb_build_object('outcome','payload'"):]
    assert "'payload_revision',2" in success
    for allowed in (
        "scheduled_run_id", "intent_id", "item_id", "item_key", "ordinal",
        "source_identity_hash", "content_identity_hash", "result_hash", "target_hash",
        "channel", "provider_revision", "delivery_state_version", "item_state_version",
        "message_type", "text", "payload_hash",
    ):
        assert f"'{allowed}'" in success
    for forbidden in (
        "secret", "credential", "access_token", "refresh_token", "raw_model",
        "snapshot", "mapping_id", "target_id", "model_result_id", "structured_content",
    ):
        assert forbidden not in success.lower()
    for outcome in ("not_found", "fenced", "unsupported", "unavailable"):
        assert f"'outcome','{outcome}'" in MIGRATION


def test_dependency_gate_acl_search_path_and_exact_rollback() -> None:
    assert MIGRATION.index("DO $$") < MIGRATION.index(f"CREATE FUNCTION {HELPER}")
    assert "DEPENDENCY_DRIFT" in MIGRATION and "pg_get_userbyid" in MIGRATION
    assert MIGRATION.count("STABLE SECURITY DEFINER SET search_path=pg_catalog,public") == 2
    assert MIGRATION.count("VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public") == 1
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION and "CREATE TABLE" not in MIGRATION
    assert f"DROP FUNCTION {FUNCTION}" in ROLLBACK
    assert f"DROP FUNCTION {HELPER}" in ROLLBACK
    assert "read_agent_runtime_scheduled_wecom_dispatch_payload_v1" not in ROLLBACK
    assert _function(
        ROLLBACK, "_agent_runtime_scheduled_wecom_recovery_json",
    ).replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1) == _function(
        PREPARE_MIGRATION, "_agent_runtime_scheduled_wecom_recovery_json",
    )
    assert len(MIGRATION.splitlines()) <= 500
    assert len(ROLLBACK.splitlines()) <= 500
