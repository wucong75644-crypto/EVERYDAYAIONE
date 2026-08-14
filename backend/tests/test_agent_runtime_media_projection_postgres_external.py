from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _prepare_legacy_schema, _seed_batch,
)

pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_06_agent_runtime_media_projection.sql"
ROLLBACK = ROOT / "migrations/rollback/228_06_agent_runtime_media_projection_rollback.sql"
PREPARED_MIGRATION = ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql"
PREPARED_SCHEMA_STUB = """
CREATE TABLE agent_runtime_prepared_media_action_bindings (
    action_id UUID PRIMARY KEY REFERENCES agent_actions(id) ON DELETE RESTRICT, task_id UUID NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT, run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    model_step_id UUID NOT NULL REFERENCES agent_model_steps(id) ON DELETE RESTRICT, org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT, input_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    output_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('image','video')),
    action_request_hash TEXT NOT NULL, task_request_hash TEXT NOT NULL, reference_manifest_hash TEXT NOT NULL, provider_request_hash TEXT NOT NULL,
    pricing_revision TEXT NOT NULL, pricing_model_id TEXT NOT NULL, pricing_key TEXT NOT NULL, pricing_fact_hash TEXT NOT NULL,
    unit_credits INTEGER NOT NULL CHECK (unit_credits > 0), credit_transaction_id UUID NOT NULL UNIQUE REFERENCES credit_transactions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION _agent_runtime_media_owner_readiness_v1()
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
RETURN jsonb_build_object(
    'ready',TRUE,'state_version',1,'projection_heartbeat_fresh',TRUE
);
"""
ASSET_RPC_STUB = """
CREATE FUNCTION register_user_asset(
    p_org_id UUID,p_storage_scope TEXT,p_storage_owner_key TEXT,
    p_storage_provider TEXT,p_storage_key TEXT,p_media_type TEXT,
    p_original_url TEXT,p_thumbnail_url TEXT,p_download_url TEXT,
    p_workspace_path TEXT,p_name TEXT,p_mime_type TEXT,p_size BIGINT,
    p_content_sha256 TEXT,p_asset_metadata JSONB,p_ref_key TEXT,
    p_actor_user_id UUID,p_source_type TEXT,p_source_kind TEXT,p_ref_kind TEXT,
    p_conversation_id UUID,p_source_message_id UUID,p_source_task_id UUID,
    p_source_generation_id UUID,p_source_attachment_id UUID,p_content_index INTEGER,
    p_model_id TEXT,p_prompt TEXT,p_ref_metadata JSONB,p_created_at TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql AS $fn$
DECLARE v_asset_id UUID:=gen_random_uuid();
BEGIN
  INSERT INTO runtime_media_asset_calls(payload) VALUES(jsonb_build_object(
    'asset_id',v_asset_id,'media_type',p_media_type,'source_kind',p_source_kind,
    'ref_metadata',p_ref_metadata,'created_at',p_created_at));
  RETURN jsonb_build_object('asset',jsonb_build_object('id',v_asset_id));
END $fn$;
"""

def _install_prepared_contract(connection: psycopg.Connection) -> None:
    if PREPARED_MIGRATION.exists():
        connection.execute(PREPARED_MIGRATION.read_text(encoding="utf-8"))
    else:
        connection.execute(PREPARED_SCHEMA_STUB)

def _seed_terminal_event(
    database: str, action_id: UUID, source_url: str,
    *, event_type: str = "action.completed",
) -> UUID:
    event_id, outbox_id = uuid4(), uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute("""
            SELECT session_id,run_id,model_step_id,org_id,user_id
              FROM agent_actions WHERE id=%s
        """, (action_id,)).fetchone()
        assert action is not None
        sequence = connection.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM agent_runtime_events "
            "WHERE session_id=%s", (action[0],),
        ).fetchone()[0]
        status = {
            "action.completed": "completed", "action.failed": "failed",
            "action.cancelled": "cancelled",
        }[event_type]
        terminal_reason = None if status == "completed" else "provider_failed"
        connection.execute("""
            UPDATE agent_actions SET status=%s,terminal_reason=%s,
                   completed_at=clock_timestamp() WHERE id=%s
        """, (status, terminal_reason, action_id))
        result_data = (
            {"result_urls": [source_url]} if status == "completed"
            else {"error_code": terminal_reason}
        )
        connection.execute("""
            INSERT INTO agent_action_results(
                action_id,session_id,run_id,org_id,user_id,status,result_hash,
                summary,data,error_code
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            action_id, action[0], action[1], action[3], action[4],
            "success" if status == "completed" else "error",
            hashlib.sha256(json.dumps(result_data).encode()).hexdigest(),
            status, Jsonb(result_data), terminal_reason,
        ))
        connection.execute("""
            INSERT INTO agent_runtime_events(
                id,session_id,sequence,org_id,user_id,scope_kind,scope_id,
                event_type,run_id,model_step_id,action_id,correlation_id,
                actor_type,payload,payload_hash
            ) VALUES(%s,%s,%s,%s,%s,'user',%s,%s,%s,%s,%s,%s,
                     'executor','{}','runtime-media-test')
        """, (
            event_id, action[0], sequence, action[3], action[4], str(action[4]),
            event_type, action[1], action[2], action_id, action_id,
        ))
        connection.execute("""
            INSERT INTO agent_projection_outbox(
                id,event_id,session_id,org_id,user_id,projection_kind
            ) VALUES(%s,%s,%s,%s,%s,'web_runtime')
        """, (outbox_id, event_id, action[0], action[3], action[4]))
    return outbox_id


def _seed_retry_run_event(database: str, action_id: UUID) -> UUID:
    event_id, outbox_id = uuid4(), uuid4()
    contract = {
        "source": "runtime_media_retry", "execution_mode": "one_shot_action",
        "projection_mode": "media_action_only",
    }
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute("""
            SELECT session_id,run_id,model_step_id,org_id,user_id
              FROM agent_actions WHERE id=%s
        """, (action_id,)).fetchone()
        sequence = connection.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM agent_runtime_events "
            "WHERE session_id=%s", (action[0],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE agent_runs SET capability_snapshot=%s WHERE id=%s",
            (Jsonb(contract), action[1]),
        )
        connection.execute("""
            INSERT INTO agent_runtime_events(
                id,session_id,sequence,org_id,user_id,scope_kind,scope_id,
                event_type,run_id,model_step_id,action_id,correlation_id,
                actor_type,payload,payload_hash
            ) VALUES(%s,%s,%s,%s,%s,'user',%s,'run.completed',%s,%s,NULL,%s,
                     'system',%s,'runtime-media-retry-test')
        """, (
            event_id, action[0], sequence, action[3], action[4], str(action[4]),
            action[1], action[2], action[1], Jsonb(contract),
        ))
        connection.execute("""
            INSERT INTO agent_projection_outbox(
                id,event_id,session_id,org_id,user_id,projection_kind
            ) VALUES(%s,%s,%s,%s,%s,'web_runtime')
        """, (outbox_id, event_id, action[0], action[3], action[4]))
    return outbox_id


def _projection_connection(database: str) -> psycopg.Connection:
    connection = psycopg.connect(
        database.replace("postgres@", "everydayai_projection_worker@"),
    )
    connection.execute("SELECT set_config('app.access_kind','projection',false)")
    return connection


def _convert_to_prepared_binding(
    database: str, action_id: UUID, media_kind: str,
) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            INSERT INTO agent_runtime_prepared_media_action_bindings(
                action_id,task_id,session_id,run_id,model_step_id,org_id,user_id,
                conversation_id,input_message_id,output_message_id,media_kind,
                action_request_hash,task_request_hash,reference_manifest_hash,
                provider_request_hash,pricing_revision,pricing_model_id,pricing_key,
                pricing_fact_hash,unit_credits,credit_transaction_id
            )
            SELECT action_id,task_id,session_id,run_id,model_step_id,org_id,user_id,
                   conversation_id,input_message_id,output_message_id,%s,
                   action_request_hash,action_request_hash,reference_manifest_hash,
                   provider_request_hash,pricing_revision,pricing_model_id,
                   pricing_resolution,pricing_fact_hash,unit_credits,
                   credit_transaction_id
              FROM agent_runtime_media_action_bindings WHERE action_id=%s
        """, (media_kind, action_id))
        if media_kind == "video":
            connection.execute(
                "UPDATE tasks SET type='video' WHERE id=(SELECT task_id FROM "
                "agent_runtime_prepared_media_action_bindings WHERE action_id=%s)",
                (action_id,),
            )


def _install_projection_migration(database: str) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        _install_prepared_contract(connection)
        connection.execute("CREATE TABLE runtime_media_asset_calls(payload JSONB NOT NULL)")
        connection.execute(ASSET_RPC_STUB)
        connection.execute(MIGRATION.read_text(encoding="utf-8"))


def _assert_projection_lock_order(connection: psycopg.Connection) -> None:
    prepared = connection.execute("""
        SELECT pg_get_functiondef(
          '_agent_runtime_media_prepared_action_projection_v1(agent_runtime_events,jsonb)'
          ::regprocedure)
    """).fetchone()[0]
    normal = connection.execute("""
        SELECT pg_get_functiondef(
          '_agent_runtime_media_action_projection_v1(agent_runtime_events,jsonb)'
          ::regprocedure)
    """).fetchone()[0]
    run = connection.execute("""
        SELECT pg_get_functiondef(
          '_agent_runtime_media_run_projection_v1(agent_runtime_events,text)'
          ::regprocedure)
    """).fetchone()[0]
    def position(source: str, pattern: str) -> int:
        match = re.search(pattern, source, flags=re.IGNORECASE)
        assert match is not None, pattern
        return match.start()

    assert position(prepared, r"FROM\s+tasks\s+WHERE\s+id\s*=\s*v_binding\.task_id\s+FOR\s+UPDATE") < position(
        prepared,
        r"FROM\s+agent_runtime_prepared_media_action_bindings\s+WHERE\s+action_id\s*=\s*p_event\.action_id\s+FOR\s+UPDATE",
    ) < position(prepared, r"FROM\s+messages\s+WHERE\s+id\s*=\s*v_binding\.output_message_id\s+FOR\s+UPDATE")
    assert position(normal, r"FROM\s+tasks\s+WHERE\s+id\s*=\s*v_binding\.task_id\s+FOR\s+UPDATE") < position(
        normal,
        r"FROM\s+agent_runtime_media_action_bindings\s+WHERE\s+action_id\s*=\s*p_event\.action_id\s+FOR\s+UPDATE",
    ) < position(normal, r"_agent_runtime_media_slot_update_v1")
    assert position(run, r"FROM\s+tasks\s+WHERE\s+id\s*=\s*v_task_id\s+FOR\s+UPDATE") < position(
        run,
        r"FROM\s+agent_runtime_media_action_bindings\s+binding\s+WHERE\s+binding\.run_id\s*=\s*v_run\.id\s+FOR\s+UPDATE",
    ) < position(run, r"FROM\s+messages\s+WHERE\s+id\s*=\s*v_output_id\s+FOR\s+UPDATE")
    assert re.search(
        r"FROM\s+agent_runs\s+WHERE\s+id\s*=\s*p_event\.run_id\s+FOR\s+UPDATE",
        run,
        flags=re.IGNORECASE,
    ) is None


def _assert_rollback_reapply(database: str, historical) -> None:
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        prepare_definition = connection.execute("""
            SELECT pg_get_functiondef(
                'prepare_agent_runtime_media_batch_v1(uuid,uuid,text,uuid,bigint,text,text)'
                ::regprocedure
            )
        """).fetchone()[0]
        _install_prepared_contract(connection)
        connection.execute("CREATE TABLE runtime_media_asset_calls(payload JSONB NOT NULL)")
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
        _assert_projection_lock_order(connection)
        assert connection.execute("""
            SELECT slot_id = action_id FROM agent_runtime_media_action_bindings
             WHERE action_id=%s
        """, (historical.attempts[0].action_id,)).fetchone()[0] is True
        assert connection.execute("""
            SELECT relrowsecurity AND relforcerowsecurity
              FROM pg_class
             WHERE oid='agent_runtime_media_projection_results'::regclass
        """).fetchone()[0] is True
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            with connection.transaction():
                connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(
            "UPDATE agent_actions SET status='completed',completed_at=clock_timestamp() "
            "WHERE id=%s",
            (historical.attempts[0].action_id,),
        )
        connection.execute("""
            UPDATE credit_transactions SET status='confirmed'
             WHERE id=(SELECT credit_transaction_id
                         FROM agent_runtime_media_action_bindings
                        WHERE action_id=%s)
        """, (historical.attempts[0].action_id,))
        connection.execute(
            "UPDATE agent_runtime_media_action_bindings SET credit_state='confirmed' "
            "WHERE action_id=%s", (historical.attempts[0].action_id,),
        )
        action = connection.execute("""
            SELECT session_id,run_id,model_step_id,org_id,user_id
              FROM agent_actions WHERE id=%s
        """, (historical.attempts[0].action_id,)).fetchone()
        event_id = uuid4()
        sequence = connection.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM agent_runtime_events "
            "WHERE session_id=%s", (action[0],),
        ).fetchone()[0]
        connection.execute("""
            INSERT INTO agent_runtime_events(
                id,session_id,sequence,org_id,user_id,scope_kind,scope_id,
                event_type,run_id,model_step_id,action_id,correlation_id,
                actor_type,payload,payload_hash
            ) VALUES(%s,%s,%s,%s,%s,'user',%s,'action.completed',%s,%s,%s,%s,
                     'system','{}','rollback-exact-guard')
        """, (
            event_id,action[0],sequence,action[3],action[4],str(action[4]),
            action[1],action[2],historical.attempts[0].action_id,event_id,
        ))
        event = (event_id, action[0], action[3], action[4])
        delivered_outbox = uuid4()
        connection.execute("""
            INSERT INTO agent_projection_outbox(
                id,event_id,session_id,org_id,user_id,projection_kind,
                status,delivered_at
            ) VALUES(%s,%s,%s,%s,%s,'web_runtime','delivered',clock_timestamp())
        """, (delivered_outbox, *event))
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            with connection.transaction():
                connection.execute("""
                    INSERT INTO agent_runtime_media_projection_recoveries(
                        recovery_request_id,outbox_id,event_id,session_id,org_id,user_id,
                        actor_user_id,expected_recovery_version,expected_attempt_count,
                        reason,not_before,database_request_id
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,0,8,'historical recovery audit',
                             clock_timestamp(),'rollback-exact-guard')
                """, (uuid4(), delivered_outbox, *event[0:4], event[3]))
                connection.execute(ROLLBACK.read_text(encoding="utf-8"))
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


def test_projection_apply_rollback_reapply_and_acl(database: str) -> None:
    _prepare_legacy_schema(database)
    historical = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, historical.attempts[0])["outcome"] == "prepared"
    _assert_rollback_reapply(database, historical)

    fresh = _seed_batch(database, 1, credits=1000)
    fresh_result = _prepare(database, fresh.attempts[0])
    assert fresh_result["outcome"] == "prepared"
    assert fresh_result["binding"]["slot_id"] == str(fresh.attempts[0].action_id)

    with _projection_connection(database) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute(
                    "SELECT * FROM agent_runtime_media_projection_results",
                )
        claimed = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        assert claimed == []

    source_url = "https://provider.example/result.webp"
    outbox_id = _seed_terminal_event(
        database, fresh.attempts[0].action_id, source_url,
    )
    content_part = {
        "type": "image", "url": "https://cdn.example/runtime.webp",
        "download_url": "https://cdn.example/runtime.webp",
        "source_url": source_url, "storage_provider": "workspace",
        "storage_key": "org/runtime.webp", "name": "runtime.webp",
        "mime_type": "image/webp", "size": 1024,
    }
    with _projection_connection(database) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        assert UUID(claimed["id"]) == outbox_id
        facts = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert facts["action_facts"]["result_urls"] == [source_url]
        registered = connection.execute(
            "SELECT register_agent_runtime_media_asset_v1(%s,%s)",
            (fresh.attempts[0].action_id, Jsonb(content_part)),
        ).fetchone()[0]
        assert registered["asset"]["id"]
        applied = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,%s)",
            (outbox_id, UUID(claimed["lease_token"]), "action_progress",
             Jsonb(content_part)),
        ).fetchone()[0]
        assert applied["outcome"] == "applied"

    with psycopg.connect(database) as connection:
        task_status, credits_used = connection.execute("""
            SELECT task.status,task.credits_used
              FROM tasks task JOIN agent_runtime_media_action_bindings binding
                ON binding.task_id=task.id WHERE binding.action_id=%s
        """, (fresh.attempts[0].action_id,)).fetchone()
        assert (task_status, credits_used) == ("completed", 6)
        call = connection.execute(
            "SELECT payload FROM runtime_media_asset_calls",
        ).fetchone()[0]
        assert call["media_type"] == "image"
        assert call["source_kind"] == "image_task"
        assert call["ref_metadata"]["action_id"] == str(fresh.attempts[0].action_id)


@pytest.mark.parametrize("media_kind,extension,mime_type", [
    ("image", "png", "image/png"),
    ("video", "mp4", "video/mp4"),
])
def test_prepared_media_has_terminal_projection_owner(
    database: str, media_kind: str, extension: str, mime_type: str,
) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    action_id = batch.attempts[0].action_id
    _convert_to_prepared_binding(database, action_id, media_kind)
    source_url = f"https://provider.example/result.{extension}"
    outbox_id = _seed_terminal_event(database, action_id, source_url)
    content_part = {
        "type": media_kind,
        "url": f"https://cdn.example/runtime.{extension}",
        "download_url": f"https://cdn.example/runtime.{extension}",
        "source_url": source_url, "storage_provider": "workspace",
        "storage_key": f"org/runtime.{extension}",
        "name": f"runtime.{extension}", "mime_type": mime_type, "size": 2048,
    }

    with _projection_connection(database) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        assert UUID(claimed["id"]) == outbox_id
        facts = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert facts["action_facts"]["result_urls"] == [source_url]
        assert facts["action_facts"]["media_kind"] == media_kind
        asset = connection.execute(
            "SELECT register_agent_runtime_media_asset_v1(%s,%s)",
            (action_id, Jsonb(content_part)),
        ).fetchone()[0]
        assert asset["asset"]["id"]
        applied = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,%s)",
            (outbox_id, UUID(claimed["lease_token"]), "action_progress",
             Jsonb(content_part)),
        ).fetchone()[0]
        assert applied["outcome"] == "applied"
        assert applied["notification"]["type"] == "task_completed"

    with psycopg.connect(database) as connection:
        status = connection.execute("""
            SELECT task.status,task.credits_used,binding.credit_state,
                   transaction.status,(message.content::JSONB->0->>'type')
              FROM agent_runtime_prepared_media_action_bindings binding
              JOIN tasks task ON task.id=binding.task_id
              JOIN credit_transactions transaction
                ON transaction.id=binding.credit_transaction_id
              JOIN messages message ON message.id=binding.output_message_id
             WHERE binding.action_id=%s
        """, (action_id,)).fetchone()
        assert status == ("completed", 6, "confirmed", "confirmed", media_kind)
        call = connection.execute(
            "SELECT payload FROM runtime_media_asset_calls",
        ).fetchone()[0]
        assert (call["media_type"], call["source_kind"]) == (media_kind, f"{media_kind}_task")
