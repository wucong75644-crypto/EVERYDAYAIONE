from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "126_wecom_conversation_settings.sql"
ROLLBACK = MIGRATIONS / "rollback" / "126_wecom_conversation_settings_rollback.sql"


def test_setting_rpc_is_scoped_locked_and_merges_jsonb():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "FOR UPDATE" in sql
    assert "user_id = p_user_id" in sql
    assert "org_id IS NOT DISTINCT FROM p_org_id" in sql
    assert "source = 'wecom'" in sql
    assert "jsonb_set(" in sql
    assert "p_setting_key NOT IN ('model', 'thinking_mode')" in sql
    assert "REVOKE ALL ON FUNCTION update_wecom_conversation_setting" in sql


def test_setting_rpc_rollback_removes_function():
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "DROP FUNCTION IF EXISTS update_wecom_conversation_setting" in sql
