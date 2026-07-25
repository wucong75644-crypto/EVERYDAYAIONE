from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "backend/migrations/189_web_runtime_access_completion.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/189_web_runtime_access_completion_rollback.sql"
).read_text(encoding="utf-8")


def test_platform_admin_identity_is_database_verified() -> None:
    body = re.search(
        r"CREATE OR REPLACE FUNCTION tenant_platform_admin\(\)"
        r"(?P<body>[\s\S]+?)\n\$\$;",
        MIGRATION,
    )
    assert body is not None
    sql = body.group("body")
    assert "session_user = 'everydayai_runtime'" in sql
    assert "app.access_kind" in sql
    assert "tenant_actor_user_id()" in sql
    assert "role::TEXT = 'super_admin'" in sql
    assert "status::TEXT = 'active'" in sql
    assert "SECURITY DEFINER" in sql


def test_platform_admin_is_read_only_on_business_tables() -> None:
    policies = re.findall(
        r"CREATE POLICY platform_admin_[a-z_]+ ON ([a-z_]+)"
        r"\nFOR ([A-Z]+) TO everydayai_runtime",
        MIGRATION,
    )
    business_policies = {
        table: command for table, command in policies if table != "error_logs"
    }
    assert business_policies
    assert set(business_policies.values()) == {"SELECT"}
    assert "FOR ALL TO everydayai_runtime" not in MIGRATION


def test_public_org_name_uses_narrow_runtime_capability() -> None:
    function = re.search(
        r"CREATE OR REPLACE FUNCTION get_public_organization_name"
        r"\(p_org_id UUID\)(?P<body>[\s\S]+?)\n\$\$;",
        MIGRATION,
    )
    assert function is not None
    body = function.group("body")
    assert "SECURITY DEFINER" in body
    assert "session_user <> 'everydayai_runtime'" in body
    assert "app.access_kind" in body
    assert "'name', organization.name" in body
    assert "'status', organization.status" in body
    assert "TO everydayai_runtime;" in MIGRATION
    assert "FROM PUBLIC, everydayai_wecom_runtime" in MIGRATION


def test_web_acl_matches_runtime_contract_and_keeps_sensitive_tables_closed() -> None:
    assert "GRANT SELECT, UPDATE ON users TO everydayai_runtime;" in MIGRATION
    assert (
        "conversations, messages, tasks, detail_projects, detail_project_images,"
        in MIGRATION
    )
    assert "refresh_tokens" not in MIGRATION.split(
        "GRANT SELECT, UPDATE ON users", 1,
    )[1]
    assert "TO everydayai_wecom_runtime" not in MIGRATION
    assert "TO everydayai_worker" not in MIGRATION


def test_rollback_removes_capability_policies_and_runtime_acl() -> None:
    assert "DROP FUNCTION IF EXISTS tenant_platform_admin();" in ROLLBACK
    assert "platform_admin_users_select" in ROLLBACK
    assert "FROM everydayai_runtime;" in ROLLBACK
