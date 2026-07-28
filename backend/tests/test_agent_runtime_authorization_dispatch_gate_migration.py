from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_24 = ROOT / (
    "migrations/220_24_agent_runtime_authorization_dispatch_gate.sql"
)
ROLLBACK_24 = ROOT / (
    "migrations/rollback/"
    "220_24_agent_runtime_authorization_dispatch_gate_rollback.sql"
)
MIGRATION_25 = ROOT / (
    "migrations/220_25_agent_runtime_authorization_recovery.sql"
)
ROLLBACK_25 = ROOT / (
    "migrations/rollback/"
    "220_25_agent_runtime_authorization_recovery_rollback.sql"
)


def test_dispatch_gate_is_the_only_worker_dispatch_entry() -> None:
    sql = MIGRATION_24.read_text(encoding="utf-8")

    assert "CREATE TABLE agent_action_dispatch_intents" in sql
    assert "CREATE FUNCTION gate_agent_action_dispatch(" in sql
    assert "CREATE FUNCTION get_agent_action_dispatch_intent(" in sql
    assert "REVOKE EXECUTE ON FUNCTION\n    mark_agent_action_dispatching" in sql
    assert "INSERT INTO agent_authorization_grant_uses" in sql
    assert "ALTER TABLE agent_action_dispatch_intents FORCE ROW LEVEL SECURITY" in sql
    assert "SET search_path = pg_catalog, public" in sql


def test_authorization_recovery_closes_or_activates_actions_atomically() -> None:
    sql = MIGRATION_25.read_text(encoding="utf-8")
    gate_sql = MIGRATION_24.read_text(encoding="utf-8")

    assert "CREATE FUNCTION claim_next_agent_authorization_recovery(" in sql
    assert "CREATE FUNCTION renew_agent_authorization_recovery(" in sql
    assert "CREATE FUNCTION activate_agent_authorized_action(" in sql
    assert "CREATE FUNCTION expire_agent_authorization_interaction(" in sql
    assert "CREATE FUNCTION _recompute_agent_run_wait_state(" in gate_sql
    assert "status = 'rejected'" in gate_sql
    assert "execution_token = NULL" in gate_sql
    assert "lease_expires_at = NULL" in gate_sql
    assert "FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime" in sql
    assert (
        "claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER)"
        in sql
    )


def test_rollbacks_are_reverse_ordered_and_fail_closed() -> None:
    rollback_25 = ROLLBACK_25.read_text(encoding="utf-8")
    rollback_24 = ROLLBACK_24.read_text(encoding="utf-8")

    assert "AGENT_AUTHORIZATION_RECOVERY_ROLLBACK_HAS_FACTS" in rollback_25
    assert "AGENT_AUTHORIZATION_GATE_ROLLBACK_HAS_FACTS" in rollback_24
    assert "DROP FUNCTION gate_agent_action_dispatch" in rollback_24
    assert "DROP TABLE agent_action_dispatch_intents" in rollback_24
    assert (
        "REVOKE EXECUTE ON FUNCTION\n"
        "    mark_agent_action_dispatching(UUID, UUID, BIGINT, TEXT)\n"
        "FROM everydayai_worker"
    ) in rollback_24
    assert (
        "GRANT EXECUTE ON FUNCTION\n"
        "    mark_agent_action_dispatching"
    ) not in rollback_24
