"""Migration 157 atomic governance write capability contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/157_governance_write_capabilities.sql"
ROLLBACK = (
    ROOT / "migrations/rollback/157_governance_write_capabilities_rollback.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
ROLLBACK_SQL = ROLLBACK.read_text(encoding="utf-8")

WRITE_FUNCTIONS = (
    "create_governed_organization",
    "update_governed_organization",
    "add_governed_member",
    "remove_governed_member",
    "change_governed_member_role",
    "create_governed_invitation",
    "accept_governed_invitation",
)


def _function_body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION {name}\b.*?\n\$\$;(?:\n|$)",
        SQL,
        re.DOTALL,
    )
    assert match, f"missing function {name}"
    return match.group(0)


def test_write_facades_are_owner_definers_and_web_runtime_only() -> None:
    grant = re.search(
        r"GRANT EXECUTE ON FUNCTION (?P<body>.*?)\nTO everydayai_runtime;",
        SQL,
        re.DOTALL,
    )
    assert grant
    for name in WRITE_FUNCTIONS:
        body = _function_body(name)
        assert "SECURITY DEFINER" in body
        assert "SET search_path = pg_catalog, public" in body
        assert name in grant.group("body")
    assert "TO everydayai_wecom_runtime" not in grant.group(0)
    assert "TO everydayai_worker" not in grant.group(0)


def test_every_administrative_write_records_audit_in_same_function() -> None:
    for name in WRITE_FUNCTIONS[:-1]:
        assert "_record_governance_audit" in _function_body(name)
    accept = _function_body("accept_governed_invitation")
    assert "'self'" in accept
    assert "_record_governance_audit" in accept
    assert "'config_value'" not in SQL
    assert "'phone'" not in re.search(
        r"_record_governance_audit\(.*?\);",
        _function_body("create_governed_invitation"),
        re.DOTALL,
    ).group(0)


def test_member_limit_and_invitation_acceptance_lock_org_row() -> None:
    for name in ("add_governed_member", "accept_governed_invitation"):
        body = _function_body(name)
        assert "FOR UPDATE" in body
        assert "max_members" in body
        assert "GOVERNANCE_MEMBER_LIMIT_REACHED" in body


def test_member_role_invariants_are_enforced() -> None:
    remove = _function_body("remove_governed_member")
    assert "GOVERNANCE_SELF_MUTATION_DENIED" in remove
    assert "v_target_role = 'owner'" in remove
    assert "v_authority = 'admin' AND v_target_role = 'admin'" in remove
    change = _function_body("change_governed_member_role")
    assert "ARRAY['owner']" in change
    assert "v_previous_role = 'owner'" in change


def test_invitation_creation_serializes_phone_per_org() -> None:
    body = _function_body("create_governed_invitation")
    assert "pg_advisory_xact_lock" in body
    assert "status = 'pending'" in body
    assert "GOVERNANCE_INVITATION_CONFLICT" in body


def test_organization_responses_exclude_secret_material() -> None:
    for name in (
        "create_governed_organization",
        "update_governed_organization",
    ):
        body = _function_body(name)
        assert "- 'encrypt_key'" in body
        assert "- 'wecom_secret_encrypted'" in body


def test_super_admin_org_update_still_uses_shared_active_org_guard() -> None:
    update = _function_body("update_governed_organization")
    assert (
        "_assert_governance_authority(\n"
        "        p_org_id, ARRAY['owner', 'admin'], TRUE"
    ) in update


def test_rollback_removes_every_write_and_restores_audit_authority() -> None:
    for name in WRITE_FUNCTIONS:
        assert f"DROP FUNCTION IF EXISTS {name}" in ROLLBACK_SQL
    assert "CREATE OR REPLACE FUNCTION _record_governance_audit" in ROLLBACK_SQL
    assert "DROP CONSTRAINT governance_audit_log_authority_check" not in ROLLBACK_SQL
