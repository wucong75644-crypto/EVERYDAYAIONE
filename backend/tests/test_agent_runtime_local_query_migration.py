from pathlib import Path

from scripts.migration_runner import discover_migrations


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_57_agent_runtime_local_query_facade.sql"
ROLLBACK = ROOT / "migrations/rollback/227_57_agent_runtime_local_query_facade_rollback.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
UNDO = ROLLBACK.read_text(encoding="utf-8")


def test_local_query_facade_is_additive_and_narrow() -> None:
    matches = [
        item for item in discover_migrations(ROOT / "migrations")
        if item.identity == MIGRATION.name
    ]
    assert [item.path for item in matches] == [MIGRATION]
    assert "CREATE TABLE" not in SQL
    assert "ALTER TABLE" not in SQL
    for contract in (
        "execute_agent_runtime_local_query_v1",
        "x.tool_name<>'local_data'",
        "x.arguments IS DISTINCT FROM p_action_arguments",
        "a.execution_token IS DISTINCT FROM p_execution_token",
        "a.request_hash IS DISTINCT FROM p_request_hash",
        "a.state_version<>p_expected_attempt_version",
        "runtime_artifact_job:local_data",
        "_agent_runtime_assert_facts_epoch",
        "SECURITY DEFINER",
        "SET search_path=pg_catalog,public",
        "TO everydayai_agent_runtime_worker",
        "AGENT_RUNTIME_LOCAL_QUERY_MODE_DISABLED",
        "AGENT_RUNTIME_LOCAL_QUERY_ORG_OVERRIDE",
    ):
        assert contract in SQL
    assert "GRANT SELECT" not in SQL
    assert "TO everydayai_worker" not in SQL


def test_local_query_facade_has_exact_function_rollback() -> None:
    assert "DROP FUNCTION execute_agent_runtime_local_query_v1" in UNDO
    assert "DROP TABLE" not in UNDO
