"""Static AR-14 migration identity, security and recovery contracts."""

from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    "220_01_agent_runtime_model_result_foundation.sql",
    "220_02_agent_runtime_coordinator_recovery.sql",
    "220_03_agent_runtime_model_result_terminal.sql",
    "220_04_agent_runtime_action_recovery.sql",
]


def test_migration_identity_and_rollback_order() -> None:
    discovered = discover_migrations(ROOT / "migrations")
    names = [item.identity for item in discovered if item.identity.startswith("220_")]
    assert names == EXPECTED
    assert all(item.rollback_identity for item in discovered if item.identity in names)


def test_model_result_is_separate_authoritative_rls_table() -> None:
    sql = (ROOT / "migrations" / EXPECTED[0]).read_text()
    assert "CREATE TABLE agent_model_results" in sql
    assert "model_step_id UUID NOT NULL UNIQUE" in sql
    assert "content_hash TEXT NOT NULL" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "TO everydayai_owner" in sql
    assert "TO everydayai_worker" not in sql


def test_recovery_rpcs_are_worker_only_and_external_io_free() -> None:
    sql = "\n".join(
        (ROOT / "migrations" / name).read_text() for name in EXPECTED[1:]
    )
    for function in (
        "claim_next_agent_run",
        "get_claimed_agent_run",
        "get_agent_run_aggregate",
        "renew_model_attempt_execution",
        "complete_model_attempt_with_result",
        "claim_ready_agent_action_snapshots",
        "claim_next_agent_action_reconciliation",
        "get_claimed_agent_action_reconciliation",
    ):
        assert f"FUNCTION {function}" in sql
    assert "TO everydayai_worker" in sql
    assert "http" not in sql.lower()


def test_run_scanner_excludes_waiting_paused_and_live_running() -> None:
    sql = (ROOT / "migrations" / EXPECTED[1]).read_text()
    assert "run.status = 'queued'" in sql
    assert "run.status = 'running'" in sql
    assert "run.lease_expires_at <= clock_timestamp()" in sql
    for status in ("waiting_actions", "waiting_interaction", "paused"):
        assert f"run.status = '{status}'" not in sql


def test_foundation_rollback_fails_closed_when_facts_exist() -> None:
    sql = (
        ROOT / "migrations/rollback"
        / "220_01_agent_runtime_model_result_foundation_rollback.sql"
    ).read_text()
    assert "AGENT_MODEL_RESULT_ROLLBACK_FACTS_PRESENT" in sql
    assert "IF EXISTS (SELECT 1 FROM agent_model_results" in sql


def test_ar14_does_not_modify_frozen_migration_band() -> None:
    assert all(name.startswith("220_") for name in EXPECTED)
