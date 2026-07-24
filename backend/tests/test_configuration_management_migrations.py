"""Migration 159 configuration management core and facade contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CORE = (
    ROOT / "migrations/159_configuration_management_core.sql"
).read_text(encoding="utf-8")
FACADES = (
    ROOT / "migrations/159_configuration_management_facades.sql"
).read_text(encoding="utf-8")
CORE_ROLLBACK = (
    ROOT
    / "migrations/rollback/159_configuration_management_core_rollback.sql"
).read_text(encoding="utf-8")
FACADE_ROLLBACK = (
    ROOT
    / "migrations/rollback/159_configuration_management_facades_rollback.sql"
).read_text(encoding="utf-8")


def _function_body(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;(?:\n|$)",
        sql,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_registry_contract_is_secret_free_and_available_to_service_roles() -> None:
    body = _function_body(CORE, "get_configuration_registry_contract")
    assert "definition_version" in body
    assert "config_key" in body
    assert "contract_hash" in body
    assert "contract_json" not in body
    assert "payload_ciphertext" not in body
    grant = re.search(
        r"GRANT EXECUTE ON FUNCTION get_configuration_registry_contract\(\)"
        r"\nTO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;",
        CORE,
    )
    assert grant


def test_platform_and_user_authorities_are_separate_and_actor_bound() -> None:
    platform = _function_body(CORE, "_assert_platform_configuration_actor")
    user = _function_body(CORE, "_assert_user_configuration_actor")
    for body in (platform, user):
        assert "session_user <> 'everydayai_runtime'" in body
        assert "app.access_kind" in body
        assert "tenant_org_id() IS NOT NULL" in body
        assert "status::TEXT = 'active'" in body
    assert "role::TEXT = 'super_admin'" in platform
    assert "role::TEXT = 'super_admin'" not in user


def test_material_validation_uses_active_registry_scope_and_envelope_shape() -> None:
    body = _function_body(CORE, "_validate_configuration_material")
    assert "configuration_definitions" in body
    assert "AND active" in body
    assert "allowed_scopes" in body
    assert "CONFIG_KEY_UNKNOWN" in body
    assert "CONFIG_SCOPE_FORBIDDEN" in body
    assert "jsonb_object_keys(p_secret_envelope)" in body
    for field in ("payload_ciphertext", "wrapped_dek", "kek_version"):
        assert field in body
    assert "CONFIG_VALUE_INVALID" in body


def test_write_uses_row_lock_cas_and_atomic_secret_rotation() -> None:
    body = _function_body(CORE, "_write_configuration_entry")
    assert "FOR UPDATE" in body
    assert body.count("CONFIG_VERSION_CONFLICT") >= 3
    assert "v_new_version := 1" in body
    assert "v_new_version := v_entry.version + 1" in body
    assert "SET status = 'retired'" in body
    assert "INSERT INTO public.secret_records" in body
    assert "payload_version, created_by, updated_by" in body
    assert "ON CONFLICT (scope_kind, org_id, user_id, config_key)" in body


def test_disable_is_idempotent_cas_and_revokes_secret() -> None:
    body = _function_body(CORE, "_disable_configuration_entry")
    assert "_assert_configuration_key_scope" in body
    assert "FOR UPDATE" in body
    assert "'deleted', FALSE" in body
    assert "'deleted', TRUE" in body
    assert "SET status = 'disabled'" in body
    assert "SET status = 'revoked'" in body


def test_status_never_selects_secret_material() -> None:
    body = _function_body(CORE, "_list_configuration_status")
    assert "'configured'" in body
    assert "'source'" in body
    assert "'version'" in body
    assert "'updated_at'" in body
    assert "secret_records" not in body
    assert "payload_ciphertext" not in body
    assert "wrapped_dek" not in body


def test_facades_use_three_narrow_authority_paths() -> None:
    platform = _function_body(FACADES, "set_platform_configuration")
    organization = _function_body(FACADES, "set_org_configuration")
    user = _function_body(FACADES, "set_user_configuration")
    assert "_assert_platform_configuration_actor" in platform
    assert "ARRAY['owner', 'admin'], FALSE" in organization
    assert "_assert_user_configuration_actor" in user
    assert "p_user_id IS DISTINCT FROM v_actor" in user
    assert "CONFIG_USER_AUTHORITY_DENIED" in user
    for body in (platform, organization, user):
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body


def test_all_mutations_audit_only_scope_key_and_version() -> None:
    mutation_names = (
        "set_platform_configuration",
        "set_org_configuration",
        "set_user_configuration",
        "delete_platform_configuration",
        "delete_org_configuration",
        "delete_user_configuration",
    )
    for name in mutation_names:
        body = _function_body(FACADES, name)
        assert "_record_governance_audit" in body
        audit = body[body.index("_record_governance_audit"):]
        assert "p_secret_envelope" not in audit
        assert "p_value_json" not in audit
        assert "'version'" in audit


def test_management_facades_are_runtime_only() -> None:
    grant = FACADES[FACADES.index("GRANT EXECUTE ON FUNCTION"):]
    assert "TO everydayai_runtime;" in grant
    assert "TO everydayai_worker" not in grant
    assert "TO everydayai_wecom_runtime" not in grant
    revoke = FACADES[FACADES.index("REVOKE ALL ON FUNCTION"):]
    assert "everydayai_wecom_runtime" in revoke
    assert "everydayai_worker" in revoke


def test_rollbacks_remove_facades_before_core_dependencies() -> None:
    for name in (
        "set_platform_configuration",
        "set_org_configuration",
        "set_user_configuration",
        "delete_platform_configuration",
        "delete_org_configuration",
        "delete_user_configuration",
        "list_platform_configuration_status",
        "list_org_configuration_status",
        "list_user_configuration_status",
    ):
        assert f"DROP FUNCTION IF EXISTS {name}" in FACADE_ROLLBACK
    for name in (
        "_write_configuration_entry",
        "_disable_configuration_entry",
        "_list_configuration_status",
        "_assert_configuration_key_scope",
        "_assert_platform_configuration_actor",
        "_assert_user_configuration_actor",
    ):
        assert f"DROP FUNCTION IF EXISTS {name}" in CORE_ROLLBACK
