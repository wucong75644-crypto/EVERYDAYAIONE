"""管理员资产数据库治理门禁合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "deploy/preflight/admin-user-assets-capability.sh"
).read_text(encoding="utf-8")


def test_preflight_checks_signature_owner_security_and_exact_acl() -> None:
    assert "209_platform_admin_user_assets_capability.sql" in SCRIPT
    assert "list_platform_admin_user_assets(uuid,text,text,integer," in SCRIPT
    assert "owner_role.rolname <> 'everydayai_owner'" in SCRIPT
    assert "NOT procedure.prosecdef" in SCRIPT
    assert "ARRAY['search_path=pg_catalog, public']" in SCRIPT
    assert "acl.grantee NOT IN" in SCRIPT
    assert "'service_role', procedure.oid, 'EXECUTE'" in SCRIPT
    assert "'everydayai', procedure.oid, 'EXECUTE'" in SCRIPT
    assert "acl.is_grantable" in SCRIPT
    assert "ADMIN_ASSET_CAPABILITY_INVALID" in SCRIPT
    assert "resolve_platform_admin_user_assets_download(uuid,jsonb)" in SCRIPT
    assert "ADMIN_ASSET_DOWNLOAD_CAPABILITY_INVALID" in SCRIPT
    assert "_list_admin_user_assets_owner(uuid,text,text,integer," in SCRIPT
    assert "acl.grantee <> procedure.proowner" in SCRIPT
    assert "ADMIN_ASSET_OWNER_CORE_INVALID" in SCRIPT


def test_preflight_rejects_runtime_table_access() -> None:
    assert "ARRAY['user_assets', 'user_asset_refs']" in SCRIPT
    assert "'SELECT, INSERT, UPDATE, DELETE'" in SCRIPT
    assert "has_any_column_privilege(" in SCRIPT
    assert "'SELECT, INSERT, UPDATE'" in SCRIPT
    assert "ADMIN_ASSET_DIRECT_ACCESS_INVALID" in SCRIPT
    assert "SET TRANSACTION READ ONLY;" in SCRIPT
    assert "ROLLBACK;" in SCRIPT
