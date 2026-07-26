"""Contract tests for request-scoped organization authority."""

from pathlib import Path


ROOT = Path(__file__).parent.parent
MIGRATION = ROOT / "migrations/191_governance_actor_authority.sql"
ROLLBACK = (
    ROOT / "migrations/rollback/191_governance_actor_authority_rollback.sql"
)


def test_authority_capability_is_runtime_only_security_definer() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE FUNCTION get_governed_actor_authority(p_org_id UUID)" in sql
    assert "SECURITY DEFINER" in sql
    assert "_assert_governance_authority(" in sql
    assert "TO everydayai_runtime;" in sql
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in sql


def test_rollback_removes_authority_capability() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "REVOKE EXECUTE ON FUNCTION get_governed_actor_authority(UUID)" in sql
    assert "DROP FUNCTION get_governed_actor_authority(UUID);" in sql
