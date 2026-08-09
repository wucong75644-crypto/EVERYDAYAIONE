from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "migrations/221_worker_media_rpc_bigint_compatibility.sql"
).read_text()
ROLLBACK = (
    ROOT / "migrations/rollback"
    / "221_worker_media_rpc_bigint_compatibility_rollback.sql"
).read_text()


def test_bigint_overloads_match_postgrest_numeric_rpc_calls() -> None:
    assert "p_expected_version BIGINT" in MIGRATION
    assert "worker_claim_media_task_completion(TEXT, BIGINT)" in MIGRATION
    assert "worker_settle_media_batch_item(TEXT, BIGINT, TEXT, JSONB, TEXT)" in MIGRATION
    assert "p_expected_version::INTEGER" in MIGRATION
    assert "TO everydayai_worker;" in MIGRATION


def test_rollback_only_removes_bigint_compatibility_overloads() -> None:
    assert "DROP FUNCTION worker_claim_media_task_completion(TEXT, BIGINT)" in ROLLBACK
    assert "DROP FUNCTION worker_settle_media_batch_item(TEXT, BIGINT, TEXT, JSONB, TEXT)" in ROLLBACK
    assert "worker_claim_media_task_completion(TEXT, INTEGER)" not in ROLLBACK
    assert "worker_settle_media_batch_item(TEXT, INTEGER, TEXT, JSONB, TEXT)" not in ROLLBACK
