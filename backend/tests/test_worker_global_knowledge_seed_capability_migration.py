"""Static contract for migration 211."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend/migrations/211_worker_global_knowledge_seed_capability.sql"
)
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/"
    "211_worker_global_knowledge_seed_capability_rollback.sql"
)


def test_migration_exposes_only_the_worker_seed_facade() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "session_user <> 'everydayai_worker'" in sql
    assert "current_setting('app.access_kind', TRUE) <> 'worker'" in sql
    assert "tenant_actor_user_id() IS NOT NULL" in sql
    assert "tenant_org_id() IS NOT NULL" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "FOR UPDATE" in sql
    assert "TO everydayai_worker;" in sql
    assert "GRANT SELECT" not in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql


def test_migration_fixes_scope_and_validates_edge_endpoints() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "'seed'" in sql
    assert "'global'" in sql
    assert "NULL, NULL" in sql
    assert "GLOBAL_KNOWLEDGE_SEED_EDGE_INVALID" in sql
    assert "GLOBAL_KNOWLEDGE_SEED_EDGE_DUPLICATE" in sql
    assert "GLOBAL_KNOWLEDGE_SEED_REFERENCED" in sql
    assert "DELETE FROM knowledge_edges" in sql
    assert "DELETE FROM knowledge_nodes" in sql
    assert "jsonb_array_length(v_node->'embedding') <> 1024" in sql
    assert "jsonb_typeof(element) <> 'number'" in sql
    assert "(v_node->>'embedding')::vector" in sql


def test_rollback_only_removes_the_capability() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "REVOKE EXECUTE" in sql
    assert "DROP FUNCTION worker_replace_global_knowledge_seed" in sql
    assert "DROP FUNCTION _validate_global_knowledge_seed_payload" in sql
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql
