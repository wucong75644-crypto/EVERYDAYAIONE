"""Migration 161 one-time legacy import capability contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/161_configuration_legacy_import.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "migrations/rollback/161_configuration_legacy_import_rollback.sql"
).read_text(encoding="utf-8")


def _function_body() -> str:
    match = re.search(
        r"CREATE OR REPLACE FUNCTION import_legacy_configuration_batch\b"
        r".*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match
    return match.group(0)


def _export_function_body() -> str:
    match = re.search(
        r"CREATE OR REPLACE FUNCTION export_legacy_configuration_snapshot\b"
        r".*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match
    return match.group(0)


def test_export_is_reader_only_gated_and_exact_shape() -> None:
    body = _export_function_body()
    assert "session_user <> 'everydayai_config_import_reader'" in body
    assert "app.legacy_config_export" in body
    assert "IS DISTINCT FROM 'read'" in body
    for table in (
        "public.organizations",
        "public.org_configs",
        "public.kuaimai_external_credentials",
    ):
        assert table in body
    for field in (
        "id",
        "wecom_corp_id",
        "encrypt_key",
        "org_id",
        "config_key",
        "config_value_encrypted",
        "source",
        "status",
        "kuaimai_company_id",
        "censeid_cookie",
        "cookie_full",
    ):
        assert f"'{field}'" in body
    grant = SQL[SQL.index(
        "REVOKE ALL ON FUNCTION export_legacy_configuration_snapshot"
    ):]
    assert "TO everydayai_config_import_reader;" in grant
    assert "TO everydayai_migrator" not in grant


def test_import_is_migrator_only_and_requires_explicit_apply_gate() -> None:
    body = _function_body()
    assert "session_user <> 'everydayai_migrator'" in body
    assert "app.legacy_config_import" in body
    assert "IS DISTINCT FROM 'apply'" in body
    assert "<> 'apply'" not in body
    grant = SQL[SQL.index("REVOKE ALL ON FUNCTION"):]
    assert "TO everydayai_migrator;" in grant
    assert "TO everydayai_runtime" not in grant
    assert "TO everydayai_worker" not in grant


def test_batch_is_bounded_exact_shape_and_duplicate_safe() -> None:
    body = _function_body()
    assert "jsonb_array_length(p_items) NOT BETWEEN 1 AND 10000" in body
    assert "jsonb_object_keys(v_item)" in body
    assert "CONFIG_IMPORT_DUPLICATE_ITEM" in body
    for field in (
        "org_id",
        "definition_version",
        "config_key",
        "value_json",
        "secret_envelope",
    ):
        assert field in body


def test_every_item_uses_create_only_cas_inside_one_rpc_transaction() -> None:
    body = _function_body()
    assert "_write_configuration_entry" in body
    call = body[body.index("_write_configuration_entry"):]
    assert "NULLIF(v_item->'value_json', 'null'::JSONB)" in call
    assert "NULLIF(v_item->'secret_envelope', 'null'::JSONB)" in call
    assert re.search(
        r"NULLIF\(v_item->'secret_envelope', 'null'::JSONB\),\s*0, NULL",
        call,
    )
    assert "COMMIT" not in body
    assert "EXCEPTION\n" not in body


def test_import_audit_is_secret_free_force_rls_and_unique_per_target() -> None:
    table = SQL[:SQL.index("CREATE OR REPLACE FUNCTION")]
    assert "configuration_import_audit_log" in table
    assert "ENABLE ROW LEVEL SECURITY" in table
    assert "FORCE ROW LEVEL SECURITY" in table
    assert "TO everydayai_owner" in table
    assert "UNIQUE (org_id, config_key)" in table
    for forbidden in (
        "payload_ciphertext",
        "wrapped_dek",
        "value_json",
        "secret_envelope",
    ):
        assert forbidden not in table


def test_response_and_audit_expose_no_configuration_material() -> None:
    body = _function_body()
    response = body[body.index("RETURN jsonb_build_object"):]
    assert "imported_count" in response
    assert "p_items" not in response
    audit = body[body.index("INSERT INTO public.configuration_import_audit_log"):]
    audit = audit[:audit.index("v_count :=")]
    assert "value_json" not in audit
    assert "secret_envelope" not in audit


def test_rollback_refuses_to_delete_durable_import_audit() -> None:
    assert "CONFIG_IMPORT_ROLLBACK_DATA_PRESENT" in ROLLBACK
    assert ROLLBACK.index("DO $$") < ROLLBACK.index("REVOKE ALL")
    assert ROLLBACK.index("REVOKE ALL") < ROLLBACK.index("DROP FUNCTION")
    assert ROLLBACK.index("DROP FUNCTION") < ROLLBACK.index("DROP TABLE")
    assert "DROP FUNCTION IF EXISTS export_legacy_configuration_snapshot()" in (
        ROLLBACK
    )
