"""迁移 153 的认证门面、第二批 RLS 与角色授权静态合同。"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/153_runtime_message_rls_and_auth.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback"
    / "153_runtime_message_rls_and_auth_rollback.sql"
).read_text()
TABLES = {
    "users", "organizations", "org_members", "org_configs",
    "wecom_user_mappings", "wecom_chat_targets", "conversations",
    "messages", "tasks", "credits_history", "credit_transactions",
    "image_generations", "detail_projects", "detail_project_images",
    "refresh_tokens", "user_subscriptions", "user_memory_settings",
}
AUTH_FUNCTIONS = {
    "lookup_web_auth_candidate", "register_web_identity", "commit_web_login",
    "rotate_web_refresh_token", "reset_web_password",
    "revoke_web_refresh_token",
}


def _function_body(name: str) -> str:
    start = SQL.index(f"CREATE OR REPLACE FUNCTION {name}(")
    end = SQL.index("\n$$;", start) + 4
    return SQL[start:end]


def test_auth_facades_are_definer_with_fixed_search_path_and_assertion() -> None:
    for name in AUTH_FUNCTIONS:
        body = _function_body(name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
        assert "PERFORM public._assert_web_auth_scope();" in body


def test_auth_scope_requires_web_runtime_and_empty_identity() -> None:
    body = _function_body("_assert_web_auth_scope")
    assert "SECURITY INVOKER" in body
    assert "session_user <> 'everydayai_runtime'" in body
    assert "app.access_kind" in body
    assert "tenant_actor_user_id() IS NOT NULL" in body
    assert "tenant_org_id() IS NOT NULL" in body
    assert "REVOKE ALL ON FUNCTION _assert_web_auth_scope() FROM PUBLIC;" in SQL


def test_registration_is_atomic_and_concurrency_safe() -> None:
    body = _function_body("register_web_identity")
    assert "p_user_id UUID" in body
    assert "id, phone, nickname" in body
    assert "p_user_id, BTRIM(p_phone)" in body
    assert "pg_advisory_xact_lock" in body
    assert "INSERT INTO public.users" in body
    assert "INSERT INTO public.credits_history" in body
    assert "INSERT INTO public.refresh_tokens" in body
    assert "'password_hash'" in body
    signature = "register_web_identity(UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ)"
    assert signature in SQL
    assert signature in ROLLBACK


def test_login_rechecks_all_principals_before_refresh_insert() -> None:
    body = _function_body("commit_web_login")
    assert "FROM public.users" in body and "FOR UPDATE" in body
    assert "FROM public.organizations" in body
    assert "FROM public.org_members" in body
    assert body.index("WEB_AUTH_PRINCIPAL_INACTIVE") < body.index(
        "INSERT INTO public.refresh_tokens"
    )
    assert "INSERT INTO public.user_activity_events" in body
    assert "'login_success', 'web'" in body
    assert "last_active_at = NOW()" in body


def test_refresh_rotation_locks_and_detects_reuse() -> None:
    body = _function_body("rotate_web_refresh_token")
    assert "WHERE token_hash = p_old_hash FOR UPDATE" in body
    assert "'outcome', 'reuse'" in body
    assert "'outcome', 'expired'" in body
    assert "'outcome', 'rotated'" in body
    assert "DELETE FROM public.refresh_tokens" in body


def test_exact_second_group_enables_rls_and_has_policies() -> None:
    enabled = set(re.findall(
        r"ALTER TABLE ([a-z_]+) ENABLE ROW LEVEL SECURITY;",
        SQL,
    ))
    assert enabled == TABLES
    for table in TABLES:
        assert re.search(rf"CREATE POLICY [a-z_]+ ON {table}\b", SQL)
        assert re.search(rf"DROP POLICY IF EXISTS [a-z_]+ ON {table};", ROLLBACK)


def test_user_fact_helper_uses_org_then_user_argument_order() -> None:
    assert "tenant_user_fact_visible(user_id, org_id)" not in SQL
    assert "tenant_user_fact_visible(project.user_id, project.org_id)" not in SQL
    assert "tenant_user_fact_visible(org_id, user_id)" in SQL
    assert "tenant_user_fact_visible(project.org_id, project.user_id)" in SQL


def test_child_writes_require_visible_matching_parent_facts() -> None:
    assert (
        "tenant_conversation_visible(conversation_id, org_id)" in SQL
    )
    assert "project.id = detail_project_images.project_id" in SQL
    assert "project.user_id = detail_project_images.user_id" in SQL
    assert (
        "project.org_id IS NOT DISTINCT FROM detail_project_images.org_id"
        in SQL
    )


def test_sensitive_tables_have_owner_only_policies_and_no_direct_grants() -> None:
    for table in ("refresh_tokens", "wecom_user_mappings", "wecom_chat_targets"):
        policy = re.search(
            rf"CREATE POLICY [a-z_]+ ON {table}\n([\s\S]+?);",
            SQL,
        )
        assert policy is not None
        assert "TO everydayai_owner" in policy.group(0)
        assert "everydayai_runtime" not in policy.group(0)
    assert (
        "REVOKE ALL ON TABLE refresh_tokens, wecom_user_mappings, "
        "wecom_chat_targets"
    ) in SQL


def test_role_grants_do_not_cross_web_and_wecom_capabilities() -> None:
    assert (
        "FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker;" in SQL
    )
    auth_grant = SQL[SQL.index(
        "GRANT EXECUTE ON FUNCTION lookup_web_auth_candidate"
    ):SQL.index("TO everydayai_runtime;", SQL.index(
        "GRANT EXECUTE ON FUNCTION lookup_web_auth_candidate"
    ))]
    assert "wecom" not in auth_grant
    wecom_grant = SQL[SQL.index(
        "GRANT EXECUTE ON FUNCTION resolve_wecom_ingress_user"
    ):SQL.index("TO everydayai_wecom_runtime;", SQL.index(
        "GRANT EXECUTE ON FUNCTION resolve_wecom_ingress_user"
    ))]
    for name in AUTH_FUNCTIONS:
        assert name not in wecom_grant
    assert "TO PUBLIC" not in SQL


def test_rollback_revokes_drops_policies_and_disables_rls() -> None:
    assert "FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;" in ROLLBACK
    disabled = set(re.findall(
        r"ALTER TABLE ([a-z_]+) DISABLE ROW LEVEL SECURITY;",
        ROLLBACK,
    ))
    assert disabled == TABLES
    for name in AUTH_FUNCTIONS:
        assert f"DROP FUNCTION IF EXISTS {name}(" in ROLLBACK
    assert ROLLBACK.rstrip().endswith("RESET ROLE;")
