from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare_legacy_schema,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION_NAMES = (
    ("228_05_agent_runtime_media_manifest_readback.sql", "RUNTIME_MEDIA_228_05_PATH"),
    ("228_06_agent_runtime_media_projection.sql", None),
    ("228_06a_agent_runtime_media_projection_isolation.sql", None),
    ("228_06b_agent_runtime_media_projection_readiness.sql", None),
    ("228_07_agent_runtime_media_controls.sql", "RUNTIME_MEDIA_228_07_PATH"),
)


def _migration_paths() -> tuple[Path, ...]:
    return tuple(
        Path(os.environ[override]).resolve()
        if override and os.environ.get(override)
        else ROOT / "migrations" / name
        for name, override in MIGRATION_NAMES
    )


def _assert_projection_readiness_lifecycle(
    database: str, migrations: tuple[Path, ...],
) -> None:
    projection_url = database.replace(
        "postgres@", "everydayai_projection_worker@",
    )
    with psycopg.connect(projection_url) as connection:
        connection.execute(
            "SELECT set_config('app.access_kind','projection',false)",
        )
        connection.execute("""
            SELECT report_agent_runtime_worker_heartbeat(
                'projection','formal-combo-worker','formal-combo-release',
                TRUE,FALSE,'accepting',
                '{"media_projection_enabled":true,"media_provider_probe_passed":true}'
            )
        """)
        readiness = connection.execute("""
            SELECT record_agent_runtime_media_projection_readiness_v1(
                'formal-combo-worker','formal-combo-release',TRUE,30
            )
        """).fetchone()[0]
        assert readiness["ready"] is True

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE agent_runtime_worker_heartbeats
               SET observed_at=clock_timestamp()-interval '31 seconds'
             WHERE process_role='projection' AND worker_id='formal-combo-worker'
        """)
    with psycopg.connect(projection_url) as connection:
        connection.execute(
            "SELECT set_config('app.access_kind','projection',false)",
        )
        readiness = connection.execute("""
            SELECT record_agent_runtime_media_projection_readiness_v1(
                'formal-combo-worker','formal-combo-release',TRUE,30
            )
        """).fetchone()[0]
        assert readiness["ready"] is False

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        rollback = ROOT / (
            "migrations/rollback/"
            "228_06b_agent_runtime_media_projection_readiness_rollback.sql"
        )
        connection.execute(rollback.read_text(encoding="utf-8"))
        restored = connection.execute("""
            SELECT pg_get_functiondef(
                'record_agent_runtime_media_projection_readiness_v1(text,text,boolean,integer)'
                ::regprocedure
            )
        """).fetchone()[0]
        assert "agent_runtime_worker_heartbeats" not in restored
        connection.execute(migrations[3].read_text(encoding="utf-8"))
        reapplied = connection.execute("""
            SELECT pg_get_functiondef(
                'record_agent_runtime_media_projection_readiness_v1(text,text,boolean,integer)'
                ::regprocedure
            )
        """).fetchone()[0]
        assert "agent_runtime_worker_heartbeats" in reapplied
        assert "runtime_control.projection_enabled" in reapplied
        assert "media_provider_probe_passed" in reapplied
        assert "heartbeat.release_revision" in reapplied
        assert connection.execute("""
            SELECT has_function_privilege(
                'everydayai_projection_worker',
                'isolate_agent_runtime_media_projection_v1(uuid,uuid,text)',
                'EXECUTE'
            )
        """).fetchone()[0] is True


def test_real_228_05_06_07_share_one_readiness_gate(database: str) -> None:
    migrations = _migration_paths()
    missing = [path.name for path in migrations if not path.exists()]
    if missing:
        pytest.skip(
            "formal media branches not combined: " + ", ".join(missing)
        )
    _prepare_legacy_schema(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for migration in migrations:
            connection.execute(migration.read_text(encoding="utf-8"))
        definitions = {
            name: connection.execute(
                "SELECT pg_get_functiondef(%s::regprocedure)", (name,),
            ).fetchone()[0]
            for name in (
                "submit_agent_runtime_media_action_v1(uuid,uuid,uuid,text,text,uuid,text,text,uuid,uuid,uuid,uuid,text,jsonb,text,text,text,text,text,text)",
                "claim_agent_runtime_media_projection_v1(integer,integer)",
                "claim_agent_compat_projection_outbox(integer,integer)",
            )
        }
        for definition in definitions.values():
            assert "_agent_runtime_media_owner_readiness_v1" in definition
        readiness = connection.execute(
            "SELECT _agent_runtime_media_owner_readiness_v1()",
        ).fetchone()[0]
        assert readiness["ready"] is False
        assert readiness["projection_heartbeat_fresh"] is False
        assert connection.execute("""
            SELECT has_function_privilege(
                'everydayai_projection_worker',
                'record_agent_runtime_media_projection_readiness_v1(text,text,boolean,integer)',
                'EXECUTE'
            )
        """).fetchone()[0] is True
        connection.execute("""
            UPDATE agent_runtime_control SET projection_enabled=TRUE,
                   release_revision='formal-combo-release' WHERE singleton
        """)
        connection.execute("""
            UPDATE agent_runtime_media_owner_readiness
               SET runtime_enabled=TRUE,provider_probe_passed=TRUE,
                   production_ready=TRUE WHERE singleton
        """)
    _assert_projection_readiness_lifecycle(database, migrations)
