"""Migration 220 Runtime manual-memory capability contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/220_runtime_manual_memory_capabilities.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations/rollback/"
    "220_runtime_manual_memory_capabilities_rollback.sql"
).read_text(encoding="utf-8")

CAPABILITIES = (
    "runtime_create_manual_memory",
    "runtime_update_manual_memory",
    "runtime_delete_memory_atom",
    "runtime_clear_memory_atoms",
)


def test_capabilities_are_owner_definers_with_runtime_scope_guard() -> None:
    assert "session_user <> 'everydayai_runtime'" in SQL
    assert "tenant_actor_user_id() IS DISTINCT FROM p_user_id" in SQL
    assert "tenant_org_id() IS DISTINCT FROM p_org_id" in SQL
    assert "status = 'active'" in SQL
    for name in CAPABILITIES:
        body = re.search(
            rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;",
            SQL,
            re.DOTALL,
        )
        assert body
        assert "SECURITY DEFINER" in body.group(0)
        assert "_assert_runtime_manual_memory_scope" in body.group(0)


def test_only_web_runtime_can_execute_public_capabilities() -> None:
    grant = SQL[SQL.index("GRANT EXECUTE ON FUNCTION"):]
    assert "TO everydayai_runtime;" in grant
    assert "TO everydayai_worker" not in grant
    assert "TO everydayai_wecom_runtime" not in grant
    for name in CAPABILITIES:
        assert name in grant


def test_legacy_capabilities_are_not_replaced_or_dropped() -> None:
    assert "CREATE OR REPLACE FUNCTION create_manual_memory(" not in SQL
    assert "DROP FUNCTION" not in SQL
    for name in CAPABILITIES:
        assert f"DROP FUNCTION IF EXISTS {name}" in ROLLBACK
