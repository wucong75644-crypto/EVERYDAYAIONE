from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_54_agent_runtime_erp_read_configuration.sql"
ROLLBACK = ROOT / "migrations/rollback/227_54_agent_runtime_erp_read_configuration_rollback.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
UNDO = ROLLBACK.read_text(encoding="utf-8")


def test_erp_read_configuration_facade_is_unique_additive_and_narrow() -> None:
    matches = [
        item for item in discover_migrations(ROOT / "migrations")
        if item.identity == MIGRATION.name
    ]
    assert [item.path for item in matches] == [MIGRATION]
    assert "CREATE TABLE" not in SQL
    assert "ALTER TABLE" not in SQL
    assert "get_agent_runtime_erp_configuration_v1" in SQL
    assert "rotate_agent_runtime_erp_token_pair_v1" in SQL
    assert "_resolve_configuration_bundle" in SQL
    assert "_write_configuration_entry" in SQL


def test_erp_read_configuration_is_attempt_tenant_and_kill_fenced() -> None:
    for contract in (
        "a.worker_id IS DISTINCT FROM btrim(p_worker_id)",
        "a.execution_token IS DISTINCT FROM p_execution_token",
        "a.request_hash IS DISTINCT FROM p_request_hash",
        "a.state_version<>p_expected_attempt_version",
        "agent_action_dispatch_intents",
        "agent_policy_receipts",
        "intent.executor_type='runtime_remote_read:'||x.tool_name",
        "intent.recovery_mode='idempotent_replay'",
        "receipt.arguments_hash=x.arguments_hash",
        "agent_runtime_owner_fences",
        "network.provider.read",
        "x.policy_decision NOT IN ('allow','preauthorized')",
    ):
        assert contract in SQL
    assert "erp_taobao_query" not in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "TO everydayai_worker" not in SQL
    assert "SECURITY DEFINER" in SQL
    assert "SET search_path=pg_catalog,public" in SQL


def test_erp_read_configuration_has_exact_rollback() -> None:
    for function in (
        "rotate_agent_runtime_erp_token_pair_v1",
        "get_agent_runtime_erp_configuration_v1",
        "_agent_runtime_erp_read_context_v1",
        "_agent_runtime_erp_read_fence_v1",
    ):
        assert f"DROP FUNCTION {function}" in UNDO
    assert "DROP TABLE" not in UNDO
