"""Static migration 218 atomicity, security, and rollback contracts."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PATHS = sorted((ROOT / "migrations").glob("218_*.sql"))
FOUNDATION = ROOT / "migrations/218_01_agent_runtime_action_foundation.sql"
TERMINAL = ROOT / "migrations/218_02_agent_runtime_action_tool_terminal.sql"
LIFECYCLE = ROOT / "migrations/218_03_agent_runtime_action_lifecycle.sql"
RECONCILIATION = ROOT / "migrations/218_04_agent_runtime_action_reconciliation.sql"
ROLLBACKS = [
    ROOT / f"migrations/rollback/{path.stem}_rollback.sql"
    for path in PATHS
]
SQL = "\n".join(path.read_text() for path in PATHS)


def test_action_tables_are_owner_only_force_rls() -> None:
    for table in (
        "agent_actions", "agent_action_claim_batches",
        "agent_action_attempts", "agent_action_results",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in SQL
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in SQL
    assert not re.search(
        r"GRANT\s+(SELECT|INSERT|UPDATE|DELETE|ALL)\s+ON",
        SQL, re.IGNORECASE,
    )
    assert "CREATE FUNCTION _agent_action_json_is_safe" in SQL
    assert "authorization|cookie" in SQL


def test_tool_terminal_owns_waiting_transition() -> None:
    terminal = SQL
    for evidence in (
        "_settle_agent_model_credits(",
        "UPDATE agent_model_attempts SET status='completed'",
        "UPDATE agent_model_steps SET status='completed'",
        "INSERT INTO agent_actions(",
        "blocking_action_count=blocking_action_count+v_blockers",
        "status=CASE WHEN v_blockers>0 THEN 'waiting_actions'",
        "execution_token=CASE WHEN v_blockers>0 THEN NULL",
        "UPDATE agent_run_attempts SET ended_at=",
        "'run.waiting'", "'model_step.completed'", "'action.requested'",
    ):
        assert evidence in terminal
    assert "set_agent_run_waiting(" not in terminal


def test_database_recomputes_complete_canonical_batch_hash() -> None:
    terminal = TERMINAL.read_text()
    for field in (
        "action_id", "index", "stable_tool_call_id", "provider_call_id",
        "tool_name", "arguments_hash", "wave", "dependencies", "blocking",
        "policy_decision", "policy_snapshot", "policy_revision",
        "retry_disposition", "session_id", "run_id", "model_step_id",
        "org_id", "user_id",
        "request_hash",
    ):
        assert f"'{field}'" in terminal
    assert "v_batch_hash := _agent_action_batch_hash(v_canonical)" in terminal
    assert "p_batch_hash IS DISTINCT FROM v_batch_hash" in terminal
    assert "sha256(convert_to(" in terminal


def test_lifecycle_preserves_required_ar05_edges() -> None:
    lifecycle = LIFECYCLE.read_text()
    reconciliation = RECONCILIATION.read_text()
    assert "v_attempt.status NOT IN ('claimed', 'dispatching', 'accepted', 'unknown')" in lifecycle
    assert "v_attempt.status NOT IN ('dispatching', 'accepted')" in reconciliation
    assert "v_attempt.status = 'claimed' AND p_action_status <> 'failed'" in lifecycle
    assert "v_action.retry_disposition <> 'retry_safe'" in lifecycle
    assert "UPDATE agent_actions SET status = 'queued'" in lifecycle
    assert "UPDATE agent_action_attempts SET status = 'cancelled'" in reconciliation
    assert "CREATE FUNCTION recover_expired_agent_action_attempt" in lifecycle
    assert "'lease_expired_after_dispatch'" in lifecycle
    assert "cancel_agent_action" not in SQL


def test_last_blocker_wakes_once_inside_terminal_transaction() -> None:
    lifecycle = LIFECYCLE.read_text()
    assert "blocking_action_count = blocking_action_count - 1" in lifecycle
    assert "v_run.blocking_action_count = 0" in lifecycle
    assert "v_run.status = 'waiting_actions'" in lifecycle
    assert "UPDATE agent_runs SET status = 'queued'" in lifecycle
    assert "'run.resumed'" in lifecycle


def test_cancel_run_closes_actions_and_blockers() -> None:
    reconciliation = RECONCILIATION.read_text()
    assert "CREATE OR REPLACE FUNCTION cancel_agent_run(" in reconciliation
    assert "UPDATE agent_action_attempts SET status = 'cancelled'" in reconciliation
    assert "UPDATE agent_actions SET status = 'cancelled'" in reconciliation
    assert "blocking_action_count = 0" in reconciliation
    assert "cancel_agent_action" not in reconciliation


def test_rollback_order_and_destructive_guard() -> None:
    foundation_rollback = ROOT / (
        "migrations/rollback/218_01_agent_runtime_action_foundation_rollback.sql"
    )
    reconciliation_rollback = ROOT / (
        "migrations/rollback/"
        "218_04_agent_runtime_action_reconciliation_rollback.sql"
    )
    assert "AGENT_ACTION_ROLLBACK_HAS_FACTS" in foundation_rollback.read_text()
    assert "Restore the exact migration 217 cancellation owner" in (
        reconciliation_rollback.read_text()
    )
    assert all(path.exists() for path in ROLLBACKS)


def test_migration_files_stay_under_structure_limit() -> None:
    assert all(len(path.read_text().splitlines()) <= 500 for path in PATHS)


def test_each_plpgsql_function_stays_under_structure_limit() -> None:
    pattern = re.compile(
        r"CREATE(?: OR REPLACE)? FUNCTION .*?LANGUAGE plpgsql"
        r".*?AS \$\$.*?\$\$;",
        re.IGNORECASE | re.DOTALL,
    )
    for path in PATHS:
        for function in pattern.findall(path.read_text()):
            assert len(function.splitlines()) <= 120, (
                path.name, function.splitlines()[0], len(function.splitlines())
            )
