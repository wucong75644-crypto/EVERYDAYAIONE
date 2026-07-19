"""通用手动 Curated Memory 数据库协议测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "144_manual_curated_memory.sql"
ROLLBACK = ROOT / "migrations" / "rollback" / (
    "144_manual_curated_memory_rollback.sql"
)


def test_personal_scope_and_source_contract_are_additive() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ALTER COLUMN org_id DROP NOT NULL" in sql
    assert "ADD COLUMN IF NOT EXISTS source_kind" in sql
    assert "'conversation', 'manual', 'skill'" in sql
    assert "idx_memory_atoms_personal_active" in sql
    assert "idx_memory_atoms_org_active" in sql


def test_all_rpc_scope_checks_are_null_safe() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.count("org_id IS NOT DISTINCT FROM p_org_id") >= 6
    for name in (
        "create_manual_memory",
        "update_manual_memory",
        "delete_memory_atom",
        "clear_memory_atoms",
    ):
        assert f"CREATE OR REPLACE FUNCTION {name}" in sql
    assert sql.count("SECURITY INVOKER") == 4
    assert sql.count("SET search_path = public") == 4


def test_create_is_bounded_deduplicated_and_confirmed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    lock = sql.index("pg_advisory_xact_lock")
    count = sql.index("SELECT COUNT(*)")
    insert = sql.index("INSERT INTO memory_atoms")
    assert lock < count < insert
    assert "v_count >= 100" in sql
    assert "content_hash = p_content_hash" in sql
    assert "'kind', 'reusable_context'" in sql
    assert "'active', 'confirmed', TRUE" in sql
    assert "'manual', NOW(), NOW()" in sql


def test_update_only_mutates_manual_memory_and_rebuilds_indexes() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    start = sql.index("CREATE OR REPLACE FUNCTION update_manual_memory")
    end = sql.index("CREATE OR REPLACE FUNCTION delete_memory_atom")
    update_sql = sql[start:end]
    assert "source_kind = 'manual'" in update_sql
    assert "pg_advisory_xact_lock" in update_sql
    assert "FOR UPDATE" in update_sql
    assert "id <> p_memory_id" in update_sql
    assert "embedding = p_embedding::vector" in update_sql
    assert "content_tsv = to_tsvector" in update_sql
    assert "'outcome', 'not_found'" in update_sql


def test_delete_and_clear_are_soft_delete_operations() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.count("SET status = 'deleted'") == 2
    assert sql.count("is_deleted = TRUE") == 2
    assert "GET DIAGNOSTICS v_deleted_count = ROW_COUNT" in sql
    assert "DELETE FROM memory_atoms" not in sql


def test_service_role_grants_all_manual_rpc() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.count("REVOKE ALL ON FUNCTION") == 4
    assert sql.count("FROM PUBLIC") == 4
    assert sql.count("GRANT EXECUTE ON FUNCTION") == 4
    assert "rolname = 'service_role'" in sql


def test_rollback_disables_writes_but_preserves_manual_facts() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert sql.count("DROP FUNCTION IF EXISTS") == 4
    assert "DROP COLUMN" not in sql
    assert "DELETE FROM" not in sql
    assert "ALTER COLUMN org_id SET NOT NULL" not in sql
