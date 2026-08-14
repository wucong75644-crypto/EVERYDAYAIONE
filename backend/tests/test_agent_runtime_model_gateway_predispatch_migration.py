from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_19_agent_runtime_model_gateway_predispatch_failure.sql"
ROLLBACK = ROOT / "migrations/rollback/227_19_agent_runtime_model_gateway_predispatch_failure_rollback.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
UNDO = ROLLBACK.read_text(encoding="utf-8")
RPC = "fail_agent_runtime_model_gateway_claim"


def test_predispatch_migration_identity_is_next_additive_lane() -> None:
    matches = [
        migration for migration in discover_migrations(ROOT / "migrations")
        if migration.identity == MIGRATION.name
    ]
    assert [migration.path for migration in matches] == [MIGRATION]
    assert ROLLBACK.exists()
    for forbidden in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "227_18"):
        assert forbidden not in SQL


def test_predispatch_rpc_has_fixed_security_and_failure_contract() -> None:
    assert f"CREATE FUNCTION {RPC}" in SQL
    assert "SECURITY DEFINER" in SQL
    assert "search_path=pg_catalog,public" in SQL
    assert "_assert_agent_model_gateway_actor('gateway')" in SQL
    assert "o.status<>'claimed'" in SQL
    assert "o.lease_expires_at<=clock_timestamp()" in SQL
    assert "o.dispatching_at IS NOT NULL" in SQL
    assert "_agent_model_gateway_fences" in SQL
    assert "TO everydayai_agent_model_gateway" in SQL
    for role in ("PUBLIC", "everydayai_agent_runtime_worker", "everydayai_worker"):
        assert role in SQL


def test_predispatch_error_codes_are_closed_and_rollback_is_exact() -> None:
    for code in (
        "GATEWAY_CONFIGURATION_UNAVAILABLE", "GATEWAY_CONFIGURATION_INVALID",
        "GATEWAY_KEK_UNAVAILABLE", "GATEWAY_SECRET_DECRYPT_FAILED",
        "GATEWAY_PROVIDER_UNSUPPORTED", "GATEWAY_PROVIDER_BUILD_FAILED",
    ):
        assert code in SQL
    assert "AGENT_MODEL_GATEWAY_PREDISPATCH_FAILURE_INVALID" in SQL
    guard = UNDO.index("AGENT_MODEL_GATEWAY_OPERATION_FACTS_EXIST")
    assert guard < UNDO.index("REVOKE ALL ON FUNCTION")
    assert guard < UNDO.index(f"DROP FUNCTION {RPC}")
    assert f"DROP FUNCTION {RPC}" in UNDO
    for forbidden in ("DELETE ", "TRUNCATE ", "DROP TABLE", "get_agent_runtime_ai_bundle"):
        assert forbidden not in UNDO
