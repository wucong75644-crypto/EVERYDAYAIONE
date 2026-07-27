"""Static contract for AR-13 migration identity, security and locking."""

from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    "219_01_agent_runtime_command_claim_foundation.sql",
    "219_02_agent_runtime_command_claim_lifecycle.sql",
    "219_02a_agent_runtime_command_claim_terminal_compatibility.sql",
    "219_sync_wecom_employee_capability_access.sql",
]
FOUNDATION = (ROOT / "migrations" / EXPECTED[0]).read_text()
LIFECYCLE = (ROOT / "migrations" / EXPECTED[1]).read_text()
ELIGIBILITY = (ROOT / "migrations" / EXPECTED[2]).read_text()


def test_migration_identity_order_and_reverse_rollback_mapping() -> None:
    discovered = discover_migrations(ROOT / "migrations")
    selected = [item for item in discovered if item.identity in EXPECTED]

    assert [item.identity for item in selected] == EXPECTED
    assert [item.rollback_identity for item in reversed(selected)] == [
        f"{Path(name).stem}_rollback.sql" for name in reversed(EXPECTED)
    ]


def test_claim_table_is_owner_only_forced_rls() -> None:
    assert "ENABLE ROW LEVEL SECURITY" in FOUNDATION
    assert "FORCE ROW LEVEL SECURITY" in FOUNDATION
    assert "FOR ALL TO everydayai_owner" in FOUNDATION
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime," in (
        FOUNDATION
    )
    assert "everydayai_worker" in FOUNDATION


def test_claim_rpc_keeps_postgres_as_truth_and_cancel_first() -> None:
    assert "FOR UPDATE SKIP LOCKED" in LIFECYCLE
    assert "ORDER BY (command.command_type = 'cancel') DESC" in LIFECYCLE
    assert "ON CONFLICT (command_id) DO NOTHING" in LIFECYCLE
    assert "UNIQUE" not in LIFECYCLE
    assert "target_run_id" in LIFECYCLE
    assert "cancel_agent_run(" in LIFECYCLE


def test_envelope_and_typed_outcomes_fail_closed() -> None:
    for field in (
        "run_kind",
        "context_receipt",
        "config_snapshot",
        "capability_snapshot",
        "request_identity",
    ):
        assert field in LIFECYCLE
    for name in (
        "claimed",
        "not_found",
        "ownership_lost",
        "lease_expired",
        "attempts_exhausted",
        "scope_rejected",
        "completed",
        "failed",
    ):
        assert f"'{name}'" in LIFECYCLE


def test_run_hash_events_and_exhaustion_follow_existing_contracts() -> None:
    assert "CREATE FUNCTION _agent_run_request_hash(" in LIFECYCLE
    assert "'command_id', p_command_id" in LIFECYCLE
    assert "'context_receipt', p_context_receipt" in LIFECYCLE
    assert "'run.created'" in LIFECYCLE
    assert "'run.cancelled'" in LIFECYCLE
    assert "'run.failed'" in LIFECYCLE
    assert "terminal_reason = 'command_attempts_exhausted'" in LIFECYCLE
    assert "_agent_run_request_hash(UUID, TEXT, JSONB, JSONB, JSONB)" in (
        LIFECYCLE
    )


def test_only_worker_receives_command_claim_rpc_execute() -> None:
    assert "TO everydayai_worker;" in LIFECYCLE
    assert "get_agent_command_run_claim(UUID, TEXT)" in LIFECYCLE
    assert "renew_agent_command_claim(UUID, UUID, INTEGER)" in LIFECYCLE
    assert "finish_agent_command_claim(UUID, UUID, TEXT, TEXT)" in LIFECYCLE


def test_historical_run_eligibility_is_locked_and_fail_closed() -> None:
    assert "LEFT JOIN agent_runs run" in ELIGIBILITY
    assert "_agent_command_run_eligibility(v_command)" in ELIGIBILITY
    assert "WHERE id = p_command.result_entity_id FOR UPDATE" in ELIGIBILITY
    assert "'association_rejected'" in ELIGIBILITY
    assert "'already_processed'" in ELIGIBILITY
    assert "run.status = 'running'" in ELIGIBILITY
    assert "run.lease_expires_at > clock_timestamp()" in ELIGIBILITY
    assert "run.user_id IS NOT DISTINCT FROM command.user_id" in ELIGIBILITY
    assert "_agent_command_run_eligibility(agent_session_commands)" in ELIGIBILITY
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime," in (
        ELIGIBILITY
    )
