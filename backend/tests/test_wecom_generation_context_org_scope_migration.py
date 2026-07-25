"""迁移 169 与 OrgScopedDB RPC 参数适配合同。"""

from pathlib import Path
from unittest.mock import MagicMock

from core.org_scoped_db import OrgScopedDB


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/169_wecom_generation_context_org_scope.sql"
).read_text()
ROLLBACK = (
    ROOT / "migrations/rollback"
    / "169_wecom_generation_context_org_scope_rollback.sql"
).read_text()


def test_org_scoped_db_calls_three_argument_overload() -> None:
    raw_db = MagicMock()
    scoped = OrgScopedDB(raw_db, "org-1")

    scoped.rpc("get_wecom_generation_context", {
        "p_user_id": "user-1",
        "p_conversation_id": None,
    })

    raw_db.rpc.assert_called_once_with(
        "get_wecom_generation_context",
        {
            "p_user_id": "user-1",
            "p_conversation_id": None,
            "p_org_id": "org-1",
        },
    )


def test_overload_validates_org_before_delegating() -> None:
    assert "get_wecom_generation_context(" in SQL
    assert "p_org_id UUID" in SQL
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in SQL
    assert SQL.index("ORG_SCOPE_MISMATCH") < SQL.index(
        "RETURN public.get_wecom_generation_context"
    )
    assert "SECURITY DEFINER" in SQL
    assert "SET search_path = pg_catalog, public" in SQL


def test_only_wecom_runtime_receives_overload() -> None:
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "get_wecom_generation_context(UUID, UUID, UUID)\n"
        "TO everydayai_wecom_runtime;"
    ) in SQL
    assert "DROP FUNCTION get_wecom_generation_context(UUID, UUID, UUID)" in ROLLBACK
