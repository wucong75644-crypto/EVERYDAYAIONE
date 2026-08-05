from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_18_agent_runtime_model_gateway.sql"
ROLLBACK = ROOT / "migrations/rollback/227_18_agent_runtime_model_gateway_rollback.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
UNDO = ROLLBACK.read_text(encoding="utf-8")


def test_gateway_migration_identity_is_unique_and_additive() -> None:
    matches = [
        migration for migration in discover_migrations(ROOT / "migrations")
        if migration.identity == "227_18_agent_runtime_model_gateway.sql"
    ]
    assert [migration.path for migration in matches] == [MIGRATION]
    assert "ALTER TABLE agent_model_attempts" not in SQL
    assert "ALTER TABLE agent_model_steps" not in SQL
    assert "ALTER TABLE agent_runs" not in SQL


def test_gateway_table_has_secret_free_rls_contract() -> None:
    assert "CREATE TABLE agent_runtime_model_gateway_operations" in SQL
    assert "ENABLE ROW LEVEL SECURITY" in SQL
    assert "FORCE ROW LEVEL SECURITY" in SQL
    for forbidden in (
        "prompt TEXT", "payload JSONB", "request_body", "response_body",
        "api_key", "payload_ciphertext TEXT", "wrapped_dek TEXT",
    ):
        assert forbidden not in SQL
    assert "everydayai_agent_model_gateway" in SQL
    assert "rolcanlogin" in SQL and "rolbypassrls" in SQL


def test_gateway_rpcs_are_fixed_path_and_owner_separated() -> None:
    assert SQL.count("SECURITY DEFINER") >= 10
    assert SQL.count("search_path=pg_catalog,public") >= 10
    for name in (
        "submit_agent_runtime_model_gateway_operation",
        "read_agent_runtime_model_gateway_operation",
        "claim_agent_runtime_model_gateway_operation",
        "mark_agent_runtime_model_gateway_dispatched",
        "renew_agent_runtime_model_gateway_operation",
        "finalize_agent_runtime_model_gateway_operation",
        "recover_agent_runtime_model_gateway_operations",
    ):
        assert f"CREATE FUNCTION {name}" in SQL
    assert "_assert_agent_model_gateway_actor('runtime')" in SQL
    assert "_assert_agent_model_gateway_actor('gateway')" in SQL
    assert "REVOKE EXECUTE ON FUNCTION get_agent_runtime_ai_bundle" in SQL
    assert "TO everydayai_worker" not in SQL


def test_recovery_and_rollback_are_failure_closed() -> None:
    assert "status='submitted'" in SQL
    assert "GATEWAY_LOST_AFTER_DISPATCH" in SQL
    assert "FOR UPDATE SKIP LOCKED" in SQL
    guard = UNDO.index("AGENT_MODEL_GATEWAY_OPERATION_FACTS_EXIST")
    assert guard < UNDO.index("REVOKE ALL ON FUNCTION")
    assert guard < UNDO.index("DROP TABLE")
    assert "get_agent_runtime_ai_bundle" not in UNDO
