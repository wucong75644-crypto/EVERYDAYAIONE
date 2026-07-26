"""Migration 193 Runtime assignment read capability contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/193_runtime_assignment_read_capabilities.sql"
ROLLBACK = (
    ROOT / "migrations/rollback/"
    "193_runtime_assignment_read_capabilities_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")

FUNCTIONS = (
    "get_runtime_member_assignment",
    "list_runtime_member_assignments",
    "list_runtime_org_departments",
    "list_runtime_org_positions",
    "list_runtime_department_user_ids",
)


def _function_body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_every_read_is_owner_definer_and_checks_governance_scope() -> None:
    for name in FUNCTIONS:
        body = _function_body(name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
        assert "_assert_governance_authority" in body


def test_assignment_reads_require_active_target_members_and_same_org_joins() -> None:
    for name in (
        "get_runtime_member_assignment",
        "list_runtime_member_assignments",
        "list_runtime_department_user_ids",
    ):
        body = _function_body(name)
        assert "member.status = 'active'" in body
        assert "assignment.org_id = p_org_id" in body
    assert "cardinality(p_user_ids) > 500" in SQL
    assert "cardinality(p_department_ids) > 100" in SQL


def test_only_web_runtime_receives_public_execute() -> None:
    grant = re.search(
        r"GRANT EXECUTE ON FUNCTION(?P<body>.*?)\nTO everydayai_runtime;",
        SQL,
        re.DOTALL,
    )
    assert grant
    for name in FUNCTIONS:
        assert name in grant.group("body")
    assert "TO everydayai_wecom_runtime" not in grant.group(0)
    assert "TO everydayai_worker" not in grant.group(0)


def test_rollback_drops_every_capability() -> None:
    for name in FUNCTIONS:
        assert f"DROP FUNCTION IF EXISTS {name}" in ROLLBACK_SQL
