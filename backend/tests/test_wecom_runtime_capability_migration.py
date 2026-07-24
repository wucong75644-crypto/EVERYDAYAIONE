"""迁移 152：WeCom runtime 独立能力静态安全合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/152_wecom_runtime_capability.sql"
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/152_wecom_runtime_capability_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")

PUBLIC_FUNCTIONS = {
    "resolve_wecom_ingress_user": "TEXT, TEXT, UUID, TEXT, TEXT",
    "update_wecom_ingress_chat_address": "TEXT, TEXT, TEXT, TEXT, UUID",
    "upsert_wecom_ingress_chat_target": "TEXT, TEXT, TEXT, UUID",
}


def _function_body(name: str) -> str:
    marker = f"CREATE OR REPLACE FUNCTION {name}("
    start = SQL.find(marker)
    assert start >= 0, f"missing function: {name}"
    end = SQL.find("\n$$;", start)
    assert end >= 0, f"unterminated function: {name}"
    return SQL[start:end + 4]


def test_migration_is_additive_and_keeps_old_checksums_immutable() -> None:
    assert "150_agent_runtime_tenant_defense.sql" not in SQL
    assert "CREATE OR REPLACE FUNCTION tenant_database_role_matches_scope()" in SQL
    assert "WHEN 'everydayai_wecom_runtime'" in SQL
    assert "current_setting('app.access_kind', TRUE) = 'runtime'" in SQL


def test_facades_are_security_definer_with_fixed_search_path() -> None:
    for name in PUBLIC_FUNCTIONS:
        body = _function_body(name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
        assert "PERFORM public._assert_wecom_ingress_scope" in body

    helper = _function_body("_assert_wecom_ingress_scope")
    assert "SECURITY INVOKER" in helper
    assert "SET search_path = pg_catalog, public" in helper
    assert "session_user <> 'everydayai_wecom_runtime'" in helper
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in helper


def test_identity_facade_validates_org_corp_and_serializes_creation() -> None:
    body = _function_body("resolve_wecom_ingress_user")
    helper = _function_body("_assert_wecom_ingress_scope")
    assert "organization.status = 'active'" in helper
    assert "organization.wecom_corp_id" in helper
    assert "SELECT COUNT(*)" in helper
    assert "pg_advisory_xact_lock" in body
    assert "wecom_mappings_uniq_idx" not in body
    assert "INSERT INTO public.users" in body
    assert "INSERT INTO public.wecom_user_mappings" in body
    assert "INSERT INTO public.credits_history" in body
    assert "INSERT INTO public.org_members" in body
    assert "WECOM_INGRESS_MEMBER_INACTIVE" in body
    assert "AND org_id IS NULL" in body
    assert "SET org_id = p_org_id" in body


def test_chat_facades_fail_closed_on_mapping_or_scope_conflict() -> None:
    address = _function_body("update_wecom_ingress_chat_address")
    target = _function_body("upsert_wecom_ingress_chat_target")
    assert "WECOM_INGRESS_MAPPING_MISSING" in address
    assert "AND org_id = p_org_id" in address
    assert "ON CONFLICT (chatid, corp_id) DO UPDATE" in target
    assert "wecom_chat_targets.org_id IS NULL" in target
    assert "wecom_chat_targets.org_id = EXCLUDED.org_id" in target
    assert "WECOM_INGRESS_TARGET_SCOPE_CONFLICT" in target


def test_public_and_legacy_entrypoints_are_not_executable_by_public() -> None:
    signatures = {
        **PUBLIC_FUNCTIONS,
        "_assert_wecom_ingress_scope": "UUID, TEXT",
        "wecom_get_or_create_user": "TEXT, TEXT, UUID, TEXT, TEXT",
    }
    for name, signature in signatures.items():
        assert (
            f"REVOKE ALL ON FUNCTION {name}("
            f"\n    {signature}\n) FROM PUBLIC;"
        ) in SQL
    assert ") TO everydayai;" in SQL
    assert ") TO everydayai_runtime;" not in SQL
    assert ") TO everydayai_wecom_runtime;" not in SQL
    assert ") TO everydayai_worker;" not in SQL


def test_rollback_drops_facades_and_restores_role_matcher() -> None:
    for name in PUBLIC_FUNCTIONS:
        assert f"DROP FUNCTION IF EXISTS {name}(" in ROLLBACK_SQL
    assert "DROP FUNCTION IF EXISTS _assert_wecom_ingress_scope" in ROLLBACK_SQL
    assert "everydayai_wecom_runtime" not in ROLLBACK_SQL
    assert "WHEN 'everydayai_runtime'" in ROLLBACK_SQL
    assert "WHEN 'everydayai_worker'" in ROLLBACK_SQL
    assert ") TO everydayai;" in ROLLBACK_SQL
    assert ") TO everydayai_wecom_runtime;" not in ROLLBACK_SQL
