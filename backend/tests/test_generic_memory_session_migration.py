"""Grok 式通用 Session Memory 数据库协议测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "140_generic_memory_session_runtime.sql"
ROLLBACK = ROOT / "migrations" / "rollback" / (
    "140_generic_memory_session_runtime_rollback.sql"
)
CAS_MIGRATION = ROOT / "migrations" / "141_memory_session_flush_cas.sql"
CAS_ROLLBACK = ROOT / "migrations" / "rollback" / (
    "141_memory_session_flush_cas_rollback.sql"
)


def test_session_log_has_bounded_revision_and_idempotency_contract() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS memory_session_logs" in sql
    assert "through_revision > from_revision" in sql
    assert "jsonb_typeof(content) = 'object'" in sql
    assert "pg_column_size(content) <= 262144" in sql
    assert "jsonb_typeof(source_refs) = 'array'" in sql
    assert "memory_session_logs_flush_unique" in sql
    assert (
        "conversation_id,\n            from_revision,\n"
        "            through_revision,\n            prompt_version"
    ) in sql


def test_memory_atoms_adds_generic_lifecycle_and_lineage_fields() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for field in (
        "status",
        "source_session_log_id",
        "explicitness",
        "valid_from",
        "valid_until",
        "superseded_by",
        "confirmed_by_user",
        "content_hash",
        "last_recalled_at",
        "recall_count",
        "skill_id",
        "skill_version",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {field}" in sql
    assert "memory_atoms_status_check" in sql
    assert "memory_atoms_valid_range_check" in sql
    assert "idx_memory_atoms_active_hash" in sql


def test_session_log_is_service_role_only_and_rls_enabled() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "ALTER TABLE memory_session_logs ENABLE ROW LEVEL SECURITY" in sql
    assert "rolname = 'service_role'" in sql
    assert "ON memory_session_logs TO service_role" in sql


def test_rollback_preserves_committed_memory_facts() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")

    assert "REVOKE INSERT, UPDATE, DELETE ON memory_session_logs" in sql
    assert "DROP TABLE" not in sql
    assert "DROP COLUMN" not in sql


def test_flush_cas_locks_cursor_before_session_log_commit() -> None:
    sql = CAS_MIGRATION.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS l1_cursor_revision" in sql
    assert "CREATE OR REPLACE FUNCTION commit_memory_session_flush" in sql
    assert "FOR UPDATE" in sql
    assert "v_cursor <> p_expected_revision" in sql
    assert "'outcome', 'stale'" in sql
    assert "INSERT INTO memory_session_logs" in sql
    assert "l1_cursor_revision = p_through_revision" in sql
    assert "MEMORY_FLUSH_CURSOR_UPDATE_FAILED" in sql


def test_flush_cas_rollback_keeps_cursor_and_logs() -> None:
    sql = CAS_ROLLBACK.read_text(encoding="utf-8")

    assert "DROP FUNCTION IF EXISTS commit_memory_session_flush" in sql
    assert "DROP TABLE" not in sql
    assert "DROP COLUMN" not in sql
