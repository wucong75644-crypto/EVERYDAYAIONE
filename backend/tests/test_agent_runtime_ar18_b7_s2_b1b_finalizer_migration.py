from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations/227_32_agent_runtime_scheduled_finalization_apply.sql").read_text()
ROLLBACK = (ROOT / "migrations/rollback/227_32_agent_runtime_scheduled_finalization_apply_rollback.sql").read_text()


def test_finalizer_is_narrow_atomic_and_wallet_free() -> None:
    assert "apply_agent_runtime_scheduled_finalization_v1" in SQL
    assert "SECURITY DEFINER SET search_path=pg_catalog,public" in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "FOR UPDATE" in SQL
    assert "status='applied'" in SQL
    assert "agent_model_credit_settlements" in SQL
    assert "UPDATE users" not in SQL
    assert "credit_transactions" not in SQL
    assert "worker_settle_scheduled_credits" not in SQL
    assert "redacted_terminal_reason" not in SQL


def test_finalizer_has_idempotency_fences_and_guarded_rollback() -> None:
    for marker in (
        "IDEMPOTENCY_CONFLICT", "STALE_VERSION", "CLAIM_FENCED",
        "SCOPE_FENCED", "EPOCH_FENCED", "SCHEDULE_INVALID",
        "APPLICATION_FACTS_EXIST",
    ):
        assert marker in SQL + ROLLBACK
    assert ROLLBACK.index("APPLICATION_FACTS_EXIST") < ROLLBACK.index("DROP FUNCTION")
