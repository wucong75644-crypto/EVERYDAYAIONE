"""135 跨 Turn 数据证据迁移契约测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "135_conversation_data_evidence.sql"
ROLLBACK = (
    ROOT / "migrations" / "rollback"
    / "135_conversation_data_evidence_rollback.sql"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_migration_keeps_old_commit_and_adds_eight_argument_overload() -> None:
    sql = _read(MIGRATION)

    assert "CREATE TABLE IF NOT EXISTS conversation_data_evidence" in sql
    assert (
        "ALTER TABLE conversation_data_evidence ENABLE ROW LEVEL SECURITY"
        in sql
    )
    assert "p_data_evidence JSONB" in sql
    assert "SELECT commit_generation_turn(" in sql
    assert "p_result_content, p_usage, p_credits_cost, p_tool_digest" in sql
    assert "closed_revision" in sql


def test_evidence_is_bounded_and_idempotent() -> None:
    sql = _read(MIGRATION)

    assert "jsonb_array_length(p_data_evidence) > 20" in sql
    assert "jsonb_array_length(v_item->'rows') > 200" in sql
    assert "pg_column_size(v_item) > 1048576" in sql
    assert "UNIQUE (conversation_id, artifact_id)" in sql
    assert "ON CONFLICT (conversation_id, artifact_id) DO NOTHING" in sql
    assert "validation_status = 'ready'" in sql


def test_non_committed_outcomes_do_not_write_evidence() -> None:
    sql = _read(MIGRATION)

    outcome_guard = sql.index(
        "COALESCE(v_result->>'outcome', '') NOT IN"
    )
    insert = sql.index("INSERT INTO conversation_data_evidence")
    assert outcome_guard < insert
    assert "'committed', 'already_committed'" in sql


def test_migration_revokes_public_execute_and_has_rollback() -> None:
    sql = _read(MIGRATION)
    rollback = _read(ROLLBACK)

    assert (
        "UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB"
        in sql
    )
    assert "FROM PUBLIC" in sql
    assert "DROP FUNCTION IF EXISTS commit_generation_turn(" in rollback
    assert "DROP TABLE IF EXISTS conversation_data_evidence" in rollback
