from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database

pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _rollback(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback" / name).read_text())


def test_22714_owner_transition_apply_permissions_and_rollback(
    database: str,
) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_02_agent_runtime_production_catalog_seed.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_13_agent_runtime_additive_ingress_compatibility.sql",
        "227_14_agent_runtime_owner_transition.sql",
    ):
        _apply(database, name)

    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regprocedure('restore_prepared_task_to_legacy_actor(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,text)'), "
            "to_regprocedure('mark_prepared_task_runtime_owned(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid)'), "
            "to_regprocedure('enqueue_wecom_runtime_turn_v6(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,text,text,text,text,text)')"
        ).fetchone() == ("restore_prepared_task_to_legacy_actor(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,text)",
                         "mark_prepared_task_runtime_owned(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,uuid,uuid)",
                         "enqueue_wecom_runtime_turn_v6(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,text,text,text,text,text)")
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_runtime', %s, 'execute'), "
            "has_function_privilege('everydayai_wecom_runtime', %s, 'execute'), "
            "has_function_privilege('public', %s, 'execute')",
            ("restore_prepared_task_to_legacy_actor(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,text)",) * 3,
        ).fetchone() == (True, True, False)
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_worker', %s, 'execute')",
            ("restore_prepared_task_to_legacy_actor(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,text,text)",),
        ).fetchone()[0] is False

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_projection_outbox")
        conn.execute("DELETE FROM agent_runtime_events")
        conn.execute("DELETE FROM agent_session_commands")
        conn.execute("DELETE FROM agent_runtime_sessions")
        conn.execute("DELETE FROM agent_runtime_tenant_gate_controls")
        conn.commit()

    _rollback(database, "227_14_agent_runtime_owner_transition_rollback.sql")
    _rollback(database, "227_13_agent_runtime_additive_ingress_compatibility_rollback.sql")
