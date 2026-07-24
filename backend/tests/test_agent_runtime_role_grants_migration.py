"""Agent Runtime 首组数据库角色授权迁移静态合同。"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/151_agent_runtime_role_grants.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/151_agent_runtime_role_grants_rollback.sql"
).read_text()

TENANT_HELPERS = {
    "tenant_actor_user_id()",
    "tenant_org_id()",
    "tenant_database_role_matches_scope()",
    "tenant_actor_is_active_member(UUID)",
    "tenant_user_fact_visible(UUID, UUID)",
    "tenant_conversation_visible(UUID, UUID)",
    "tenant_task_visible(UUID, UUID)",
    "tenant_asset_visible(UUID, TEXT, TEXT)",
    "tenant_asset_ref_visible(UUID)",
}
FIRST_RUNTIME_GROUP = {
    "conversation_artifacts",
    "conversation_attachment_refs",
    "conversation_channel_bindings",
    "conversation_compactions",
    "conversation_context_items",
    "conversation_context_receipts",
    "conversation_data_evidence",
    "message_generation_requests",
    "task_attachment_refs",
    "memory_atoms",
    "user_assets",
    "user_asset_refs",
    "user_activity_events",
}


def test_migration_runs_as_owner_and_defers_service_cutover() -> None:
    assert "SET LOCAL ROLE everydayai_owner;" in SQL
    assert SQL.rstrip().endswith("RESET ROLE;")
    assert "完整 RPC 权限清单由任务 5.3b 补齐" in SQL


def test_only_asset_policies_include_owner_for_security_definer_chain() -> None:
    owner_policies = set(
        re.findall(
            r"ALTER POLICY (tenant_[a-z_]+)[\s\S]+?"
            r"TO everydayai_owner, everydayai_runtime, everydayai_worker;",
            SQL,
        )
    )

    assert owner_policies == {"tenant_user_assets", "tenant_user_asset_refs"}


def test_runtime_and_worker_never_receive_delete_or_asset_table_access() -> None:
    grants = SQL[SQL.index("GRANT SELECT ON TABLE"):SQL.index(
        "GRANT EXECUTE ON FUNCTION tenant_actor_user_id()"
    )]

    assert "GRANT DELETE" not in grants
    assert "SELECT, INSERT, UPDATE, DELETE" not in grants
    assert "user_assets" not in grants
    assert "user_asset_refs" not in grants


def test_rollback_revokes_exact_first_runtime_group() -> None:
    table_block = ROLLBACK[
        ROLLBACK.index("REVOKE ALL ON TABLE"):
        ROLLBACK.index("FROM everydayai_runtime, everydayai_worker;")
    ]
    revoked_tables = {
        name
        for name in re.findall(r"^\s+([a-z_]+),?$", table_block, re.MULTILINE)
    }

    assert revoked_tables == FIRST_RUNTIME_GROUP


def test_runtime_and_worker_receive_required_direct_operations() -> None:
    assert "message_generation_requests,\n    memory_atoms\nTO everydayai_runtime;" in SQL
    assert "user_activity_events\nTO everydayai_runtime;" in SQL
    assert "conversation_context_receipts,\n    conversation_data_evidence" in SQL
    assert "conversation_context_receipts,\n    memory_atoms\nTO everydayai_worker;" in SQL


def test_all_tenant_helpers_are_executable_by_both_roles() -> None:
    for helper in TENANT_HELPERS:
        assert (
            f"GRANT EXECUTE ON FUNCTION {helper}\n"
            "TO everydayai_runtime, everydayai_worker;"
        ) in SQL


def test_only_public_asset_entrypoint_is_granted() -> None:
    assert "GRANT EXECUTE ON FUNCTION register_user_asset(" in SQL
    assert "GRANT EXECUTE ON FUNCTION _resolve_user_asset(" not in SQL
    assert "GRANT EXECUTE ON FUNCTION _bind_user_asset_ref(" not in SQL
    assert "TO PUBLIC" not in SQL


def test_rollback_revokes_tables_functions_and_owner_policy_scope() -> None:
    assert "REVOKE ALL ON TABLE" in ROLLBACK
    for helper in TENANT_HELPERS:
        assert f"REVOKE EXECUTE ON FUNCTION {helper}" in ROLLBACK
    assert "REVOKE EXECUTE ON FUNCTION register_user_asset(" in ROLLBACK
    assert ROLLBACK.count(
        "TO everydayai_runtime, everydayai_worker;"
    ) == 2
    assert ROLLBACK.rstrip().endswith("RESET ROLE;")
