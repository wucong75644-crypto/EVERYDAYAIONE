"""Migration 220 platform-admin credit adjustment capability contract."""

from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/220_platform_admin_credit_adjustment.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/220_platform_admin_credit_adjustment_rollback.sql"
)
TRANSFER = (
    ROOT.parent / "deploy/transfer-admin-credit-adjustment-ownership.sh"
)
TRANSFER_ROLLBACK = (
    ROOT.parent / "deploy/rollback-admin-credit-adjustment-ownership.sh"
)
RUN_MIGRATIONS = ROOT.parent / "deploy/run-migrations.sh"
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")
TRANSFER_SQL = TRANSFER.read_text(encoding="utf-8")
TRANSFER_ROLLBACK_SQL = TRANSFER_ROLLBACK.read_text(encoding="utf-8")
RUN_MIGRATIONS_SQL = RUN_MIGRATIONS.read_text(encoding="utf-8")
SIGNATURE = "admin_adjust_credits(\n    UUID, INTEGER, TEXT, UUID, UUID\n)"


def test_credit_adjustment_fails_closed_to_verified_platform_admin() -> None:
    for requirement in (
        "SECURITY DEFINER",
        "SET search_path = pg_catalog, public",
        "session_user <> 'everydayai_runtime'",
        "current_setting('app.access_kind', TRUE) <> 'runtime'",
        "NOT public.tenant_platform_admin()",
        "p_operator_id IS DISTINCT FROM v_actor_user_id",
        "ERRCODE = '42501'",
    ):
        assert requirement in SQL


def test_credit_adjustment_is_atomic_and_preserves_reason() -> None:
    assert "credits + p_delta >= 0" in SQL
    assert "RETURNING credits INTO v_new_balance" in SQL
    assert "INSERT INTO public.credits_history" in SQL
    assert "p_reason, v_actor_user_id, p_org_id" in SQL
    assert SQL.index("UPDATE public.users") < SQL.index(
        "INSERT INTO public.credits_history"
    )


def test_credit_adjustment_acl_is_runtime_only() -> None:
    assert f"REVOKE ALL ON FUNCTION {SIGNATURE}" in SQL
    for role in (
        "PUBLIC",
        "everydayai_wecom_runtime",
        "everydayai_worker",
        "everydayai_sync",
        "everydayai",
        "service_role",
    ):
        assert role in SQL
    assert f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO everydayai_runtime;" in SQL


def test_admin_ownership_transfer_is_exact_idempotent_and_reversible() -> None:
    signature = "public.admin_adjust_credits(uuid,integer,text,uuid,uuid)"
    assert signature in TRANSFER_SQL
    assert "OWNER TO everydayai_owner" in TRANSFER_SQL
    assert "NOT IN ('everydayai', 'everydayai_owner')" in TRANSFER_SQL
    assert "FROM PUBLIC, everydayai_runtime" in TRANSFER_SQL
    assert "ADMIN_CREDIT_OWNERSHIP_EXECUTE_NOT_CLOSED" in TRANSFER_SQL
    assert "CONFIRM_ADMIN_CREDIT_OWNER_ROLLBACK" in TRANSFER_ROLLBACK_SQL
    assert "procedure.proconfig @> ARRAY['search_path=public']" in (
        TRANSFER_ROLLBACK_SQL
    )
    assert "OWNER TO everydayai;" in TRANSFER_ROLLBACK_SQL


def test_migration_runner_closes_owner_gap_before_applying_220() -> None:
    assert "'220_platform_admin_credit_adjustment.sql'" in RUN_MIGRATIONS_SQL
    assert "sudo -n -u postgres env" in RUN_MIGRATIONS_SQL
    assert '"PATH=${migration_python_dir}:/usr/local/bin:/usr/bin:/bin"' in (
        RUN_MIGRATIONS_SQL
    )
    assert "transfer-admin-credit-adjustment-ownership.sh" in RUN_MIGRATIONS_SQL
    assert RUN_MIGRATIONS_SQL.index(
        "transfer-admin-credit-adjustment-ownership.sh"
    ) < RUN_MIGRATIONS_SQL.index(
        '"$migration_python" scripts/migration_runner.py apply'
    )


def test_rollback_restores_migration_115_contract() -> None:
    assert "SET search_path = public" in ROLLBACK_SQL
    assert "GRANT EXECUTE ON FUNCTION" in ROLLBACK_SQL
    assert ") TO everydayai_runtime;" in ROLLBACK_SQL
    assert ") TO PUBLIC;" not in ROLLBACK_SQL
    assert "tenant_platform_admin()" not in ROLLBACK_SQL


def test_migration_runner_discovers_credit_adjustment_after_current_head() -> None:
    migrations = discover_migrations(ROOT / "migrations")
    identities = [migration.identity for migration in migrations]
    assert identities.index(
        "219_sync_wecom_employee_capability_access.sql"
    ) < identities.index("220_platform_admin_credit_adjustment.sql")
    migration = next(
        item
        for item in migrations
        if item.identity == "220_platform_admin_credit_adjustment.sql"
    )
    assert (
        migration.rollback_identity
        == "220_platform_admin_credit_adjustment_rollback.sql"
    )
