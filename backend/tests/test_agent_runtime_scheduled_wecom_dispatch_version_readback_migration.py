from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_45_agent_runtime_scheduled_wecom_dispatch_version_readback.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_45_agent_runtime_scheduled_wecom_dispatch_version_readback_rollback.sql",
).read_text()

V2_FUNCTIONS = (
    "prepare_agent_runtime_scheduled_wecom_dispatch_v2",
    "start_agent_runtime_scheduled_wecom_dispatch_v2",
    "read_agent_runtime_scheduled_wecom_dispatch_attempt_v2",
)
V1_FUNCTIONS = tuple(name.replace("_v2", "_v1") for name in V2_FUNCTIONS)


def _function_body(name: str) -> str:
    marker = f"CREATE FUNCTION {name}("
    start = MIGRATION.find(marker)
    assert start >= 0, name
    sql_end = MIGRATION.find("\n$$;", start)
    plpgsql_end = MIGRATION.find("\nEND $$;", start)
    endings = [value for value in (sql_end, plpgsql_end) if value >= 0]
    assert endings, name
    end = min(endings)
    suffix = "\nEND $$;" if end == plpgsql_end else "\n$$;"
    return MIGRATION[start : end + len(suffix)]


def test_v2_wrappers_return_authoritative_versions_from_identity_bound_rows() -> None:
    helper = _function_body("_agent_runtime_scheduled_wecom_dispatch_versioned_json")
    for binding in (
        "delivery.intent_id=p_intent_id",
        "item.id=p_item_id",
        "attempt.id=p_attempt_id",
        "attempt.provider_request_id=btrim(p_provider_request_id)",
        "attempt.idempotency_key=p_idempotency_key",
        "attempt.provider_revision=p_provider_revision",
        "delivery.claim_request_id,delivery.lease_token,delivery.claim_worker_id",
        "p_claim_request_id,p_lease_token,btrim(p_worker_id)",
    ):
        assert binding in helper
    assert "delivery.state_version" in helper
    assert "item.state_version" in helper
    assert "jsonb_build_object('outcome','fenced')" in helper
    assert "NOT IN('prepared','dispatch_started','readback') THEN p_result" in helper
    assert "lease_expires_at" not in helper
    for name, v1_name in zip(V2_FUNCTIONS, V1_FUNCTIONS, strict=True):
        wrapper = _function_body(name)
        assert f"{v1_name}(" in wrapper
        assert "_agent_runtime_scheduled_wecom_dispatch_versioned_json" in wrapper
        assert "_assert_agent_runtime_scheduled_wecom_actor()" in wrapper


def test_acl_cutover_is_worker_only_and_rollback_restores_only_v1_surface() -> None:
    for name in V2_FUNCTIONS:
        body = _function_body(name)
        assert "SECURITY DEFINER SET search_path=pg_catalog,public" in body
        assert f"DROP FUNCTION {name}" in ROLLBACK
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "FROM everydayai_wecom_runtime" in MIGRATION
    assert "TO everydayai_wecom_runtime" in ROLLBACK
    assert "FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime" in MIGRATION
    assert "GRANT SELECT" not in MIGRATION
    assert "GRANT INSERT" not in MIGRATION
    assert "GRANT UPDATE" not in MIGRATION
    assert "CREATE TABLE" not in MIGRATION
    assert "ALTER TABLE" not in MIGRATION
    assert "ROLLBACK_HAS_FACTS" not in ROLLBACK
    grant_block = MIGRATION.rsplit("GRANT EXECUTE ON FUNCTION", maxsplit=1)[1]
    assert "_agent_runtime_scheduled_wecom_dispatch_versioned_json" not in grant_block


def test_version_contract_is_additive_and_size_bounded() -> None:
    assert "227_39_agent_runtime_scheduled_wecom_dispatch_prepare" not in MIGRATION
    assert "227_40_agent_runtime_scheduled_wecom_dispatch_outcomes" not in MIGRATION
    assert "record_agent_runtime_scheduled_wecom_dispatch_outcome_v1" not in MIGRATION
    assert "state_version=state_version+1" not in MIGRATION
    assert "UPDATE " not in MIGRATION
    assert "INSERT " not in MIGRATION
    assert len(MIGRATION.splitlines()) <= 500
    assert len(ROLLBACK.splitlines()) <= 500
