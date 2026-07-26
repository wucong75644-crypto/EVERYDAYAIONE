from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND_DIR / "migrations" / "200_web_wecom_control_capabilities.sql"
ROLLBACK = (
    BACKEND_DIR / "migrations" / "rollback"
    / "200_web_wecom_control_capabilities_rollback.sql"
)


def test_web_wecom_control_uses_four_narrow_capabilities() -> None:
    sql = MIGRATION.read_text()
    for name in (
        "list_runtime_wecom_chat_targets",
        "list_governed_wecom_chat_targets",
        "update_governed_wecom_chat_target_name",
        "resolve_governed_wecom_push_target",
        "is_runtime_wecom_self_target",
    ):
        assert f"CREATE OR REPLACE FUNCTION {name}" in sql
    assert "TO everydayai_runtime" in sql
    assert "everydayai_wecom_runtime" in sql


def test_governed_mutations_and_push_require_admin_authority() -> None:
    sql = MIGRATION.read_text()
    assert sql.count("ARRAY['owner', 'admin']") == 3
    assert "WHERE id = p_target_id" in sql
    assert "AND org_id = p_org_id" in sql
    assert "mapping.org_id = p_org_id" in sql
    assert "member.status = 'active'" in sql


def test_rollback_drops_all_capabilities() -> None:
    sql = ROLLBACK.read_text()
    assert sql.count("DROP FUNCTION IF EXISTS") == 5
