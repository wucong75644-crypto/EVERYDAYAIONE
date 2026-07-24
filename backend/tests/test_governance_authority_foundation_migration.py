"""Migration 156a governance authority and audit security contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/156_governance_authority_foundation.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/156_governance_authority_foundation_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_audit_table_is_force_rls_owner_only_and_secret_free() -> None:
    assert "CREATE TABLE governance_audit_log" in SQL
    assert "ALTER TABLE governance_audit_log ENABLE ROW LEVEL SECURITY" in SQL
    assert "ALTER TABLE governance_audit_log FORCE ROW LEVEL SECURITY" in SQL
    assert "CREATE POLICY governance_audit_owner_only" in SQL
    assert "TO everydayai_owner" in SQL
    assert "config_value" not in SQL
    assert "secret" not in _function_body("_record_governance_audit").lower()


def test_authority_guard_binds_runtime_actor_and_exact_org_scope() -> None:
    body = _function_body("_assert_governance_authority")
    assert "session_user <> 'everydayai_runtime'" in body
    assert "current_setting('app.access_kind', TRUE) <> 'runtime'" in body
    assert "tenant_actor_user_id()" in body
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in body
    assert "status::TEXT = 'active'" in body
    assert "v_user_role = 'super_admin'" in body
    assert "status = 'active'" in body
    assert "v_member_role = ANY(p_allowed_org_roles)" in body


def test_non_null_org_must_be_active_before_super_admin_is_allowed() -> None:
    body = _function_body("_assert_governance_authority")
    org_guard = body.index("IF p_org_id IS NOT NULL AND NOT EXISTS")
    inactive_error = body.index("GOVERNANCE_ORG_INACTIVE")
    super_admin_return = body.index(
        "IF p_allow_super_admin AND v_user_role = 'super_admin'"
    )
    assert org_guard < inactive_error < super_admin_return
    assert "WHERE id = p_org_id AND status = 'active'" in body


def test_internal_functions_are_not_executable_by_service_roles() -> None:
    for function_name in (
        "_assert_governance_authority",
        "_record_governance_audit",
        "_assert_governance_self_scope",
    ):
        assert re.search(
            rf"REVOKE ALL ON FUNCTION {function_name}\b.*?"
            r"everydayai_runtime.*?everydayai_wecom_runtime.*?"
            r"everydayai_worker;",
            SQL,
            re.DOTALL,
        )
    grant = SQL[SQL.index("GRANT EXECUTE ON FUNCTION"):]
    assert "_assert_governance_authority" not in grant
    assert "_record_governance_audit" not in grant
    assert "_assert_governance_self_scope" not in grant


def test_read_facades_are_minimally_granted_to_web_runtime() -> None:
    functions = (
        "list_actor_organizations",
        "get_governed_organization",
        "list_governed_members",
        "list_actor_pending_invitations",
        "list_all_governed_organizations",
        "search_governed_user_by_phone",
    )
    grant = re.search(
        r"GRANT EXECUTE ON FUNCTION (?P<body>.*?)\nTO everydayai_runtime;",
        SQL,
        re.DOTALL,
    )
    assert grant
    for function_name in functions:
        body = _function_body(function_name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
        assert function_name in grant.group("body")
    assert "TO everydayai_wecom_runtime" not in grant.group(0)
    assert "TO everydayai_worker" not in grant.group(0)


def test_member_reads_mask_phone_and_never_return_encryption_material() -> None:
    body = _function_body("list_governed_members")
    assert "'****'" in body
    organization = _function_body("get_governed_organization")
    assert "'encrypt_key'" not in organization
    assert "'wecom_secret_encrypted'" not in organization


def test_super_admin_reads_use_database_authority_guard() -> None:
    for function_name in (
        "list_all_governed_organizations",
        "search_governed_user_by_phone",
    ):
        body = _function_body(function_name)
        assert "_assert_governance_authority" in body
        assert "ARRAY[]::TEXT[], TRUE" in body


def test_actor_self_reads_are_bound_to_active_database_principal() -> None:
    guard = _function_body("_assert_governance_self_scope")
    assert "session_user <> 'everydayai_runtime'" in guard
    assert "current_setting('app.access_kind', TRUE) <> 'runtime'" in guard
    assert "tenant_actor_user_id()" in guard
    assert "status::TEXT = 'active'" in guard
    for function_name in (
        "list_actor_organizations",
        "list_actor_pending_invitations",
    ):
        assert "_assert_governance_self_scope" in _function_body(function_name)
    assert "invitation.expires_at > NOW()" in _function_body(
        "list_actor_pending_invitations"
    )


def test_audit_uses_scoped_actor_and_request_id() -> None:
    body = _function_body("_record_governance_audit")
    assert "public.tenant_actor_user_id()" in body
    assert "current_setting('app.request_id', TRUE)" in body
    assert "jsonb_typeof(p_metadata) <> 'object'" in body


def test_rollback_removes_functions_before_table() -> None:
    audit_drop = ROLLBACK_SQL.index(
        "DROP FUNCTION IF EXISTS _record_governance_audit"
    )
    guard_drop = ROLLBACK_SQL.index(
        "DROP FUNCTION IF EXISTS _assert_governance_authority"
    )
    table_drop = ROLLBACK_SQL.index(
        "DROP TABLE IF EXISTS governance_audit_log"
    )
    assert audit_drop < table_drop
    assert guard_drop < table_drop
    for function_name in (
        "search_governed_user_by_phone",
        "list_all_governed_organizations",
        "list_actor_pending_invitations",
        "list_governed_members",
        "get_governed_organization",
        "list_actor_organizations",
        "_assert_governance_self_scope",
    ):
        assert f"DROP FUNCTION IF EXISTS {function_name}" in ROLLBACK_SQL
