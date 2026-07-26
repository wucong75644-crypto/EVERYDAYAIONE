"""Runtime Knowledge 企业、个人和系统事实边界合同。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT / "backend/migrations/197_runtime_knowledge_tenant_boundary.sql"
)
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/"
    "197_runtime_knowledge_tenant_boundary_rollback.sql"
)


def test_schema_has_explicit_personal_owner_and_isolated_unique_key() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.count(
        "ADD COLUMN owner_user_id UUID REFERENCES users(id) ON DELETE CASCADE"
    ) == 2
    assert "knowledge_nodes_owner_scope_check" in sql
    assert "knowledge_edges_owner_scope_check" in sql
    unique = sql.split("CREATE UNIQUE INDEX uq_knowledge_nodes_owner", 1)[1]
    assert "COALESCE(org_id" in unique
    assert "COALESCE(owner_user_id" in unique


def test_runtime_policies_separate_system_org_and_personal_facts() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "(p_org_id IS NULL AND p_owner_user_id IS NULL)" in sql
    assert "p_org_id = tenant_org_id()" in sql
    assert "p_owner_user_id = tenant_actor_user_id()" in sql
    assert "tenant_actor_is_active_member(p_org_id)" in sql
    assert "runtime_knowledge_nodes_select" in sql
    assert "runtime_knowledge_edges_insert" in sql
    assert "runtime_knowledge_metrics_insert" in sql
    assert "GRANT EXECUTE ON FUNCTION tenant_knowledge_visible" in sql
    assert "FORCE ROW LEVEL SECURITY" not in sql


def test_rollback_refuses_to_discard_created_personal_ownership() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    guard = "KNOWLEDGE_PERSONAL_FACTS_REQUIRE_FORWARD_ROLLBACK"
    assert guard in sql
    assert sql.index(guard) < sql.index("DROP COLUMN owner_user_id")
    assert "CREATE UNIQUE INDEX uq_knowledge_nodes_org" in sql
