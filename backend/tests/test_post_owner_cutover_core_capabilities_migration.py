from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT / "migrations/203_post_owner_cutover_core_capabilities.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/203_post_owner_cutover_core_capabilities_rollback.sql"
).read_text()


def test_conversation_policy_does_not_self_query_for_returning() -> None:
    policy = SQL.split(
        "CREATE POLICY tenant_conversations_runtime", 1,
    )[1].split("REVOKE ALL ON FUNCTION prepare_generation", 1)[0]

    assert "tenant_conversation_visible(id, org_id)" not in policy
    assert "tenant_user_fact_visible(org_id, user_id)" in policy
    assert "scope_type = 'channel'" in policy
    assert "tenant_actor_is_active_member(org_id)" in policy


def test_prepare_generation_is_runtime_only() -> None:
    assert (
        "GRANT EXECUTE ON FUNCTION prepare_generation(\n"
        "    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB\n"
        ") TO everydayai_runtime;"
    ) in SQL
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in SQL


def test_worker_ai_bundle_uses_generation_actor_capability() -> None:
    assert "CREATE OR REPLACE FUNCTION _assert_configuration_generation_actor()" in SQL
    assert "session_user = 'everydayai_worker'" in SQL
    assert SQL.count("public._assert_configuration_generation_actor()") == 4
    assert "get_ai_dashscope_bundle()" in SQL
    assert "TO everydayai_worker;" in SQL


def test_worker_metric_insert_remains_tenant_scoped() -> None:
    assert (
        "FOR INSERT TO everydayai_runtime, everydayai_worker\n"
        "WITH CHECK (tenant_user_fact_visible(org_id, user_id));"
    ) in SQL
    assert "GRANT INSERT ON knowledge_metrics TO everydayai_worker;" in SQL


def test_rollback_restores_previous_contracts() -> None:
    assert "tenant_conversation_visible(id, org_id)" in ROLLBACK
    assert "TO everydayai;" in ROLLBACK
    assert ROLLBACK.count("public._assert_configuration_runtime_actor(FALSE)") == 4
    assert "DROP FUNCTION IF EXISTS _assert_configuration_generation_actor();" in ROLLBACK
    assert "REVOKE INSERT ON knowledge_metrics FROM everydayai_worker;" in ROLLBACK
