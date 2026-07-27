"""Migration 216 Runtime administrator WeCom test Bundle contract."""

from pathlib import Path
import re

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/216_configuration_admin_test_bundle.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/216_configuration_admin_test_bundle_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")
FACADE_SQL = (
    ROOT / "migrations/160_configuration_resolution_facades.sql"
).read_text(encoding="utf-8")
GOVERNANCE_SQL = (
    ROOT / "migrations/156_governance_authority_foundation.sql"
).read_text(encoding="utf-8")
FUNCTION = "get_wecom_bot_admin_test_bundle"


def test_admin_test_facade_is_fixed_and_runtime_org_admin_scoped() -> None:
    assert f"CREATE OR REPLACE FUNCTION {FUNCTION}()" in SQL
    assert "_assert_configuration_runtime_org_admin()" in SQL
    assert "'v1', 'wecom.bot', v_actor, public.tenant_org_id()" in SQL
    assert "SECURITY DEFINER" in SQL
    assert "SET search_path = pg_catalog, public" in SQL


def test_admin_authority_fails_closed_without_platform_bypass() -> None:
    helper = re.search(
        r"CREATE OR REPLACE FUNCTION "
        r"_assert_configuration_runtime_org_admin\(\).*?\n\$\$;",
        FACADE_SQL,
        re.DOTALL,
    )
    governance = re.search(
        r"CREATE OR REPLACE FUNCTION _assert_governance_authority\(.*?\n\$\$;",
        GOVERNANCE_SQL,
        re.DOTALL,
    )
    assert helper and governance
    assert "ARRAY['owner', 'admin'], FALSE" in helper.group()
    for requirement in (
        "session_user <> 'everydayai_runtime'",
        "app.access_kind",
        "v_actor IS NULL",
        "tenant_org_id() IS DISTINCT FROM p_org_id",
        "status::TEXT = 'active'",
        "status = 'active'",
        "v_member_role = ANY(p_allowed_org_roles)",
    ):
        assert requirement in governance.group()


def test_admin_test_facade_has_exact_service_role_acl() -> None:
    revoke = re.search(
        rf"REVOKE ALL ON FUNCTION {FUNCTION}\(\).*?;",
        SQL,
        re.DOTALL,
    )
    assert revoke
    for role in (
        "PUBLIC",
        "everydayai_runtime",
        "everydayai_wecom_runtime",
        "everydayai_worker",
        "everydayai_sync",
        "everydayai",
    ):
        assert role in revoke.group()
    assert (
        f"GRANT EXECUTE ON FUNCTION {FUNCTION}()\n"
        "TO everydayai_runtime;"
    ) in SQL
    grant_section = SQL[SQL.index("GRANT EXECUTE"):]
    for role in (
        "everydayai_wecom_runtime",
        "everydayai_worker",
        "everydayai_sync",
        "everydayai;",
    ):
        assert role not in grant_section


def test_worker_bundle_contract_is_not_modified() -> None:
    assert "get_wecom_bot_bundle" not in SQL
    assert "get_wecom_bot_bundle" not in ROLLBACK_SQL


def test_rollback_only_removes_new_facade() -> None:
    assert f"REVOKE ALL ON FUNCTION {FUNCTION}()" in ROLLBACK_SQL
    assert f"DROP FUNCTION IF EXISTS {FUNCTION}();" in ROLLBACK_SQL
    for forbidden in (
        "DROP TABLE",
        "DELETE FROM",
        "TRUNCATE",
        "configuration_entries",
        "secret_records",
    ):
        assert forbidden not in ROLLBACK_SQL


def test_migration_runner_places_admin_facade_after_current_head() -> None:
    identities = [
        migration.identity
        for migration in discover_migrations(ROOT / "migrations")
    ]

    assert identities.index(
        "215_agent_runtime_model_event_projection_rpcs.sql"
    ) < identities.index("216_configuration_admin_test_bundle.sql")
