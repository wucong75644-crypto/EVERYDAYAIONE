from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/230_07_agent_runtime_media_production_activation.sql"
ROLLBACK = ROOT / "migrations/rollback/230_07_agent_runtime_media_production_activation_rollback.sql"


def test_media_activation_is_atomic_and_audited() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "set_agent_runtime_definition_ingress_enabled" in sql
    assert "FOR UPDATE" in sql
    assert "state_version<>p_expected_state_version" in sql
    assert "set_media_production_state" in sql
    assert "agent_runtime_admin_audit" in sql
    assert "p_image_ingress_enabled AND NOT p_production_ready" in sql


def test_media_activation_context_and_rollback_are_runtime_admin_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "session_user<>'everydayai_runtime_admin'" in sql
    assert "TO everydayai_runtime_admin" in sql
    assert "DROP FUNCTION IF EXISTS set_agent_runtime_media_production_state_v1" in rollback
    assert "DROP FUNCTION IF EXISTS get_agent_runtime_media_admin_context_v1" in rollback
