from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _prepare_legacy_schema, _seed_batch,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _convert_to_prepared_binding, _install_projection_migration,
    _projection_connection, _seed_terminal_event,
)


pytestmark = pytest.mark.external


def _seed_initial_run_terminal_event(
    database: str, action_id: UUID, event_type: str,
) -> UUID:
    event_id, outbox_id = uuid4(), uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute("""
            SELECT session_id,run_id,org_id,user_id FROM agent_actions WHERE id=%s
        """, (action_id,)).fetchone()
        sequence = connection.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM agent_runtime_events "
            "WHERE session_id=%s", (action[0],),
        ).fetchone()[0]
        run_status = event_type.removeprefix("run.")
        result_hash = None
        if event_type == "run.completed":
            final_text = "runtime media final"
            result_hash = hashlib.sha256(final_text.encode()).hexdigest()
            step_id = connection.execute("""
                UPDATE agent_model_steps SET stop_reason='final'
                 WHERE run_id=%s RETURNING id
            """, (action[1],)).fetchone()[0]
            connection.execute("""
                INSERT INTO agent_model_results(
                    model_step_id,run_id,session_id,org_id,user_id,output_kind,
                    text_content,content_hash
                ) VALUES(%s,%s,%s,%s,%s,'text',%s,%s)
            """, (
                step_id,action[1],action[0],action[2],action[3],
                final_text,result_hash,
            ))
        connection.execute("""
            UPDATE agent_runs SET status=%s,result_hash=%s,
                   terminal_reason=%s,completed_at=clock_timestamp()
             WHERE id=%s
        """, (
            run_status,result_hash,
            "runtime_terminal" if event_type != "run.completed" else None,
            action[1],
        ))
        connection.execute("""
            UPDATE tasks SET request_params=jsonb_build_object(
                '_task_slot_id','initial-media-chat-slot'
            ) WHERE id=(
                SELECT chat_task_id FROM agent_runtime_media_action_bindings
                 WHERE action_id=%s
            )
        """, (action_id,))
        connection.execute("""
            INSERT INTO agent_runtime_events(
                id,session_id,sequence,org_id,user_id,scope_kind,scope_id,
                event_type,run_id,correlation_id,actor_type,payload,payload_hash
            ) VALUES(%s,%s,%s,%s,%s,'user',%s,%s,%s,%s,'system','{}',
                     'runtime-media-initial-run-test')
        """, (
            event_id,action[0],sequence,action[2],action[3],str(action[3]),
            event_type,action[1],action[1],
        ))
        connection.execute("""
            INSERT INTO agent_projection_outbox(
                id,event_id,session_id,org_id,user_id,projection_kind
            ) VALUES(%s,%s,%s,%s,%s,'web_runtime')
        """, (outbox_id,event_id,action[0],action[2],action[3]))
    return outbox_id


@pytest.mark.parametrize("fallback_key", ["image_urls", "urls"])
def test_empty_result_urls_falls_back_to_https_provider_urls(
    database: str, fallback_key: str,
) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    outbox_id = _seed_terminal_event(
        database, batch.attempts[0].action_id,
        "https://ignored.example/result.webp",
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_action_results SET data=%s WHERE action_id=%s", (
                Jsonb({"result_urls": [], fallback_key: [
                    "http://provider.example/blocked.webp",
                    "https://provider.example/accepted.webp",
                ]}), batch.attempts[0].action_id,
            ),
        )
    with _projection_connection(database) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        readback = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert readback["action_facts"]["result_urls"] == [
            "https://provider.example/accepted.webp",
        ]


@pytest.mark.parametrize("event_type", [
    "run.completed", "run.failed", "run.cancelled",
])
def test_initial_media_run_projects_message_terminal_and_chat_slot(
    database: str, event_type: str,
) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    outbox_id = _seed_initial_run_terminal_event(
        database, batch.attempts[0].action_id, event_type,
    )
    action = {
        "run.completed": "run_completed", "run.failed": "run_failed",
        "run.cancelled": "run_cancelled",
    }[event_type]
    with _projection_connection(database) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        readback = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert readback["action_facts"]["run_projection_mode"] == "runtime_media_initial"
        assert readback["action_facts"]["chat_task_slot_id"] == "initial-media-chat-slot"
        result = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
            (outbox_id, UUID(claimed["lease_token"]), action),
        ).fetchone()[0]
        assert result["outcome"] == "applied"
    expected = event_type.removeprefix("run.")
    with psycopg.connect(database) as connection:
        task_status, message_status, content = connection.execute("""
            SELECT chat_task.status,message.status,message.content::JSONB
              FROM agent_runtime_media_action_bindings binding
              JOIN tasks chat_task ON chat_task.id=binding.chat_task_id
              JOIN messages message ON message.id=binding.output_message_id
             WHERE binding.action_id=%s
        """, (batch.attempts[0].action_id,)).fetchone()
        expected_message = "completed" if expected == "completed" else "failed"
        assert (task_status, message_status) == (expected, expected_message)
        final_parts = [part for part in content if part.get("type") == "text"]
        assert len(final_parts) == (1 if event_type == "run.completed" else 0)


def test_prepared_video_cancelled_has_generic_terminal_settlement(
    database: str,
) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    action_id = batch.attempts[0].action_id
    _convert_to_prepared_binding(database, action_id, "video")
    outbox_id = _seed_terminal_event(
        database, action_id, "https://provider.example/cancelled.mp4",
        event_type="action.cancelled",
    )

    with _projection_connection(database) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        readback = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert readback["action_facts"]["media_kind"] == "video"
        result = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
            (outbox_id, UUID(claimed["lease_token"]), "action_progress"),
        ).fetchone()[0]
        assert result["outcome"] == "applied"

    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT task.status,task.credits_used,task.credits_locked,
                   binding.credit_state,transaction.status,message.status,
                   message.content::JSONB->0->>'type'
              FROM agent_runtime_prepared_media_action_bindings binding
              JOIN tasks task ON task.id=binding.task_id
              JOIN credit_transactions transaction
                ON transaction.id=binding.credit_transaction_id
              JOIN messages message ON message.id=binding.output_message_id
             WHERE binding.action_id=%s
        """, (action_id,)).fetchone()
        assert state == (
            "cancelled", 0, 0, "refunded", "refunded", "failed", "video",
        )
