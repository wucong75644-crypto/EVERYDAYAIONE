"""Conversation delivery 事务 Outbox 迁移契约测试。"""

from pathlib import Path


MIGRATIONS = Path(__file__).parent.parent / "migrations"
MIGRATION = MIGRATIONS / "124_conversation_delivery_outbox.sql"
ROLLBACK = (
    MIGRATIONS / "rollback" / "124_conversation_delivery_outbox_rollback.sql"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(sql: str, name: str, next_name: str | None = None) -> str:
    start = sql.index(f"CREATE OR REPLACE FUNCTION {name}")
    end = (
        sql.index(f"CREATE OR REPLACE FUNCTION {next_name}")
        if next_name else len(sql)
    )
    return sql[start:end]


def test_outbox_has_unique_task_channel_and_state_constraints() -> None:
    sql = _read(MIGRATION)

    assert "CREATE TABLE IF NOT EXISTS conversation_deliveries" in sql
    assert "UNIQUE (task_id, channel)" in sql
    assert "status IN ('pending', 'delivering', 'delivered', 'dead')" in sql
    assert "jsonb_typeof(target_context) = 'object'" in sql
    assert "jsonb_typeof(delivered_items) = 'array'" in sql
    assert "idx_conversation_deliveries_claim" in sql
    assert "idx_conversation_deliveries_expired_lease" in sql


def test_terminal_trigger_is_transactional_and_wecom_only() -> None:
    sql = _read(MIGRATION)
    function = _function(
        sql, "create_actor_terminal_delivery", "claim_conversation_delivery",
    )

    assert "AFTER UPDATE OF status ON tasks" in sql
    assert "NEW.status NOT IN ('completed', 'failed')" in function
    assert "delivery_context @> '{\"actor\": true}'::JSONB" in function
    assert "v_channel <> 'wecom'" in function
    assert "ON CONFLICT (task_id, channel) DO NOTHING" in function


def test_claim_uses_skip_locked_stable_order_and_lease() -> None:
    function = _function(
        _read(MIGRATION),
        "claim_conversation_delivery",
        "renew_conversation_delivery",
    )

    assert "FOR UPDATE SKIP LOCKED" in function
    assert "ORDER BY next_attempt_at, created_at, id" in function
    assert "lease_expires_at <= NOW()" in function
    assert "attempt_count < p_max_attempts" in function
    assert "attempt_count = attempt_count + 1" in function
    assert "delivery lease expired after max attempts" in function
    assert "'lease_token', v_token" in function


def test_renew_and_complete_require_current_fencing_token() -> None:
    sql = _read(MIGRATION)
    renew = _function(
        sql, "renew_conversation_delivery", "complete_conversation_delivery",
    )
    complete = _function(
        sql, "complete_conversation_delivery", "fail_conversation_delivery",
    )

    for function in (renew, complete):
        assert "lease_token IS DISTINCT FROM p_lease_token" in function
        assert "'ownership_lost'" in function
    assert "lease_expires_at <= NOW()" in renew
    assert "lease_expires_at <= NOW()" in complete
    assert "delivered_items = COALESCE" in renew
    assert "status = 'delivered'" in complete


def test_fail_uses_bounded_backoff_and_dead_letter() -> None:
    function = _function(_read(MIGRATION), "fail_conversation_delivery")

    assert "v_delivery.attempt_count >= p_max_attempts" in function
    assert "LEAST(" in function
    assert "900" in function
    assert "status = CASE WHEN v_dead THEN 'dead' ELSE 'pending' END" in function
    assert "'retry_scheduled'" in function


def test_delivery_rpcs_are_not_public() -> None:
    sql = _read(MIGRATION)
    for signature in (
        "claim_conversation_delivery(INTEGER, INTEGER)",
        "renew_conversation_delivery(UUID, UUID, INTEGER, JSONB)",
        "complete_conversation_delivery(UUID, UUID, JSONB)",
        "fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER)",
    ):
        assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC" in sql


def test_rollback_removes_trigger_before_table() -> None:
    sql = _read(ROLLBACK)

    assert sql.index("DROP TRIGGER") < sql.index(
        "DROP TABLE IF EXISTS conversation_deliveries"
    )
    for function in (
        "create_actor_terminal_delivery",
        "claim_conversation_delivery",
        "renew_conversation_delivery",
        "complete_conversation_delivery",
        "fail_conversation_delivery",
    ):
        assert f"DROP FUNCTION IF EXISTS {function}" in sql
