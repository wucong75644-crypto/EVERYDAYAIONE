"""Migration 155 Web WeCom OAuth capability security contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/155_web_wecom_oauth_capabilities.sql"
ROLLBACK = (
    ROOT / "migrations/rollback/155_web_wecom_oauth_capabilities_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")

PUBLIC_FUNCTIONS = (
    "get_web_wecom_oauth_public_config",
    "get_web_wecom_oauth_exchange_config",
    "commit_web_wecom_login",
    "bind_web_wecom_identity",
    "unbind_web_wecom_identity",
    "get_web_wecom_binding_status",
)


def _function_body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_oauth_facades_are_owner_security_definers_with_public_revoked() -> None:
    assert "SET LOCAL ROLE everydayai_owner;" in SQL
    for function_name in PUBLIC_FUNCTIONS:
        body = _function_body(function_name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
        assert re.search(
            rf"REVOKE ALL ON FUNCTION {function_name}\b.*?FROM PUBLIC;",
            SQL,
            re.DOTALL,
        )


def test_scope_guard_binds_database_role_access_kind_actor_and_org() -> None:
    body = _function_body("_assert_web_wecom_oauth_scope")
    assert "session_user <> 'everydayai_runtime'" in body
    assert "current_setting('app.access_kind', TRUE) <> 'runtime'" in body
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in body
    assert "p_actor_required AND public.tenant_actor_user_id() IS NULL" in body
    assert "NOT p_actor_required AND public.tenant_actor_user_id() IS NOT NULL" in body


def test_login_commit_is_atomic_and_does_not_reuse_wecom_ingress_rpc() -> None:
    body = _function_body("commit_web_wecom_login")
    assert "pg_advisory_xact_lock" in body
    assert "INSERT INTO public.users" in body
    assert "INSERT INTO public.wecom_user_mappings" in body
    assert "INSERT INTO public.org_members" in body
    assert "INSERT INTO public.refresh_tokens" in body
    assert "INSERT INTO public.user_activity_events" in body
    assert "wecom_get_or_create_user" not in body


def test_login_safely_adopts_legacy_mapping_without_org() -> None:
    body = _function_body("commit_web_wecom_login")
    assert "IF v_mapping.org_id IS NULL THEN" in body
    assert "SET org_id = p_org_id" in body
    assert "WHERE id = v_mapping.id" in body
    assert "AND org_id IS NULL" in body
    assert "v_mapping.org_id IS DISTINCT FROM p_org_id" in body


def test_bind_rejects_cross_user_identity_without_touching_governance_tables() -> None:
    body = _function_body("bind_web_wecom_identity")
    assert "WEB_WECOM_OAUTH_MERGE_REVIEW_REQUIRED" in body
    assert "_merge_web_wecom_disposable_user" not in body
    assert "user_extra_grants" not in body
    assert "user_revocations" not in body
    assert "DELETE FROM public.users" not in body


def test_bind_persists_login_token_and_activity_in_same_transaction() -> None:
    body = _function_body("bind_web_wecom_identity")
    assert "p_refresh_hash TEXT" in body
    assert "p_refresh_expires_at TIMESTAMPTZ" in body
    assert "INSERT INTO public.refresh_tokens" in body
    assert "INSERT INTO public.user_activity_events" in body


def test_runtime_gets_only_explicit_oauth_facades() -> None:
    grant = re.search(
        r"GRANT EXECUTE ON FUNCTION (?P<body>.*?)\nTO everydayai_runtime;",
        SQL,
        re.DOTALL,
    )
    assert grant
    for function_name in PUBLIC_FUNCTIONS:
        assert function_name in grant.group("body")
    assert "TO everydayai_wecom_runtime" not in SQL
    assert "TO everydayai_worker" not in SQL
    assert "GRANT " not in SQL.replace(grant.group(0), "")


def test_rollback_drops_every_created_function() -> None:
    for function_name in (*PUBLIC_FUNCTIONS, "_assert_web_wecom_oauth_scope"):
        assert f"DROP FUNCTION IF EXISTS {function_name}" in ROLLBACK_SQL
