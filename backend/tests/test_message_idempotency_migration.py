"""消息生成幂等迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "119_message_generation_idempotency.sql"
ROLLBACK = MIGRATIONS / "rollback" / "119_message_generation_idempotency_rollback.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_migration_defines_request_state_and_replay_fields() -> None:
    sql = _read(MIGRATION)

    assert "CREATE TABLE IF NOT EXISTS message_generation_requests" in sql
    assert "request_fingerprint CHAR(64)" in sql
    assert "status IN ('processing', 'completed', 'failed')" in sql
    assert "response_status SMALLINT" in sql
    assert "response_body JSONB" in sql
    assert "expires_at TIMESTAMPTZ" in sql


def test_migration_has_personal_and_org_idempotency_uniqueness() -> None:
    sql = _read(MIGRATION)

    assert "uq_message_generation_requests_org_key" in sql
    assert "WHERE org_id IS NOT NULL" in sql
    assert "uq_message_generation_requests_personal_key" in sql
    assert "WHERE org_id IS NULL" in sql


def test_claim_rpc_validates_scope_and_handles_duplicate_requests() -> None:
    sql = _read(MIGRATION)

    assert "CREATE OR REPLACE FUNCTION claim_message_generation_request" in sql
    assert "RETURNS JSONB" in sql
    assert "RETURNS TABLE" not in sql
    assert "RETURN jsonb_build_object(" in sql
    assert "conversation.user_id = p_user_id" in sql
    assert "conversation.org_id IS NOT DISTINCT FROM p_org_id" in sql
    assert "EXCEPTION WHEN unique_violation" in sql
    assert "v_outcome := 'fingerprint_mismatch'" in sql
    assert "v_outcome := v_request.status" in sql
    assert "SECURITY INVOKER" in sql
    assert "REVOKE ALL ON FUNCTION claim_message_generation_request" in sql


def test_rollback_drops_rpc_before_table() -> None:
    sql = _read(ROLLBACK)

    function_position = sql.index("DROP FUNCTION IF EXISTS claim_message_generation_request")
    table_position = sql.index("DROP TABLE IF EXISTS message_generation_requests")
    assert function_position < table_position


def test_migration_defines_indexed_expiry_cleanup_function() -> None:
    sql = _read(MIGRATION)

    assert "idx_message_generation_requests_expiry" in sql
    assert "CREATE OR REPLACE FUNCTION cleanup_expired_message_generation_requests" in sql
    assert "WHERE expires_at < NOW()" in sql
    assert "GET DIAGNOSTICS v_deleted = ROW_COUNT" in sql
    assert "REVOKE ALL ON FUNCTION cleanup_expired_message_generation_requests()" in sql

    rollback = _read(ROLLBACK)
    cleanup_position = rollback.index(
        "DROP FUNCTION IF EXISTS cleanup_expired_message_generation_requests"
    )
    table_position = rollback.index("DROP TABLE IF EXISTS message_generation_requests")
    assert cleanup_position < table_position
