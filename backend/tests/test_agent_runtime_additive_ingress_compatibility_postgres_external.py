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
SIGNATURE = "uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb"


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations" / name).read_text())


def _rollback(url: str) -> None:
    with psycopg.connect(url) as conn:
        with conn.transaction():
            conn.execute((ROOT / "migrations/rollback/227_13_agent_runtime_additive_ingress_compatibility_rollback.sql").read_text())


def _ingress(url: str, *, key: str, anchor, conversation, role: str = "everydayai_runtime") -> dict:
    with _connect(url, role) as conn:
        _settings(conn, role)
        return conn.execute(
            f"SELECT runtime_submit_ingress_v5(%s::uuid,%s::uuid,%s::uuid,'user',%s,%s::uuid,"
            "'everydayai-default','v1',%s,'submit_input',%s,'web',%s::uuid,%s,%s,NULL,"
            "'{}'::jsonb,'{}'::jsonb,'c4.1-test','{}'::jsonb)",
            (conversation, ORG, USER, str(USER), USER, DEFINITION_HASH, key,
             anchor, f"message:{anchor}", CATALOG_REVISION),
        ).fetchone()[0]


def test_22713_real_postgres_apply_readback_fence_permissions_and_rollback(
    database: str,
) -> None:
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_02_agent_runtime_production_catalog_seed.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_13_agent_runtime_additive_ingress_compatibility.sql",
    ):
        _apply(database, name)

    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT to_regprocedure('runtime_submit_ingress_v5(" + SIGNATURE + ")')"
        ).fetchone()[0] is not None
        assert conn.execute(
            "SELECT to_regprocedure('runtime_submit_ingress_v4(" + SIGNATURE + ")'), "
            "to_regprocedure('runtime_submit_ingress_v3(" + SIGNATURE + ")')"
        ).fetchone()[0] is not None
        assert conn.execute(
            "SELECT get_agent_runtime_ingress_capability()"
        ).fetchone()[0] == {"outcome": "available", "ingress_version": 5}
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_runtime',%s,'execute'), "
            "has_function_privilege('everydayai_wecom_runtime',%s,'execute'), "
            "has_function_privilege('public',%s,'execute')",
            (f"runtime_submit_ingress_v5({SIGNATURE})",) * 3,
        ).fetchone() == (True, True, False)
        assert conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid='agent_runtime_production_bindings'::regclass"
        ).fetchone() == (True, True)
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_production_bindings WHERE ready"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_production_bindings "
            "WHERE ready AND tool_name LIKE ANY(ARRAY['%erp%','%media%'])"
        ).fetchone()[0] == 0
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "UPDATE agent_runtime_definition_facts SET enabled_for_new_ingress=true "
            "WHERE agent_key='everydayai-default' AND definition_revision='v1'"
        )
        conn.execute(
            "UPDATE agent_runtime_catalog_facts SET enabled_for_new_ingress=true "
            "WHERE catalog_revision=%s", (CATALOG_REVISION,)
        )
        conn.execute(
            "UPDATE agent_runtime_effective_toolset_facts SET enabled_for_new_ingress=true "
            "WHERE catalog_revision=%s", (CATALOG_REVISION,)
        )
        conn.execute(
            "INSERT INTO agent_runtime_rollout_subjects(subject_kind,subject_id,channel,enabled,capabilities) "
            "VALUES('user',%s,'web',true,'[\"runtime_ingress\"]'::jsonb)", (str(USER),)
        )
        conn.execute("UPDATE agent_runtime_control SET ingress_enabled=true WHERE singleton")
        assert conn.execute(
            "SELECT ingress_enabled,non_safe_actions_enabled,code_execute_enabled,tool_confirmation_enabled "
            "FROM agent_runtime_control WHERE singleton"
        ).fetchone() == (True, False, False, False)
        anchor = uuid4()
        conversation = uuid4()
        conn.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) VALUES(%s,%s,%s,'user',%s)",
            (conversation, USER, ORG, str(USER)),
        )
        conn.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','c4.1')",
            (anchor, conversation, ORG),
        )
        conn.commit()

    first = _ingress(database, key="c4.1-first", anchor=anchor, conversation=conversation)
    assert first["outcome"] == "created"
    assert first["ingress_version"] == 5
    with psycopg.connect(database) as conn:
        catalog_hashes = conn.execute(
            "SELECT catalog_hash FROM agent_runtime_catalog_facts WHERE catalog_revision=%s",
            (CATALOG_REVISION,),
        ).fetchall()
        assert catalog_hashes == [(catalog_hashes[0][0],)]
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_tenant_gate_controls "
            "(org_id,gate_scope,scope_key,ingress_blocked,claim_blocked,dispatch_blocked,kill_epoch,state_version,reason,updated_by) "
            "VALUES(%s,'tenant','tenant',true,false,true,1,0,'c4.1 kill',%s)", (ORG, USER),
        )
        blocked_anchor = uuid4()
        blocked_conversation = uuid4()
        conn.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) VALUES(%s,%s,%s,'user',%s)",
            (blocked_conversation, USER, ORG, str(USER)),
        )
        conn.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content) VALUES(%s,%s,%s,'user','blocked')",
            (blocked_anchor, blocked_conversation, ORG),
        )
        conn.commit()
    blocked = _ingress(database, key="c4.1-blocked", anchor=blocked_anchor, conversation=blocked_conversation)
    assert blocked == {
        "outcome": "ingress_disabled", "error_code": "RUNTIME_KILL_EPOCH_FENCED", "ingress_version": 5,
    }
    with pytest.raises(psycopg.Error, match="AR_17_4_ROLLBACK_BLOCKED_INGRESS_FACTS"):
        _rollback(database)

    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_projection_outbox")
        conn.execute("DELETE FROM agent_runtime_events")
        conn.execute("DELETE FROM agent_session_commands")
        conn.execute("DELETE FROM agent_runtime_sessions")
        conn.execute("DELETE FROM agent_runtime_tenant_gate_controls")
        conn.commit()
    _rollback(database)
    _apply(database, "227_13_agent_runtime_additive_ingress_compatibility.sql")
    _rollback(database)
