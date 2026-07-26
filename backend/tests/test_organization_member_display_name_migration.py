"""Migrations 194-195 enterprise-local display-name contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGEMENT = (
    ROOT / "migrations/194_governed_assignment_management.sql"
).read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "migrations/195_organization_member_display_name.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations/rollback/"
    "195_organization_member_display_name_rollback.sql"
).read_text(encoding="utf-8")


def test_display_name_is_enterprise_local_and_used_by_aggregates() -> None:
    assert "ALTER TABLE org_members" in MANAGEMENT
    assert "ADD COLUMN display_name VARCHAR(50)" in MANAGEMENT
    assert "member.display_name, account.nickname" in MANAGEMENT
    assert "UPDATE public.org_members" in MIGRATION
    assert "UPDATE public.users" not in MIGRATION


def test_update_requires_governance_authority_and_audit() -> None:
    assert "ARRAY['owner', 'admin']" in MIGRATION
    assert "v_target_role IN ('owner', 'admin')" in MIGRATION
    assert "'member.display_name_update'" in MIGRATION
    assert "_record_governance_audit" in MIGRATION


def test_only_runtime_can_update_and_rollback_preserves_member_data() -> None:
    assert "TO everydayai_runtime;" in MIGRATION
    assert "TO everydayai_wecom_runtime" not in MIGRATION
    assert "TO everydayai_worker" not in MIGRATION
    assert "DROP FUNCTION IF EXISTS update_governed_member_display_name" in ROLLBACK
    assert "DROP COLUMN" not in ROLLBACK
