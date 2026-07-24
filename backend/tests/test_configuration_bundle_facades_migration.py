"""Migration 160 fixed Bundle facade authorization contracts."""

from pathlib import Path
import re

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/160_configuration_resolution_facades.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/160_configuration_resolution_facades_rollback.sql"
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


def test_runtime_actor_requires_active_principal_and_membership() -> None:
    body = _function_body("_assert_configuration_runtime_actor")
    assert "session_user <> 'everydayai_runtime'" in body
    assert "app.access_kind" in body
    assert "status::TEXT = 'active'" in body
    assert "organizations" in body
    assert "org_members" in body
    assert "p_org_required" in body


def test_actorless_oauth_and_worker_require_exact_active_org() -> None:
    oauth = _function_body("_assert_configuration_runtime_oauth")
    worker = _function_body("_assert_configuration_worker_org")
    assert "tenant_actor_user_id() IS NOT NULL" in oauth
    assert "session_user <> 'everydayai_runtime'" in oauth
    assert "session_user <> 'everydayai_worker'" in worker
    assert "'worker'" in worker
    for body in (oauth, worker):
        assert "v_org IS NULL" in body
        assert "organizations" in body
        assert "status = 'active'" in body


def test_all_facades_use_fixed_names_without_key_or_bundle_parameters() -> None:
    facades = {
        "get_ai_dashscope_bundle": "ai.provider.dashscope",
        "get_ai_openrouter_bundle": "ai.provider.openrouter",
        "get_ai_kie_bundle": "ai.provider.kie",
        "get_ai_google_bundle": "ai.provider.google",
        "get_erp_runtime_bundle": "erp.runtime",
        "get_wecom_bot_bundle": "wecom.bot",
        "get_wecom_oauth_public_bundle": "wecom.oauth.public",
        "get_wecom_oauth_exchange_bundle": "wecom.oauth.exchange",
        "get_wecom_contact_bundle": "wecom.contact",
        "get_kuaimai_thinktank_bundle": "kuaimai_external.thinktank",
        "get_kuaimai_viperp_bundle": "kuaimai_external.viperp",
    }
    for function, bundle in facades.items():
        body = _function_body(function)
        assert f"'{bundle}'" in body
        assert "_resolve_configuration_bundle" in body
        signature = body[:body.index("RETURNS JSONB")]
        assert re.search(rf"{function}\(\)\s*$", signature)


def test_role_grants_match_bundle_consumer_matrix() -> None:
    runtime_grant = re.search(
        r"GRANT EXECUTE ON FUNCTION get_ai_dashscope_bundle\(\).*?"
        r"TO everydayai_runtime;",
        SQL,
        re.DOTALL,
    )
    worker_grant = re.search(
        r"GRANT EXECUTE ON FUNCTION get_erp_runtime_bundle\(\).*?"
        r"TO everydayai_worker;",
        SQL,
        re.DOTALL,
    )
    wecom_grant = re.search(
        r"GRANT EXECUTE ON FUNCTION get_wecom_contact_bundle\(\)"
        r"\nTO everydayai_wecom_runtime;",
        SQL,
    )
    assert runtime_grant and "get_wecom_bot_bundle()" not in runtime_grant.group()
    assert worker_grant and "get_ai_" not in worker_grant.group()
    assert wecom_grant


def test_kuaimai_runtime_path_reuses_org_admin_authority() -> None:
    helper = _function_body("_assert_configuration_runtime_org_admin")
    assert "_assert_governance_authority" in helper
    assert "ARRAY['owner', 'admin'], FALSE" in helper
    for name in (
        "get_kuaimai_thinktank_bundle",
        "get_kuaimai_viperp_bundle",
    ):
        body = _function_body(name)
        assert "_assert_configuration_runtime_org_admin" in body
        assert "_assert_configuration_worker_org" in body


def test_internal_assertions_remain_ungranted() -> None:
    grant_section = SQL[SQL.index("GRANT EXECUTE ON FUNCTION"):]
    assert "_assert_configuration_" not in grant_section
    assert "_resolve_configuration_bundle" not in grant_section


def test_rollback_removes_facades_before_assertion_helpers() -> None:
    helper_position = ROLLBACK_SQL.index(
        "DROP FUNCTION IF EXISTS _assert_configuration_runtime_org_admin"
    )
    for facade in (
        "get_ai_dashscope_bundle",
        "get_erp_runtime_bundle",
        "get_wecom_contact_bundle",
        "get_kuaimai_viperp_bundle",
    ):
        assert ROLLBACK_SQL.index(
            f"DROP FUNCTION IF EXISTS {facade}"
        ) < helper_position


def test_migration_runner_orders_160_core_before_facades() -> None:
    identities = [
        migration.identity
        for migration in discover_migrations(ROOT / "migrations")
    ]

    assert identities.index("160_configuration_resolution_core.sql") < (
        identities.index("160_configuration_resolution_facades.sql")
    )
