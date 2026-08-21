from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "235_restore_legacy_admin_credit_adjustment.sql"
)


def test_legacy_admin_credit_rpc_does_not_depend_on_runtime_tenant_helpers() -> None:
    sql = MIGRATION.read_text()
    function_body = sql.split("AS $$", 1)[1].split("$$;", 1)[0]

    assert "CREATE OR REPLACE FUNCTION public.admin_adjust_credits" in sql
    assert "SECURITY DEFINER" in sql
    assert "tenant_platform_admin" not in function_body
    assert "tenant_actor_user_id" not in function_body
    assert "credits + p_delta >= 0" in sql
    assert "'admin_adjust'::public.credits_change_type" in sql


def test_legacy_admin_credit_rpc_is_only_granted_to_legacy_backend_role() -> None:
    sql = MIGRATION.read_text()

    assert "REVOKE ALL ON FUNCTION public.admin_adjust_credits" in sql
    assert "GRANT EXECUTE ON FUNCTION public.admin_adjust_credits" in sql
    assert "TO everydayai;" in sql
