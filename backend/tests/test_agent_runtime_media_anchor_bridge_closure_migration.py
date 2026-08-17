from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/231_02_agent_runtime_media_anchor_bridge_closure.sql"
ROLLBACK = ROOT / "migrations/rollback/231_02_agent_runtime_media_anchor_bridge_closure_rollback.sql"


def test_media_bridge_restores_input_anchor_from_context_and_passes_output_anchor() -> None:
    sql = MIGRATION.read_text()

    assert "input_message_id := (p_context_receipt->>'input_message_id')::UUID" in sql
    assert "p_output_message_id::TEXT,p_task_id::TEXT" in sql
    assert "AGENT_RUNTIME_MEDIA_CHAT_INPUT_BRIDGE_FUNCTION_DRIFT" in sql
    assert "AGENT_RUNTIME_MEDIA_OUTPUT_BRIDGE_FUNCTION_DRIFT" in sql


def test_media_bridge_rollback_restores_previous_arguments() -> None:
    sql = ROLLBACK.read_text()

    assert "p_input_message_id::TEXT,p_task_id::TEXT" in sql
    assert "AGENT_RUNTIME_MEDIA_CHAT_INPUT_BRIDGE_ROLLBACK_DRIFT" in sql
    assert "AGENT_RUNTIME_MEDIA_OUTPUT_BRIDGE_ROLLBACK_DRIFT" in sql


@pytest.mark.external
def test_media_bridge_migration_applies_to_current_function_definitions(database: str) -> None:
    _prepare_legacy_schema(database)
    migrations = (
        "227_63_agent_runtime_chat_action_submission.sql",
        "227_65_agent_runtime_media_ingress.sql",
        "228_05_agent_runtime_media_manifest_readback.sql",
        "230_10_agent_runtime_media_ingress_policy_snapshot.sql",
        "230_13_agent_runtime_chat_media_anchor.sql",
        "230_14_agent_runtime_media_direct_anchor_compatibility.sql",
        "230_15_agent_runtime_media_anchor_elsif_compatibility.sql",
        MIGRATION.name,
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for name in migrations:
            connection.execute((ROOT / "migrations" / name).read_text())
        chat = connection.execute(
            "SELECT pg_get_functiondef('submit_agent_runtime_chat_action_v1(uuid,uuid,uuid,text,text,text,integer,text,jsonb,text,text,text,text,text,text,integer,jsonb,jsonb,text)'::regprocedure)"
        ).fetchone()[0]
        media = connection.execute(
            "SELECT pg_get_functiondef('submit_agent_runtime_media_action_v1(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,uuid,text,jsonb,text,text,text,text,text,text)'::regprocedure)"
        ).fetchone()[0]
    assert "input_message_id := (p_context_receipt->>'input_message_id')::UUID" in chat
    assert "p_output_message_id::TEXT,p_task_id::TEXT" in media
