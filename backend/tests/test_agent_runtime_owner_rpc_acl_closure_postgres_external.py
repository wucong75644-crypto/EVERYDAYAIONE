from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import _connect, _settings, database
from tests.test_agent_runtime_owner_transition_postgres_external import (
    _enable_v5_ingress,
    _owner_transition,
    _prepared_task,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]

RAW_V5 = (
    "runtime_submit_ingress_v5(uuid,uuid,uuid,text,text,uuid,text,text,text,text,"
    "text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb)"
)
RESTORE = (
    "restore_prepared_task_to_legacy_actor(uuid,uuid,uuid,uuid,uuid,uuid,uuid,"
    "uuid,text,text,text)"
)
MARK = (
    "mark_prepared_task_runtime_owned(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,"
    "text,text,text,uuid,uuid)"
)
WEB_WRAPPER = (
    "runtime_submit_ingress_v5_owner_transition(uuid,uuid,uuid,text,text,uuid,"
    "text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb,uuid,"
    "text,uuid,uuid,uuid,text)"
)
WECOM_WRAPPER = (
    "enqueue_wecom_runtime_turn_v6(jsonb,uuid,uuid,uuid,jsonb,jsonb,text,text,"
    "text,text,text,text,text)"
)


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _rollback(url: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute(
                (
                    ROOT
                    / "migrations/rollback/227_15_agent_runtime_owner_rpc_acl_closure_rollback.sql"
                ).read_text()
            )


def _privileges(url: str, role: str) -> tuple[bool, ...]:
    signatures = (RAW_V5, RESTORE, MARK, WEB_WRAPPER, WECOM_WRAPPER)
    with psycopg.connect(url) as conn:
        return tuple(
            conn.execute(
                "SELECT has_function_privilege(%s,%s,'execute')", (role, signature)
            ).fetchone()[0]
            for signature in signatures
        )


def _assert_call_denied(url: str, role: str, signature: str) -> None:
    name, raw_types = signature[:-1].split("(", 1)
    args = ",".join(f"NULL::{sql_type}" for sql_type in raw_types.split(","))
    with _connect(url, role) as conn:
        _settings(conn, role)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT {name}({args})")


def _cleanup_owner_facts(url: str, task_ids: tuple, message_ids: tuple) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM tasks WHERE id = ANY(%s)", (list(task_ids),))
        conn.execute("DELETE FROM messages WHERE id = ANY(%s)", (list(message_ids),))
        conn.execute("DELETE FROM agent_projection_outbox")
        conn.execute("DELETE FROM agent_runtime_events")
        conn.execute("DELETE FROM agent_session_commands")
        conn.execute("DELETE FROM agent_runtime_sessions")
        conn.execute("DELETE FROM agent_runtime_tenant_gate_controls")
        conn.commit()


def test_22715_atomic_entrypoints_acl_fallback_rollback_and_reapply(
    database: str,
) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_02_agent_runtime_production_catalog_seed.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_13_agent_runtime_additive_ingress_compatibility.sql",
        "227_14_agent_runtime_owner_transition.sql",
        "227_15_agent_runtime_owner_rpc_acl_closure.sql",
    ):
        _apply(database, name)

    assert _privileges(database, "everydayai_runtime") == (
        False, False, False, True, False,
    )
    assert _privileges(database, "everydayai_wecom_runtime") == (
        False, False, False, False, True,
    )
    assert _privileges(database, "everydayai_worker") == (
        False, False, False, False, False,
    )
    assert _privileges(database, "public") == (
        False, False, False, False, False,
    )
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_runtime',"
            "'get_agent_runtime_ingress_capability()','execute'),"
            "has_function_privilege('everydayai_wecom_runtime',"
            "'get_agent_runtime_ingress_capability()','execute')"
        ).fetchone() == (True, True)

    _assert_call_denied(database, "everydayai_runtime", RAW_V5)
    _assert_call_denied(database, "everydayai_runtime", RESTORE)
    _assert_call_denied(database, "everydayai_runtime", MARK)
    _assert_call_denied(database, "everydayai_wecom_runtime", WEB_WRAPPER)
    _assert_call_denied(database, "everydayai_wecom_runtime", RAW_V5)

    _enable_v5_ingress(database)
    runtime_task = _prepared_task(database, key="c6.2-a-runtime")
    runtime_result = _owner_transition(
        database, key="c6.2-a-runtime", prepared=runtime_task
    )
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls"
            "(org_id,gate_scope,scope_key,ingress_blocked,claim_blocked,"
            "dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "SELECT org_id,'tenant','tenant',true,true,true,1,0,'c6.2-a',user_id "
            "FROM tasks WHERE id=%s",
            (runtime_task[0],),
        )
        conn.commit()
    fallback_task = _prepared_task(database, key="c6.2-a-fallback")
    fallback_result = _owner_transition(
        database, key="c6.2-a-fallback", prepared=fallback_task
    )

    assert runtime_result["outcome"] == "marked"
    assert fallback_result["outcome"] == "already_actor_owned"
    _cleanup_owner_facts(
        database,
        (runtime_task[0], fallback_task[0]),
        (runtime_task[1], runtime_task[2], fallback_task[1], fallback_task[2]),
    )

    _rollback(database)
    assert _privileges(database, "everydayai_runtime") == (
        True, True, True, True, False,
    )
    assert _privileges(database, "everydayai_wecom_runtime") == (
        True, True, True, True, True,
    )

    _apply(database, "227_15_agent_runtime_owner_rpc_acl_closure.sql")
    assert _privileges(database, "everydayai_runtime") == (
        False, False, False, True, False,
    )
    assert _privileges(database, "everydayai_wecom_runtime") == (
        False, False, False, False, True,
    )
    _rollback(database)
