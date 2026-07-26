"""Migration 192 atomic organization initialization contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "migrations/192_atomic_organization_permission_initialization.sql"
)
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "192_atomic_organization_permission_initialization_rollback.sql"
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


def test_creation_initializes_every_blueprint_part_in_one_function() -> None:
    body = _function_body("create_governed_organization")
    for operation in (
        "INSERT INTO public.organizations",
        "INSERT INTO public.org_members",
        "_initialize_governed_org_positions",
        "_initialize_governed_org_roles",
        "_initialize_governed_org_structure",
        "_record_governance_audit",
    ):
        assert operation in body
    assert "\nEXCEPTION\n" not in body


def test_blueprint_matches_application_constants() -> None:
    assert {
        "boss", "vp", "manager", "deputy", "member"
    }.issubset(set(re.findall(r"'([a-z_]+)'", _function_body(
        "_initialize_governed_org_positions"
    ))))
    for code in (
        "role_ops", "role_finance", "role_warehouse", "role_service",
        "role_design", "role_hr", "role_boss_full", "role_vp_full",
    ):
        assert f"'{code}'" in SQL
    for department_type in (
        "ops", "finance", "warehouse", "service", "design", "hr",
    ):
        assert f"'{department_type}'" in SQL
    assert "v_expected_permissions CONSTANT INTEGER := 23" in SQL
    assert "'task.push_to_others'" in SQL


def test_private_helpers_are_not_executable_by_service_roles() -> None:
    revoke = re.search(
        r"REVOKE ALL ON FUNCTION\s+(?P<body>.*?)\nFROM PUBLIC, "
        r"everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;",
        SQL,
        re.DOTALL,
    )
    assert revoke
    for helper in (
        "_initialize_governed_org_positions",
        "_initialize_governed_org_roles",
        "_initialize_governed_org_structure",
    ):
        assert helper in revoke.group("body")
        assert "SECURITY INVOKER" in _function_body(helper)
    assert (
        "GRANT EXECUTE ON FUNCTION create_governed_organization(TEXT, UUID)\n"
        "TO everydayai_runtime;"
    ) in SQL


def test_rollback_restores_pre_192_creation_and_drops_helpers() -> None:
    assert "_initialize_governed_org_positions(v_org.id)" not in ROLLBACK_SQL
    for helper in (
        "_initialize_governed_org_positions(UUID)",
        "_initialize_governed_org_roles(UUID)",
        "_initialize_governed_org_structure(UUID, UUID)",
    ):
        assert f"DROP FUNCTION IF EXISTS {helper}" in ROLLBACK_SQL
