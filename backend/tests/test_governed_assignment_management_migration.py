"""Migration 194 governed assignment management contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/194_governed_assignment_management.sql"
ROLLBACK = (
    ROOT / "migrations/rollback/"
    "194_governed_assignment_management_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_management_facades_are_owner_definers_and_runtime_only() -> None:
    for name in (
        "list_governed_member_assignments",
        "list_governed_wecom_assignments",
        "update_governed_member_assignment",
    ):
        body = _function_body(name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
    grant = re.search(
        r"GRANT EXECUTE ON FUNCTION(?P<body>.*?)\nTO everydayai_runtime;",
        SQL,
        re.DOTALL,
    )
    assert grant
    assert "TO everydayai_worker" not in grant.group(0)
    assert "TO everydayai_wecom_runtime" not in grant.group(0)


def test_assignment_update_enforces_role_and_tenant_invariants() -> None:
    validation = _function_body("_validate_governed_assignment_change")
    assert "ARRAY['owner', 'admin']" in validation
    assert "v_target_role IN ('owner', 'admin')" in validation
    assert "p_target_user_id <> v_owner_id" in validation
    assert "v_position_code IN ('boss', 'vp')" in validation
    assert "org_id = p_org_id" in validation
    assert "jsonb_array_length" in validation


def test_assignment_update_is_atomic_and_audited() -> None:
    body = _function_body("update_governed_member_assignment")
    assert "FOR UPDATE" in body
    assert "perm_version = perm_version + 1" in body
    assert "_record_governance_audit" in body
    assert "'member.assignment_update'" in body
    assert "\nEXCEPTION\n" in body


def test_rollback_drops_public_and_private_functions() -> None:
    for name in (
        "update_governed_member_assignment",
        "_validate_governed_assignment_change",
        "list_governed_wecom_assignments",
        "list_governed_member_assignments",
    ):
        assert f"DROP FUNCTION IF EXISTS {name}" in ROLLBACK_SQL
