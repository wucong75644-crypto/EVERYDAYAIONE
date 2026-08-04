from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    CATALOG_REVISION, DEFINITION_HASH, ORG, USER, _connect, _settings, database,
)

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


def _enable_v5_ingress(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_definition_facts SET enabled_for_new_ingress=true "
            "WHERE agent_key='everydayai-default' AND definition_revision='v1'"
        )
        conn.execute(
            "UPDATE agent_runtime_catalog_facts SET enabled_for_new_ingress=true "
            "WHERE catalog_revision=%s", (CATALOG_REVISION,),
        )
        conn.execute(
            "UPDATE agent_runtime_effective_toolset_facts "
            "SET enabled_for_new_ingress=true WHERE catalog_revision=%s",
            (CATALOG_REVISION,),
        )
        conn.execute(
            "INSERT INTO agent_runtime_rollout_subjects"
            "(subject_kind,subject_id,channel,enabled,capabilities) "
            "VALUES('user',%s,'web',true,'[\"runtime_ingress\"]'::jsonb)",
            (str(USER),),
        )
        conn.execute(
            "UPDATE agent_runtime_control SET ingress_enabled=true WHERE singleton"
        )
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS "
            "context_through_message_id UUID"
        )
        conn.commit()


def _prepared_task(url: str, *, key: str) -> tuple:
    task_id, input_id, output_id, turn_id = (uuid4() for _ in range(4))
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content,turn_id) "
            "VALUES(%s,'55555555-5555-5555-5555-555555555555',%s,'user','input',%s),"
            "(%s,'55555555-5555-5555-5555-555555555555',%s,'assistant','output',%s)",
            (input_id, ORG, turn_id, output_id, ORG, turn_id),
        )
        conn.execute(
            "INSERT INTO tasks(id,client_task_id,user_id,org_id,conversation_id,"
            "type,status,assistant_message_id,input_message_id,turn_id,"
            "context_through_message_id,delivery_context) "
            "VALUES(%s,%s,%s,%s,'55555555-5555-5555-5555-555555555555',"
            "'chat','pending',%s,%s,%s,%s,'{\"actor\":true,\"runtime\":false}'::jsonb)",
            (task_id, key, USER, ORG, output_id, input_id, turn_id, input_id),
        )
        conn.commit()
    return task_id, input_id, output_id, turn_id


def _owner_transition(url: str, *, key: str, prepared: tuple) -> dict:
    task_id, input_id, output_id, turn_id = prepared
    with _connect(url, "everydayai_runtime") as conn:
        _settings(conn, "everydayai_runtime")
        return conn.execute(
            "SELECT runtime_submit_ingress_v5_owner_transition("
            "'55555555-5555-5555-5555-555555555555'::uuid,%s,%s,'user',%s,%s,"
            "'everydayai-default','v1',%s,'submit_input',%s,'web',%s,%s,%s,NULL,"
            "'{}'::jsonb,'{}'::jsonb,'c5.3.3','{}'::jsonb,%s,%s,%s,%s,%s,%s)",
            (ORG, USER, str(USER), USER, DEFINITION_HASH, key, input_id,
             f"message:{input_id}", CATALOG_REVISION, task_id, key, input_id,
             output_id, turn_id, key),
        ).fetchone()[0]


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


def test_22714_runtime_owner_and_legacy_fallback_are_mutually_exclusive(
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
    _enable_v5_ingress(database)

    runtime_task = _prepared_task(database, key="c5.3.3-runtime")
    first = _owner_transition(
        database, key="c5.3.3-runtime", prepared=runtime_task,
    )
    replay = _owner_transition(
        database, key="c5.3.3-runtime", prepared=runtime_task,
    )
    with psycopg.connect(database) as conn:
        runtime_context = conn.execute(
            "SELECT delivery_context FROM tasks WHERE id=%s", (runtime_task[0],),
        ).fetchone()[0]
        actor_visible = conn.execute(
            "SELECT count(*) FROM tasks WHERE id=%s "
            "AND delivery_context @> '{\"actor\":true}'::jsonb",
            (runtime_task[0],),
        ).fetchone()[0]
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls"
            "(org_id,gate_scope,scope_key,ingress_blocked,claim_blocked,"
            "dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'tenant','tenant',true,true,true,1,0,'c5.3.3',%s)",
            (ORG, USER),
        )
        conn.commit()

    legacy_task = _prepared_task(database, key="c5.3.3-legacy")
    blocked = _owner_transition(
        database, key="c5.3.3-legacy", prepared=legacy_task,
    )
    with psycopg.connect(database) as conn:
        legacy_context = conn.execute(
            "SELECT delivery_context FROM tasks WHERE id=%s", (legacy_task[0],),
        ).fetchone()[0]

    assert first["outcome"] == "marked"
    assert replay["outcome"] == "already_runtime_owned"
    assert runtime_context["runtime"] is True and runtime_context["actor"] is False
    assert actor_visible == 0
    assert blocked["outcome"] == "already_actor_owned"
    assert legacy_context["runtime"] is False and legacy_context["actor"] is True

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "DELETE FROM tasks WHERE id IN (%s,%s)",
            (runtime_task[0], legacy_task[0]),
        )
        conn.execute("DELETE FROM messages WHERE id IN (%s,%s,%s,%s)", (
            runtime_task[1], runtime_task[2], legacy_task[1], legacy_task[2],
        ))
        conn.execute("DELETE FROM agent_projection_outbox")
        conn.execute("DELETE FROM agent_runtime_events")
        conn.execute("DELETE FROM agent_session_commands")
        conn.execute("DELETE FROM agent_runtime_sessions")
        conn.execute("DELETE FROM agent_runtime_tenant_gate_controls")
        conn.commit()
    _rollback(database, "227_14_agent_runtime_owner_transition_rollback.sql")
