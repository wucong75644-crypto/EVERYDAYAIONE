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


def test_ar174_227_apply_rollback_reapply_and_force_rls(database: str) -> None:
    migration = "227_01_agent_runtime_production_closure.sql"
    rollback = "227_01_agent_runtime_production_closure_rollback.sql"
    _apply(database, migration)
    with psycopg.connect(database) as conn:
        rows = conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('agent_runtime_rollout_subjects',"
            "'agent_runtime_production_bindings','agent_runtime_shadow_mismatches') "
            "ORDER BY relname"
        ).fetchall()
        assert rows == [(True, True), (True, True), (True, True)]
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_runtime',"
            "'runtime_submit_ingress_v3(uuid,uuid,uuid,text,text,uuid,text,text,text,text,text,text,uuid,text,text,text,jsonb,jsonb,text,jsonb)','execute')"
        ).fetchone()[0] is True
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'record_agent_runtime_shadow_mismatch(uuid,uuid,text,text,text,jsonb)','execute')"
        ).fetchone()[0] is True
    _rollback(database, rollback)
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT to_regclass('agent_runtime_rollout_subjects')").fetchone()[0] is None
    _apply(database, migration)
    _rollback(database, rollback)


def test_ar174_personal_subject_rollout_is_not_global(database: str) -> None:
    _apply(database, "227_01_agent_runtime_production_closure.sql")
    user_id = "11111111-1111-1111-1111-111111111111"
    import uuid
    conversation_id, message_id = str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute(
            "INSERT INTO agent_runtime_production_bindings"
            "(catalog_revision,tool_name,provider_revision,secret_binding,readiness_hash,ready)"
            "VALUES(%s,'code_execute','sandbox-v1','secret:sandbox',%s,true)",
            ("9ef52c52816e357a4cb2bf03a9893e41127105a3ffb4c2cba18489fa880ce874", "a" * 64),
        )
        conn.execute(
            "INSERT INTO agent_runtime_rollout_subjects"
            "(subject_kind,subject_id,channel,enabled,capabilities)"
            "VALUES('user',%s,'web',true,'[\"runtime_ingress\"]'::jsonb)",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id)"
            "VALUES(%s,%s,NULL,'user',%s)", (conversation_id, user_id, user_id),
        )
        conn.execute(
            "INSERT INTO messages(id,conversation_id,org_id,role,content)"
            "VALUES(%s,%s,NULL,'user','personal')", (message_id, conversation_id),
        )
        conn.execute("UPDATE agent_runtime_control SET ingress_enabled=true WHERE singleton")
        conn.commit()
    with psycopg.connect(database.replace("postgres@", "everydayai_runtime@")) as conn:
        conn.execute("SELECT set_config('app.actor_user_id',%s,false)", (user_id,))
        conn.execute("SELECT set_config('app.org_id','',false)")
        conn.execute("SELECT set_config('app.access_kind','runtime',false)")
        result = conn.execute(
            "SELECT runtime_submit_ingress_v3(%s::uuid,NULL,%s::uuid,'user',%s,%s::uuid,"
            "'everydayai-default','v1',%s,'submit_input','ar174-personal','web',%s::uuid,%s,"
            "%s,%s,NULL,'{}'::jsonb,'ar174-test','{}'::jsonb)",
            (conversation_id, user_id, user_id, user_id,
             "c24430ae6c5e1f4a5062a87eae0369b2249cdca18eedfc275b590c2c5f76eefe",
             message_id, f"message:{message_id}",
             "9ef52c52816e357a4cb2bf03a9893e41127105a3ffb4c2cba18489fa880ce874",
             "9ef52c52816e357a4cb2bf03a9893e41127105a3ffb4c2cba18489fa880ce874"),
        ).fetchone()[0]
        assert result["outcome"] == "created"
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_runtime_rollout_subjects")
        conn.execute("DELETE FROM agent_runtime_production_bindings")
        conn.commit()
    _rollback(database, "227_01_agent_runtime_production_closure_rollback.sql")


def test_ar174_22702_seeds_exact_production_catalog_and_rolls_back(database: str) -> None:
    _apply(database, "227_01_agent_runtime_production_closure.sql")
    _apply(database, "227_02_agent_runtime_production_catalog_seed.sql")
    with psycopg.connect(database) as conn:
        catalog_revision, tool_count, binding_count, enabled = conn.execute(
            "SELECT c.catalog_revision, jsonb_array_length(c.catalog_document->'tools'), "
            "(SELECT count(*) FROM agent_runtime_production_bindings b "
            " WHERE b.catalog_revision=c.catalog_revision), d.enabled_for_new_ingress "
            "FROM agent_runtime_catalog_facts c "
            "JOIN agent_runtime_definition_facts d ON d.catalog_revision=c.catalog_revision "
            "WHERE d.definition_revision='v3'"
        ).fetchone()
        assert tool_count == 42
        assert binding_count == 42
        assert enabled is False
        # Seed bindings are identities only; credentials and provider probes
        # must promote them to ready in a later non-production control step.
        assert conn.execute(
            "SELECT count(*) FROM agent_runtime_production_bindings "
            "WHERE catalog_revision=%s AND ready"
            , (catalog_revision,)
        ).fetchone()[0] == 0
        definition, catalog, toolset = conn.execute(
            "SELECT d.definition_document, c.catalog_document, e.toolset_document "
            "FROM agent_runtime_definition_facts d "
            "JOIN agent_runtime_catalog_facts c ON c.catalog_revision=d.catalog_revision "
            "JOIN agent_runtime_effective_toolset_facts e ON e.catalog_revision=c.catalog_revision "
            "WHERE d.definition_revision='v3' AND e.scope_kind='user' AND e.channel='web' "
            "AND e.gate_state='disabled'"
        ).fetchone()
        from services.agent.runtime.catalog.registry import restore_frozen_toolset
        restored = restore_frozen_toolset(
            definition, catalog, toolset, catalog_revision=catalog_revision,
        )
        stored_hash = conn.execute(
            "SELECT effective_toolset_hash FROM agent_runtime_effective_toolset_facts "
            "WHERE catalog_revision=%s AND scope_kind='user' AND channel='web' "
            "AND gate_state='disabled'", (catalog_revision,),
        ).fetchone()[0]
        assert restored.toolset_hash == stored_hash
    _rollback(database, "227_02_agent_runtime_production_catalog_seed_rollback.sql")
    _apply(database, "227_02_agent_runtime_production_catalog_seed.sql")
    _rollback(database, "227_02_agent_runtime_production_catalog_seed_rollback.sql")
    _rollback(database, "227_01_agent_runtime_production_closure_rollback.sql")
