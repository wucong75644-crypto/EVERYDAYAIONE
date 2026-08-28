from pathlib import Path


MIGRATION = (
    Path(__file__).parent.parent
    / "migrations"
    / "236_restore_legacy_admin_user_assets.sql"
)


def test_legacy_asset_rpc_does_not_require_runtime_tenant_helpers() -> None:
    sql = MIGRATION.read_text()

    assert "CREATE FUNCTION public.list_platform_admin_user_assets" in sql
    assert "CREATE FUNCTION public.resolve_platform_admin_user_assets_download" in sql
    assert "tenant_platform_admin" not in sql
    assert "tenant_actor_user_id" not in sql
    assert "TO everydayai;" in sql


def test_legacy_asset_download_keeps_user_scope_and_ready_state() -> None:
    sql = MIGRATION.read_text()

    assert "asset.status = 'ready'" in sql
    assert "asset_ref.actor_user_id = p_actor_user_id" in sql
    assert "ADMIN_ASSET_DOWNLOAD_SCOPE_INVALID" in sql
