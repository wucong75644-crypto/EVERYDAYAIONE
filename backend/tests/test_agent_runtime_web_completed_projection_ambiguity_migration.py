from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT
    / "migrations/228_08p_agent_runtime_web_completed_projection_ambiguity.sql"
).read_text()
ROLLBACK = (
    ROOT
    / "migrations/rollback/"
    "228_08p_agent_runtime_web_completed_projection_ambiguity_rollback.sql"
).read_text()


def test_completed_projection_uses_unambiguous_content_variable() -> None:
    assert "CREATE OR REPLACE FUNCTION _agent_compat_project_completed_run" in SQL
    assert "v_content TEXT" in SQL
    assert "SET content=v_content" in SQL
    assert "UPDATE messages AS target" in SQL
    assert "content=content" not in SQL
    assert "SET search_path=pg_catalog,public" in SQL


def test_rollback_restores_the_previous_function_definition() -> None:
    assert "CREATE OR REPLACE FUNCTION _agent_compat_project_completed_run" in ROLLBACK
    assert "content TEXT" in ROLLBACK
    assert "SET content=content" in ROLLBACK
