from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_atomic_image_batch_postgres_external import (
    _apply,
    _install_predecessors,
    _seed_prepared_batch,
    _set_ready,
    _submit,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _projection_connection,
    _seed_terminal_event,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ATOMIC = ROOT / "migrations/228_08d_agent_runtime_media_atomic_image_batch.sql"
PROJECTION = ROOT / "migrations/228_06_agent_runtime_media_projection.sql"
MIGRATION = ROOT / (
    "migrations/228_08f1_agent_runtime_media_prepared_image_batch_projection.sql"
)
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08f1_agent_runtime_media_prepared_image_batch_projection_rollback.sql"
)


def _prepare_batch(database_url: str):
    _install_predecessors(database_url)
    _apply(database_url, ATOMIC)
    _set_ready(database_url)
    task_ids, batch_id, anchor = _seed_prepared_batch(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE tasks SET delivery_context=delivery_context||%s "
            "WHERE id=ANY(%s)",
            (Jsonb({"channel": "web"}), task_ids),
        )
        connection.execute(
            "UPDATE messages SET generation_params=%s WHERE id=(SELECT "
            "assistant_message_id FROM tasks WHERE id=%s)",
            (Jsonb({"type": "image", "num_images": 2}), task_ids[0]),
        )
    receipt = _submit(database_url, batch_id, anchor)
    assert receipt["runtime_owned"] is True
    _apply(database_url, PROJECTION)
    _apply(database_url, MIGRATION)
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            "SELECT task.image_index,binding.action_id,binding.unit_credits "
            "FROM tasks task JOIN agent_runtime_prepared_media_action_bindings "
            "binding ON binding.task_id=task.id WHERE task.id=ANY(%s) "
            "ORDER BY task.image_index",
            (task_ids,),
        ).fetchall()
    return task_ids, {row[0]: row[1] for row in rows}, rows[0][2]


def _apply_projection(
    database_url: str, outbox_id: UUID,
    content_part: dict[str, object] | None = None,
) -> dict[str, object]:
    with _projection_connection(database_url) as connection:
        for _ in range(20):
            claims = connection.execute(
                "SELECT claim_agent_runtime_media_projection_v1(10,15)",
            ).fetchone()[0]
            if not claims:
                break
            for claim in claims:
                claimed_id = UUID(str(claim["id"]))
                applied = connection.execute(
                    "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,%s)",
                    (
                        claimed_id, UUID(str(claim["lease_token"])),
                        "action_progress",
                        Jsonb(content_part)
                        if claimed_id == outbox_id and content_part is not None
                        else None,
                    ),
                ).fetchone()[0]
                if claimed_id == outbox_id:
                    return applied
        raise AssertionError(f"projection outbox was not claimable: {outbox_id}")


def _seed_progress_event(
    database_url: str, action_id: UUID, event_type: str,
) -> UUID:
    event_id, outbox_id = uuid4(), uuid4()
    status = event_type.removeprefix("action.")
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute(
            "SELECT session_id,run_id,model_step_id,org_id,user_id,request_hash "
            "FROM agent_actions WHERE id=%s", (action_id,),
        ).fetchone()
        if event_type == "action.accepted":
            attempt_id, token = uuid4(), uuid4()
            connection.execute("""
              INSERT INTO agent_action_attempts(
                id,action_id,session_id,run_id,org_id,user_id,attempt_number,status,
                dispatch_phase,worker_id,execution_token,lease_expires_at,
                idempotency_key,request_hash,external_receipt,retry_disposition,
                accepted_at
              ) VALUES(%s,%s,%s,%s,%s,%s,1,'accepted','accepted','projection-test',
                       %s,clock_timestamp()+interval '10 minutes',%s,%s,%s,
                       'retry_after_reconcile',clock_timestamp())
            """, (
                attempt_id, action_id, action[0], action[1], action[3], action[4],
                token, f"attempt:{attempt_id}", action[5],
                Jsonb({"provider_task_ref": f"provider-{action_id}"}),
            ))
            connection.execute("""
              INSERT INTO agent_runtime_provider_submission_facts(
                attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,
                provider,provider_revision,external_idempotency_key,request_hash,
                execution_token,state,provider_task_ref,status_locator
              ) VALUES(%s,%s,%s,%s,%s,'user',%s,'kie','v1',%s,%s,%s,
                       'submitted',%s,'/api/v1/jobs/recordInfo')
            """, (
                attempt_id, action_id, action[1], action[3], action[4],
                str(action[4]),
                hashlib.sha256(str(action_id).encode()).hexdigest(),
                action[5], token,
                f"provider-{action_id}",
            ))
        connection.execute(
            "UPDATE agent_actions SET "
            "status=CASE WHEN status IN ('completed','failed','rejected','cancelled') "
            "THEN status ELSE %s END,"
            "terminal_reason=CASE WHEN status IN "
            "('completed','failed','rejected','cancelled') THEN terminal_reason "
            "WHEN %s='cancelled' THEN 'action_cancelled' ELSE terminal_reason END,"
            "completed_at=CASE WHEN status IN "
            "('completed','failed','rejected','cancelled') THEN completed_at "
            "WHEN %s IN ('completed','failed','rejected','cancelled') "
            "THEN clock_timestamp() ELSE NULL END WHERE id=%s",
            (status, status, status, action_id),
        )
        sequence = connection.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM agent_runtime_events "
            "WHERE session_id=%s", (action[0],),
        ).fetchone()[0]
        connection.execute("""
          INSERT INTO agent_runtime_events(
            id,session_id,sequence,org_id,user_id,scope_kind,scope_id,event_type,
            run_id,model_step_id,action_id,correlation_id,actor_type,payload,payload_hash
          ) VALUES(%s,%s,%s,%s,%s,'user',%s,%s,%s,%s,%s,%s,'executor','{}',%s)
        """, (
            event_id, action[0], sequence, action[3], action[4], str(action[4]),
            event_type, action[1], action[2], action_id, action_id,
            f"prepared-image-batch-{event_type}-{sequence}",
        ))
        connection.execute("""
          INSERT INTO agent_projection_outbox(
            id,event_id,session_id,org_id,user_id,projection_kind
          ) VALUES(%s,%s,%s,%s,%s,'web_runtime')
        """, (outbox_id, event_id, action[0], action[3], action[4]))
    return outbox_id


def _message(database_url: str, task_id: UUID):
    with psycopg.connect(database_url) as connection:
        return connection.execute(
            "SELECT message.status,message.content::JSONB,message.credits_cost "
            "FROM messages message JOIN tasks task "
            "ON task.assistant_message_id=message.id WHERE task.id=%s",
            (task_id,),
        ).fetchone()


def test_projection_apply_rollback_reapply(database: str) -> None:
    _install_predecessors(database)
    _apply(database, ATOMIC)
    _apply(database, PROJECTION)
    _apply(database, MIGRATION)
    with psycopg.connect(database) as connection:
        rows = connection.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname='agent_runtime_prepared_image_batch_slots'",
        ).fetchone()
        assert rows == (True, True)
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_prepared_image_batch_slots')",
        ).fetchone()[0] is None
    _apply(database, MIGRATION)


def test_arbitrary_mixed_terminal_order_preserves_completed_slots(database: str) -> None:
    task_ids, actions, unit_credits = _prepare_batch(database)
    second_url = "https://provider.example/second.png"
    second = _seed_terminal_event(database, actions[1], second_url)
    applied = _apply_projection(database, second, {
        "type": "image", "url": "https://cdn.example/second.png",
        "source_url": second_url,
    })
    assert applied["notification"]["slot_index"] == 1
    assert applied["notification"]["slot_status"] == "completed"
    status, content, _ = _message(database, task_ids[0])
    assert status == "pending"
    assert [(part["slot_index"], part["slot_status"]) for part in content] == [
        (0, "pending"), (1, "completed"),
    ]

    first = _seed_terminal_event(
        database, actions[0], "https://provider.example/failed.png",
        event_type="action.failed",
    )
    _apply_projection(database, first)
    status, content, credits = _message(database, task_ids[0])
    assert status == "completed"
    assert credits == unit_credits
    assert content[0]["slot_status"] == "failed"
    assert content[0]["failed"] is True
    assert content[1]["slot_status"] == "completed"
    assert content[1]["url"] == "https://cdn.example/second.png"
    assert [part["slot_index"] for part in content] == [0, 1]


def test_progress_cancel_and_old_revision_are_monotonic(database: str) -> None:
    task_ids, actions, _ = _prepare_batch(database)
    accepted = _seed_progress_event(database, actions[0], "action.accepted")
    _apply_projection(database, accepted)
    unknown = _seed_progress_event(database, actions[1], "action.unknown")
    _apply_projection(database, unknown)
    status, content, _ = _message(database, task_ids[0])
    assert status == "pending"
    assert [part["slot_status"] for part in content] == ["accepted", "unknown"]

    reconciled = _seed_progress_event(database, actions[1], "action.accepted")
    _apply_projection(database, reconciled)
    assert _message(database, task_ids[0])[1][1]["slot_status"] == "accepted"
    regressed = _seed_progress_event(database, actions[1], "action.unknown")
    _apply_projection(database, regressed)
    assert _message(database, task_ids[0])[1][1]["slot_status"] == "accepted"

    cancelled = _seed_progress_event(database, actions[1], "action.cancelled")
    _apply_projection(database, cancelled)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_prepared_image_batch_slots SET slot_revision=100 "
            "WHERE action_id=%s", (actions[1],),
        )
    stale = _seed_progress_event(database, actions[1], "action.unknown")
    first_apply = _apply_projection(database, stale)
    assert first_apply["notification"]["slot_status"] == "cancelled"
    with _projection_connection(database) as connection:
        replay = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
            (
                stale,
                uuid4(),
                "action_progress",
            ),
        ).fetchone()[0]
    assert replay["outcome"] == "already_applied"
    with psycopg.connect(database) as connection:
        slot = connection.execute(
            "SELECT slot_status,slot_revision FROM "
            "agent_runtime_prepared_image_batch_slots WHERE action_id=%s",
            (actions[1],),
        ).fetchone()
    assert slot == ("cancelled", 100)
