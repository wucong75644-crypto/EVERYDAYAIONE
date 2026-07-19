"""Grok Dream 式 Consolidation 数据库协议测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "142_memory_consolidation_runtime.sql"
ROLLBACK = ROOT / "migrations" / "rollback" / (
    "142_memory_consolidation_runtime_rollback.sql"
)
COMMIT_MIGRATION = ROOT / "migrations" / "143_memory_consolidation_commit.sql"
COMMIT_ROLLBACK = ROOT / "migrations" / "rollback" / (
    "143_memory_consolidation_commit_rollback.sql"
)


def test_run_contract_requires_three_bounded_source_logs() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS memory_consolidation_runs" in sql
    assert "cardinality(source_log_ids) >= 3" in sql
    assert "cardinality(source_log_ids) <= 25" in sql
    assert "UNIQUE (user_id, source_hash)" in sql
    assert "output_count <= input_count" in sql


def test_run_receipt_and_terminal_state_are_bounded() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "jsonb_typeof(receipt) = 'object'" in sql
    assert "pg_column_size(receipt) <= 262144" in sql
    assert "status IN ('completed', 'failed')" in sql
    assert "status = 'completed' AND completed_at IS NOT NULL" in sql
    assert "status = 'failed' AND error_code IS NOT NULL" in sql


def test_session_logs_have_consistent_consolidated_lineage() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS consolidation_run_id" in sql
    assert "ADD COLUMN IF NOT EXISTS consolidated_at" in sql
    assert "status IN ('ready', 'failed', 'consolidated')" in sql
    assert "memory_session_logs_consolidation_check" in sql
    assert "idx_memory_session_logs_ready_user" in sql


def test_consolidation_table_is_service_role_only_with_rls() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert (
        "ALTER TABLE memory_consolidation_runs ENABLE ROW LEVEL SECURITY"
        in sql
    )
    assert "rolname = 'service_role'" in sql
    assert "ON memory_consolidation_runs TO service_role" in sql


def test_rollback_preserves_runs_and_promoted_facts() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "REVOKE INSERT, UPDATE, DELETE ON memory_consolidation_runs" in sql
    assert "DROP TABLE" not in sql
    assert "DROP COLUMN" not in sql


def test_atomic_commit_locks_sources_and_curated_relations() -> None:
    sql = COMMIT_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION commit_memory_consolidation" in sql
    assert sql.count("FOR UPDATE;") >= 2
    assert "status = 'ready'" in sql
    assert "RETURN jsonb_build_object('outcome', 'stale_sources')" in sql
    assert "RETURN jsonb_build_object('outcome', 'stale_curated')" in sql


def test_atomic_commit_only_accepts_restricted_relations_and_evidence() -> None:
    sql = COMMIT_MIGRATION.read_text(encoding="utf-8")

    for relation in ("novel", "duplicate", "supersedes", "conflicts"):
        assert f"'{relation}'" in sql
    assert "jsonb_array_length(v_operation->'source_message_ids') = 0" in sql
    assert "'explicit', 'confirmed'" in sql
    assert "source_session_log_id" in sql
    assert "pg_column_size(p_operations) > 4194304" in sql
    assert "jsonb_array_length(p_operations) > 60" not in sql


def test_atomic_commit_records_run_before_consuming_session_logs() -> None:
    sql = COMMIT_MIGRATION.read_text(encoding="utf-8")

    run_insert = sql.index("INSERT INTO memory_consolidation_runs")
    source_update = sql.index("UPDATE memory_session_logs")
    assert run_insert < source_update
    assert "SET status = 'consolidated'" in sql
    assert "consolidation_run_id = v_run_id" in sql


def test_commit_rollback_only_disables_future_writes() -> None:
    sql = COMMIT_ROLLBACK.read_text(encoding="utf-8")

    assert "DROP FUNCTION IF EXISTS commit_memory_consolidation" in sql
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql
