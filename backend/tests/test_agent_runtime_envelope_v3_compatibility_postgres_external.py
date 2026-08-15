"""Disposable PostgreSQL apply/rollback/reapply proof for Runtime envelope v3."""

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "migrations/228_08m_agent_runtime_envelope_v3_compatibility.sql"
ROLLBACK = (
    ROOT
    / "migrations/rollback/228_08m_agent_runtime_envelope_v3_compatibility_rollback.sql"
)


def _definition(connection: psycopg.Connection, signature: str) -> str:
    return connection.execute(
        "SELECT pg_get_functiondef(%s::regprocedure)", (signature,),
    ).fetchone()[0]


def _read_context_foundation() -> str:
    sql = (
        ROOT / "migrations/225_01_agent_runtime_read_capability_rpcs.sql"
    ).read_text()
    start = sql.index("CREATE OR REPLACE FUNCTION _agent_runtime_read_context")
    end = sql.index("\nEND $$;", start) + len("\nEND $$;")
    return "SET LOCAL ROLE everydayai_owner;\n" + sql[start:end]


def test_apply_rollback_reapply_preserves_acl_and_exact_schema_contract(
    database: str,
) -> None:
    with psycopg.connect(database) as connection:
        connection.execute(_read_context_foundation())
        connection.execute(MIGRATION.read_text())
        connection.commit()

        for signature in (
            "get_agent_runtime_model_context_v2(uuid,text,uuid)",
            "_agent_runtime_read_context(uuid,uuid,uuid,text,text,integer)",
        ):
            definition = _definition(connection, signature)
            assert "IS NULL" in definition
            assert "NOT IN ('2','3')" in definition
            assert connection.execute(
                "SELECT has_function_privilege("
                "'everydayai_agent_runtime_worker',%s,'EXECUTE')",
                (signature,),
            ).fetchone()[0]

        connection.execute(ROLLBACK.read_text())
        connection.commit()
        for signature in (
            "get_agent_runtime_model_context_v2(uuid,text,uuid)",
            "_agent_runtime_read_context(uuid,uuid,uuid,text,text,integer)",
        ):
            definition = _definition(connection, signature)
            assert "IS DISTINCT FROM '2'" in definition
            assert "NOT IN ('2','3')" not in definition

        connection.execute(MIGRATION.read_text())
        connection.commit()
        assert "NOT IN ('2','3')" in _definition(
            connection,
            "get_agent_runtime_model_context_v2(uuid,text,uuid)",
        )
