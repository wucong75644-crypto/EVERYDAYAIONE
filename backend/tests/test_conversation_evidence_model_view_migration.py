"""136 Evidence model_view 迁移契约测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "136_conversation_evidence_model_view.sql"
ROLLBACK = (
    ROOT / "migrations" / "rollback"
    / "136_conversation_evidence_model_view_rollback.sql"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_migration_adds_bounded_model_view_fields() -> None:
    sql = _read(MIGRATION)

    assert "ADD COLUMN IF NOT EXISTS model_view JSONB" in sql
    assert "ADD COLUMN IF NOT EXISTS content_hash TEXT" in sql
    assert "ADD COLUMN IF NOT EXISTS byte_size BIGINT" in sql
    assert "ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ" in sql
    assert "jsonb_typeof(v_item->'model_view') <> 'object'" in sql
    assert "v_item ? 'content_hash'" in sql
    assert "v_item ? 'byte_size'" in sql
    assert "COALESCE((v_item->>'byte_size')::BIGINT, -1) < 0" in sql


def test_commit_persists_model_view_in_same_actor_transaction() -> None:
    sql = _read(MIGRATION)

    assert "validation_status, model_view, content_hash, byte_size, expires_at" in sql
    assert "v_item->'model_view'" in sql
    assert "ON CONFLICT (conversation_id, artifact_id) DO NOTHING" in sql
    assert "FROM PUBLIC" in sql


def test_rollback_restores_old_projection_before_dropping_columns() -> None:
    sql = _read(ROLLBACK)

    restore = sql.index("CREATE OR REPLACE FUNCTION commit_generation_turn")
    drop = sql.index("DROP COLUMN IF EXISTS model_view")
    assert restore < drop
    assert "validation_status, model_view" not in sql
