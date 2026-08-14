from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = (
    ROOT / "migrations/220_11_agent_runtime_compat_projection_foundation.sql"
).read_text()
RPCS = (
    ROOT / "migrations/220_12_agent_runtime_compat_projection_rpcs.sql"
).read_text()
ROLLBACK = (
    ROOT / "migrations/rollback/"
    "220_11_agent_runtime_compat_projection_foundation_rollback.sql"
).read_text()
RPC_ROLLBACK = (
    ROOT / "migrations/rollback/"
    "220_12_agent_runtime_compat_projection_rpcs_rollback.sql"
).read_text()


def test_foundation_is_owner_only_force_rls_and_rollback_guarded() -> None:
    for table in (
        "agent_compat_projection_checkpoints",
        "agent_compat_projection_results",
    ):
        assert f"CREATE TABLE {table}" in FOUNDATION
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in FOUNDATION
    assert "REVOKE ALL ON TABLE" in FOUNDATION
    assert "AGENT_COMPAT_PROJECTION_ROLLBACK_HAS_FACTS" in ROLLBACK
    assert "AGENT_COMPAT_PROJECTION_ROLLBACK_HAS_FACTS" in RPC_ROLLBACK


def test_claim_and_apply_are_ordered_atomic_and_fenced() -> None:
    assert "FOR UPDATE OF outbox SKIP LOCKED" in RPCS
    assert "earlier_event.sequence < event.sequence" in RPCS
    assert "AGENT_COMPAT_PROJECTION_GAP" in RPCS
    assert "lease_token IS DISTINCT FROM p_lease_token" in RPCS
    assert "UPDATE agent_compat_projection_checkpoints" in RPCS
    assert "UPDATE agent_projection_outbox SET status = 'delivered'" in RPCS
    assert "GRANT EXECUTE ON FUNCTION" in RPCS
    assert "TO everydayai_worker;" in RPCS


def test_completed_projection_validates_authoritative_model_result() -> None:
    for contract in (
        "v_final_count <> 1",
        "v_result.run_id <> p_run.id",
        "v_result.model_step_id <> v_step.id",
        "v_result.content_hash IS DISTINCT FROM p_run.result_hash",
        "digest(",
        "AGENT_COMPAT_MODEL_RESULT_INVALID",
    ):
        assert contract in RPCS
    assert "credits_history" not in RPCS
    assert "credit_transactions" not in RPCS
    assert "INSERT INTO conversation_deliveries" in RPCS


def test_migration_identity_and_order_are_exact() -> None:
    apply = sorted(path.name for path in (ROOT / "migrations").glob("220_1*.sql"))
    rollback = sorted(
        (
            path.name for path in (ROOT / "migrations/rollback").glob(
                "220_1*_rollback.sql",
            )
        ),
        reverse=True,
    )
    assert apply == [
        "220_11_agent_runtime_compat_projection_foundation.sql",
        "220_12_agent_runtime_compat_projection_rpcs.sql",
    ]
    assert rollback == [
        "220_12_agent_runtime_compat_projection_rpcs_rollback.sql",
        "220_11_agent_runtime_compat_projection_foundation_rollback.sql",
    ]
