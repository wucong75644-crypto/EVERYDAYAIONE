"""管理员资产旧查询函数 owner 转移脚本合同测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/transfer-admin-user-assets-ownership.sh"


def test_transfer_is_narrow_and_fails_closed() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "ADMIN_ASSET_OWNER_TRANSFER_REQUIRES_ADMIN" in script
    assert "ADMIN_ASSET_OWNER_TRANSFER_FUNCTION_MISSING" in script
    assert "ADMIN_ASSET_OWNER_TRANSFER_OWNER_UNEXPECTED" in script
    assert script.count("ALTER FUNCTION public.list_admin_user_assets(") == 1
    assert "ALTER TABLE" not in script
    assert "GRANT " not in script
    assert "REVOKE " not in script


def test_transfer_uses_admin_transport_and_transaction() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "BEGIN;" in script
    assert "COMMIT;" in script
    assert 'run-psql-admin.py"' in script
    assert "--no-psqlrc --set=ON_ERROR_STOP=1" in script
