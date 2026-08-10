from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_49_agent_runtime_scheduled_wecom_unicode_payload.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_49_agent_runtime_scheduled_wecom_unicode_payload_rollback.sql",
).read_text()
PREDECESSOR = ROOT.joinpath(
    "migrations/227_46_agent_runtime_scheduled_wecom_dispatch_payload.sql",
).read_text()
FUNCTION = "read_agent_runtime_scheduled_wecom_dispatch_payload_v1"
HASH_FUNCTION = "_agent_runtime_scheduled_wecom_payload_hash_v2"


def _function(sql: str, name: str) -> str:
    marker = f"FUNCTION {name}("
    start = sql.index(marker)
    start = sql.rfind("CREATE", 0, start)
    end = sql.index("\nEND $$;", start) + len("\nEND $$;")
    return sql[start:end].replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)


def test_v2_hashes_utf8_values_before_ascii_canonical_facts() -> None:
    helper = _function(MIGRATION, HASH_FUNCTION)
    assert "digest(convert_to(p_text,'UTF8'),'sha256')" in helper
    assert "digest(convert_to(p_target::TEXT,'UTF8'),'sha256')" in helper
    canonical = helper[helper.index("_agent_runtime_scheduled_canonical_json("):]
    assert "'text_hash',text_hash" in canonical
    assert "'transport_target_hash',transport_target_hash" in canonical
    assert "'text',p_text" not in canonical and "'target',p_target" not in canonical
    for fact in (
        "source_identity_hash", "content_identity_hash", "result_hash", "target_hash",
        "org_id", "channel", "provider_revision", "delivery_state_version",
        "item_state_version", "item_id", "item_key",
    ):
        assert f"'{fact}'" in canonical


def test_replaced_rpc_preserves_fences_and_returns_only_revision_two() -> None:
    body = _function(MIGRATION, FUNCTION)
    assert "read_agent_runtime_scheduled_wecom_dispatch_context_v1(" in body
    assert "item.state_version IS DISTINCT FROM p_expected_item_state_version" in body
    assert "summary IS DISTINCT FROM derived_summary" in body
    assert "target->>'org_id' IS DISTINCT FROM context->>'org_id'" in body
    assert "_agent_runtime_scheduled_wecom_payload_hash_v2(" in body
    assert "'payload_revision',2" in body
    returned = body[body.index("RETURN jsonb_build_object('outcome','payload'"):]
    assert "text_content" not in returned and "structured_content" not in returned
    for forbidden in (
        "mapping_id", "target_id", "user_id", "access_token", "credential",
        "storage_ref", "object_path", "http://", "https://", "/private/",
    ):
        assert forbidden not in returned.lower()


def test_acl_search_path_and_no_table_rights() -> None:
    assert MIGRATION.count("SECURITY DEFINER SET search_path=pg_catalog,public") == 2
    assert "TO everydayai_wecom_runtime" in MIGRATION
    assert "FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker" in MIGRATION
    for privilege in ("GRANT SELECT", "GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"):
        assert privilege not in MIGRATION
    assert "CREATE TABLE" not in MIGRATION and "ALTER TABLE" not in MIGRATION


def test_rollback_restores_exact_227_46_rpc_and_drops_only_v2_helper() -> None:
    assert _function(ROLLBACK, FUNCTION) == _function(PREDECESSOR, FUNCTION)
    assert f"DROP FUNCTION {HASH_FUNCTION}" in ROLLBACK
    assert "DROP FUNCTION read_agent_runtime_scheduled_wecom_dispatch_payload_v1" not in ROLLBACK
    assert "DROP TABLE" not in ROLLBACK and "ALTER TABLE" not in ROLLBACK
    assert len(MIGRATION.splitlines()) <= 500
    assert len(ROLLBACK.splitlines()) <= 500
