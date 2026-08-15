"""Disposable apply/rollback/reapply proof for Web terminal projection."""

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "migrations/228_08n_agent_runtime_web_terminal_projection.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_08n_agent_runtime_web_terminal_projection_rollback.sql"
)


def _definition(connection: psycopg.Connection) -> str:
    return connection.execute(
        "SELECT pg_get_functiondef("
        "'_agent_compat_project_run(agent_runtime_events,text)'::regprocedure)"
    ).fetchone()[0]


def test_apply_rollback_reapply_preserves_projection_and_admin_acl(
    database: str,
) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("ALTER TABLE messages ADD COLUMN is_error BOOLEAN")
        connection.execute(MIGRATION.read_text())
        connection.commit()
        assert "UPDATE messages SET status='failed'" in _definition(connection)
        assert connection.execute(
            "SELECT has_function_privilege("
            "'everydayai_runtime_admin',"
            "'repair_agent_runtime_web_terminal_projection_v1(uuid,uuid,text)',"
            "'EXECUTE')"
        ).fetchone()[0]
        assert not connection.execute(
            "SELECT has_function_privilege("
            "'everydayai_agent_runtime_worker',"
            "'repair_agent_runtime_web_terminal_projection_v1(uuid,uuid,text)',"
            "'EXECUTE')"
        ).fetchone()[0]

        connection.execute(ROLLBACK.read_text())
        connection.commit()
        assert "UPDATE messages SET status='failed'" not in _definition(connection)
        assert connection.execute(
            "SELECT to_regprocedure("
            "'repair_agent_runtime_web_terminal_projection_v1(uuid,uuid,text)')"
        ).fetchone()[0] is None

        connection.execute(MIGRATION.read_text())
        connection.commit()
        assert "UPDATE messages SET status='failed'" in _definition(connection)
