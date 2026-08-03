from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_04_agent_runtime_provider_submission_facts.sql"
ROLLBACK = ROOT / "migrations/rollback/227_04_agent_runtime_provider_submission_facts_rollback.sql"


def test_a2_migration_is_additive_secret_free_and_force_rls() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE TABLE agent_runtime_provider_submission_facts" in sql
    assert "ALTER TABLE agent_runtime_provider_submission_facts FORCE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE agent_runtime_provider_submission_facts" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "SECURITY DEFINER" in sql
    assert "provider_revision" in sql
    assert "request_hash" in sql
    assert "execution_token" in sql
    assert "external_idempotency_key" in sql
    assert "provider_receipt_hash" in sql
    assert "secret_value" not in sql
    assert "access_token" not in sql
    assert "CREATE TABLE agent_runtime_provider_submission_facts" not in ROLLBACK.read_text()


def test_a2_rollback_has_fact_guard_and_removes_only_a2_contract() -> None:
    rollback = ROLLBACK.read_text()
    assert "AR174_A2_ROLLBACK_GUARD_FACTS_EXIST" in rollback
    assert "DROP TABLE agent_runtime_provider_submission_facts" in rollback
    assert "DROP FUNCTION create_agent_runtime_provider_submission" in rollback
    assert "DROP FUNCTION reconcile_agent_runtime_provider_submission" in rollback
