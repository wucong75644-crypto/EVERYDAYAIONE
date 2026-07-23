"""ContextReceipt Epoch、缓存身份与 Provider 用量迁移合同测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "147_context_receipt_cache_identity.sql"
ROLLBACK = (
    ROOT
    / "migrations"
    / "rollback"
    / "147_context_receipt_cache_identity_rollback.sql"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_migration_adds_epoch_cache_and_usage_columns() -> None:
    sql = _read(MIGRATION)

    assert "ADD COLUMN IF NOT EXISTS context_epoch_id TEXT" in sql
    assert "ADD COLUMN IF NOT EXISTS cache_identity JSONB" in sql
    assert "ADD COLUMN IF NOT EXISTS provider_usage JSONB" in sql
    assert "idx_context_receipts_epoch" in sql
    assert "conversation_context_receipts_provider_usage_check" in sql


def test_v2_commit_validates_and_atomically_updates_receipts() -> None:
    sql = _read(MIGRATION)

    assert "commit_generation_turn_with_context_v2(" in sql
    assert "SELECT commit_generation_turn(" in sql
    assert "ACTOR_CONTEXT_RECEIPT_CACHE_INVALID" in sql
    assert "UPDATE conversation_context_receipts" in sql
    assert "context_epoch_id = v_item->>'context_epoch_id'" in sql
    assert "cache_identity = v_item->'cache_identity'" in sql
    assert "provider_usage = v_item->'provider_usage'" in sql
    assert "ACTOR_CONTEXT_RECEIPT_MISSING" in sql
    assert "SECURITY INVOKER" in sql
    assert "REVOKE ALL ON FUNCTION" in sql


def test_rollback_restores_old_rpc_path_and_drops_added_schema() -> None:
    sql = _read(ROLLBACK)

    assert "DROP FUNCTION IF EXISTS commit_generation_turn_with_context_v2" in sql
    assert "DROP INDEX IF EXISTS idx_context_receipts_epoch" in sql
    assert "DROP COLUMN IF EXISTS provider_usage" in sql
    assert "DROP COLUMN IF EXISTS cache_identity" in sql
    assert "DROP COLUMN IF EXISTS context_epoch_id" in sql
