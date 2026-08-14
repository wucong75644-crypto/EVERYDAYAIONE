from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_53_agent_runtime_model_configuration_facade.sql"
ROLLBACK = ROOT / "migrations/rollback/227_53_agent_runtime_model_configuration_facade_rollback.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
UNDO = ROLLBACK.read_text(encoding="utf-8")


def test_model_configuration_facade_is_unique_and_narrow() -> None:
    matches = [
        migration for migration in discover_migrations(ROOT / "migrations")
        if migration.identity == MIGRATION.name
    ]
    assert [migration.path for migration in matches] == [MIGRATION]
    assert "CREATE FUNCTION get_agent_runtime_model_configuration_v1" in SQL
    assert "CREATE FUNCTION start_model_attempt_dispatch_v2" in SQL
    assert "ALTER TABLE agent_model_attempts" in SQL
    assert "SECURITY DEFINER" in SQL
    assert "SET search_path=pg_catalog,public" in SQL
    assert "_resolve_configuration_bundle" in SQL
    assert "CREATE TABLE" not in SQL


def test_model_configuration_facade_is_run_fenced_and_role_scoped() -> None:
    for contract in (
        "r.status<>'running'", "r.execution_token IS DISTINCT FROM",
        "p_expected_attempt_version IS NULL",
        "r.lease_expires_at<=clock_timestamp()", "ra.worker_id=a.worker_id",
        "ra.ended_at IS NULL", "a.request_hash IS DISTINCT FROM p_request_hash",
        "credential_revision' IS DISTINCT FROM s.model_revision",
        "model_tenant_kill_epoch", "model_provider_kill_epoch",
        "model_capability_kill_epoch",
    ):
        assert contract in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "TO everydayai_worker" not in SQL
    assert "claim_agent_runtime_model_gateway_operation_v2" in SQL
    assert "REVOKE USAGE ON SCHEMA public FROM everydayai_agent_model_gateway" in SQL
    assert "api_key" not in SQL.lower()


def test_model_configuration_facade_has_exact_rollback() -> None:
    assert "REVOKE ALL ON FUNCTION" in UNDO
    assert "get_agent_runtime_model_configuration_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT)" in UNDO
    assert "DROP FUNCTION get_agent_runtime_model_configuration_v1" in UNDO
    assert "AGENT_RUNTIME_MODEL_DISPATCH_ROLLBACK_FACTS_EXIST" in UNDO
    assert "DROP FUNCTION start_model_attempt_dispatch_v2" in UNDO
    assert "GRANT USAGE ON SCHEMA public TO everydayai_agent_model_gateway" in UNDO
    assert "claim_agent_runtime_model_gateway_operation_v2" in UNDO
