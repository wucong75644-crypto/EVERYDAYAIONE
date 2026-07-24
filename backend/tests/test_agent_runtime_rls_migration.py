"""Agent Runtime 首组 RLS 迁移静态合同。"""

from pathlib import Path
import re

from core.tenant_registry import TENANT_TABLE_REGISTRY
from tests.test_tenant_registry_contract import FIRST_RUNTIME_GROUP


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/150_agent_runtime_tenant_defense.sql").read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/150_agent_runtime_tenant_defense_rollback.sql"
).read_text()


def test_migration_policy_set_matches_registry_runtime_group() -> None:
    policy_tables = set(re.findall(r"CREATE POLICY tenant_[a-z_]+\s+ON ([a-z_]+)", SQL))

    assert policy_tables == FIRST_RUNTIME_GROUP
    assert policy_tables <= TENANT_TABLE_REGISTRY.keys()


def test_every_policy_has_using_and_with_check() -> None:
    policies = re.findall(
        r"CREATE POLICY tenant_[a-z_]+\s+ON [\s\S]+?;",
        SQL,
    )

    assert len(policies) == len(FIRST_RUNTIME_GROUP)
    for policy in policies:
        assert "USING (" in policy
        assert "WITH CHECK (" in policy
        assert "everydayai_runtime, everydayai_worker" in policy


def test_direct_user_facts_require_actor_identity() -> None:
    helper = SQL[
        SQL.index("CREATE OR REPLACE FUNCTION tenant_user_fact_visible"):
        SQL.index("CREATE OR REPLACE FUNCTION tenant_conversation_visible")
    ]

    assert "p_user_id = tenant_actor_user_id()" in helper
    assert "p_org_id = tenant_org_id()" in helper
    assert "tenant_actor_is_active_member(p_org_id)" in helper


def test_conversation_policy_separates_user_and_channel_scope() -> None:
    helper = SQL[
        SQL.index("CREATE OR REPLACE FUNCTION tenant_conversation_visible"):
        SQL.index("CREATE OR REPLACE FUNCTION tenant_task_visible")
    ]

    assert "conversation.scope_type = 'user'" in helper
    assert "conversation.user_id = tenant_actor_user_id()" in helper
    assert "conversation.scope_type = 'channel'" in helper
    assert "tenant_actor_is_active_member(conversation.org_id)" in helper


def test_database_role_must_match_access_kind() -> None:
    assert "SELECT CASE session_user" in SQL
    assert "SELECT CASE current_user" not in SQL
    assert "WHEN 'everydayai_runtime'" in SQL
    assert "current_setting('app.access_kind', TRUE) = 'runtime'" in SQL
    assert "WHEN 'everydayai_worker'" in SQL
    assert "current_setting('app.access_kind', TRUE) = 'worker'" in SQL


def test_disabled_or_suspended_enterprise_fails_membership() -> None:
    assert "member.status = 'active'" in SQL
    assert "organization.status = 'active'" in SQL


def test_asset_policy_separates_user_and_channel_storage() -> None:
    assert "p_storage_scope = 'user'" in SQL
    assert "p_storage_owner_key = tenant_actor_user_id()::TEXT" in SQL
    assert "p_storage_scope = 'channel'" in SQL
    assert "tenant_asset_ref_visible(asset_id)" in SQL


def test_helpers_are_owner_controlled_and_not_public() -> None:
    assert SQL.startswith("-- 150:")
    assert "SET LOCAL ROLE everydayai_owner;" in SQL
    assert SQL.rstrip().endswith("RESET ROLE;")
    for function in (
        "tenant_actor_user_id()",
        "tenant_org_id()",
        "tenant_database_role_matches_scope()",
        "tenant_actor_is_active_member(UUID)",
        "tenant_user_fact_visible(UUID, UUID)",
        "tenant_conversation_visible(UUID, UUID)",
        "tenant_task_visible(UUID, UUID)",
        "tenant_asset_visible(UUID, TEXT, TEXT)",
        "tenant_asset_ref_visible(UUID)",
    ):
        assert f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC;" in SQL


def test_force_rls_is_deferred_to_separate_cutover() -> None:
    assert "FORCE ROW LEVEL SECURITY" not in SQL


def test_rollback_restores_previous_rls_enablement() -> None:
    previously_disabled = {
        "conversation_attachment_refs",
        "conversation_channel_bindings",
        "message_generation_requests",
        "task_attachment_refs",
        "memory_atoms",
        "user_activity_events",
    }
    disabled = set(re.findall(r"ALTER TABLE ([a-z_]+) DISABLE ROW LEVEL SECURITY", ROLLBACK))

    assert disabled == previously_disabled
    for table in FIRST_RUNTIME_GROUP:
        assert f"DROP POLICY IF EXISTS tenant_{table}" in ROLLBACK
    assert "RESET ROLE;" in ROLLBACK
