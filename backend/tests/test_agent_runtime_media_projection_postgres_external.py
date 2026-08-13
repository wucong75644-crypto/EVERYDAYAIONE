from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _prepare_legacy_schema, _seed_batch,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_06_agent_runtime_media_projection.sql"
ROLLBACK = ROOT / "migrations/rollback/228_06_agent_runtime_media_projection_rollback.sql"
ASSET_RPC_STUB = """
CREATE FUNCTION register_user_asset(
    UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,
    BIGINT,TEXT,JSONB,TEXT,UUID,TEXT,TEXT,TEXT,UUID,UUID,UUID,UUID,
    UUID,INTEGER,TEXT,TEXT,JSONB,TIMESTAMPTZ
) RETURNS JSONB LANGUAGE sql AS 'SELECT ''{}''::jsonb';
"""


def test_projection_apply_rollback_reapply_and_acl(database: str) -> None:
    _prepare_legacy_schema(database)
    historical = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, historical.attempts[0])["outcome"] == "prepared"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        prepare_definition = connection.execute("""
            SELECT pg_get_functiondef(
                'prepare_agent_runtime_media_batch_v1(uuid,uuid,text,uuid,bigint,text,text)'
                ::regprocedure
            )
        """).fetchone()[0]
        connection.execute(ASSET_RPC_STUB)
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT pg_get_functiondef(
                'prepare_agent_runtime_media_batch_v1(uuid,uuid,text,uuid,bigint,text,text)'
                ::regprocedure
            )
        """).fetchone()[0] == prepare_definition
        assert connection.execute("""
            SELECT attnotnull FROM pg_attribute
             WHERE attrelid='agent_runtime_media_action_bindings'::regclass
               AND attname='slot_id'
        """).fetchone()[0] is True
        assert connection.execute("""
            SELECT slot_id = action_id FROM agent_runtime_media_action_bindings
             WHERE action_id=%s
        """, (historical.attempts[0].action_id,)).fetchone()[0] is True
        assert connection.execute("""
            SELECT relrowsecurity AND relforcerowsecurity
              FROM pg_class
             WHERE oid='agent_runtime_media_projection_results'::regclass
        """).fetchone()[0] is True
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT pg_get_functiondef(
                'prepare_agent_runtime_media_batch_v1(uuid,uuid,text,uuid,bigint,text,text)'
                ::regprocedure
            )
        """).fetchone()[0] == prepare_definition
        assert connection.execute("""
            SELECT count(*) FROM pg_attribute
             WHERE attrelid='agent_runtime_media_action_bindings'::regclass
               AND attname='slot_id' AND NOT attisdropped
        """).fetchone()[0] == 0
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        assert connection.execute("""
            SELECT pg_get_functiondef(
                'prepare_agent_runtime_media_batch_v1(uuid,uuid,text,uuid,bigint,text,text)'
                ::regprocedure
            )
        """).fetchone()[0] == prepare_definition

    fresh = _seed_batch(database, 1, credits=1000)
    fresh_result = _prepare(database, fresh.attempts[0])
    assert fresh_result["outcome"] == "prepared"
    assert fresh_result["binding"]["slot_id"] == str(fresh.attempts[0].action_id)

    projection_url = database.replace(
        "postgres@", "everydayai_projection_worker@",
    )
    with psycopg.connect(projection_url) as connection:
        connection.execute(
            "SELECT set_config('app.access_kind','projection',false)",
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute(
                    "SELECT * FROM agent_runtime_media_projection_results",
                )
        claimed = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        assert claimed == []
