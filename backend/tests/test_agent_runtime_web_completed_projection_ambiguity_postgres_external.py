"""Disposable PostgreSQL apply/rollback/reapply proof for migration 228.08p."""

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "migrations/228_08p_agent_runtime_web_completed_projection_ambiguity.sql"
)
ROLLBACK = (
    ROOT
    / "migrations/rollback/"
    "228_08p_agent_runtime_web_completed_projection_ambiguity_rollback.sql"
)
SIGNATURE = (
    "_agent_compat_project_completed_run("
    "agent_runs,agent_runtime_sessions,agent_session_commands,tasks)"
)


def _definition(connection: psycopg.Connection) -> str:
    return connection.execute(
        "SELECT pg_get_functiondef(%s::regprocedure)", (SIGNATURE,),
    ).fetchone()[0]


def test_apply_rollback_reapply_compiles_unambiguous_projection(
    database: str,
) -> None:
    with psycopg.connect(database) as connection:
        connection.execute(MIGRATION.read_text())
        connection.commit()
        definition = _definition(connection).lower()
        assert "v_content text" in definition
        assert "content=v_content" in definition
        assert "content=content" not in definition

        connection.execute(ROLLBACK.read_text())
        connection.commit()
        assert "content=content" in _definition(connection).lower()

        connection.execute(MIGRATION.read_text())
        connection.commit()
        definition = _definition(connection).lower()
        assert "content=v_content" in definition
        assert "content=content" not in definition
