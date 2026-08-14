from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    AttemptFact,
    BatchFact,
    _seed_batch,
    _worker_call,
)
from tests.test_agent_runtime_media_atomic_image_batch_postgres_external import (
    _set_ready,
)
from tests.test_agent_runtime_media_atomic_image_batch_ownership_postgres_external import (
    _submit_v2,
)
from tests.test_agent_runtime_media_manifest_readback_postgres_external import (
    _create_legacy_prepared_task,
    _seed_attempt_fence,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _projection_connection,
)
from tests.test_agent_runtime_media_real_event_normalization_postgres_external import (
    _event_outbox,
    _insert_provider_fact,
    _provider_request,
)
from tests.test_agent_runtime_media_real_event_terminal_postgres_external import (
    _complete_action,
)
from tests.runtime_media_real_image_event_helpers import finalize_after_cancel


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
I1 = ROOT / "migrations/228_08i1_agent_runtime_media_real_image_event_normalization.sql"
I2 = ROOT / "migrations/228_08i2_agent_runtime_media_model_image_wecom_outbox.sql"
CHAT_ACTION_SUBMISSION = tuple(ROOT / "migrations" / name for name in (
    "227_63_agent_runtime_chat_action_submission.sql",
    "227_67_agent_runtime_chat_action_catalog_fix.sql",
))
ACTION_EVENTS = {
    "action.requested", "action.accepted", "action.unknown",
    "action.completed", "action.failed", "action.rejected", "action.cancelled",
    "action.provider.accepted", "action.provider.unknown",
    "action.completed_after_cancel", "action.failed_after_cancel",
}
RUN_ACTIONS = {
    "run.created": "run_pending",
    "run.claimed": "run_running",
    "run.resumed": "run_running",
    "run.waiting": "run_waiting",
    "run.completed": "run_completed",
    "run.failed": "run_failed",
    "run.cancelled": "run_cancelled",
}


def _install(database_url: str) -> None:
    from tests.test_agent_runtime_media_real_event_normalization_postgres_external import (
        _install as install_real_events,
    )
    install_real_events(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for migration in CHAT_ACTION_SUBMISSION:
            connection.execute(migration.read_text(encoding="utf-8"))
        connection.execute(I1.read_text(encoding="utf-8"))
        connection.execute(I2.read_text(encoding="utf-8"))


def _prepare_prepared_images(database_url: str, count: int) -> BatchFact:
    _install(database_url)
    source = _seed_batch(database_url, 1, credits=10_000)
    _set_ready(database_url)
    tasks = [
        _create_legacy_prepared_task(
            database_url, source.attempts[0], kind="image",
            model="gpt-image-2-image-to-image",
        )
        for _ in range(count)
    ]
    batch_id = str(uuid4())
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for index, task_id in enumerate(tasks):
            connection.execute(
                "UPDATE tasks SET batch_id=%s,image_index=%s,"
                "delivery_context=delivery_context||%s WHERE id=%s",
                (batch_id, index, Jsonb({"channel": "web"}), task_id),
            )
        anchor = connection.execute(
            "SELECT conversation_id,org_id,user_id,input_message_id,"
            "assistant_message_id,turn_id,model_id FROM tasks WHERE id=%s",
            (tasks[0],),
        ).fetchone()
        requests = connection.execute(
            "SELECT id,request_params FROM tasks WHERE id=ANY(%s) "
            "ORDER BY image_index", (tasks,),
        ).fetchall()
        connection.execute(
            "UPDATE messages SET generation_params=%s WHERE id=%s",
            (Jsonb({"type": "image", "num_images": count}), anchor[4]),
        )
    items = [{
        "task_id": str(task_id),
        "idempotency_key": f"real-image-events:{task_id}",
        "arguments": request,
    } for task_id, request in requests]
    receipt = _submit_v2(database_url, batch_id, (*anchor, items))
    assert receipt["runtime_owned"] is True
    attempts: list[AttemptFact] = []
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        actions = connection.execute("""
            SELECT binding.action_id,action.request_hash,action.model_step_id,
                   receipt.id,receipt.executor_type,receipt.executor_revision,
                   receipt.policy_revision,task.image_index
              FROM agent_runtime_prepared_media_action_bindings binding
              JOIN agent_actions action ON action.id=binding.action_id
              JOIN agent_policy_receipts receipt ON receipt.action_id=action.id
              JOIN tasks task ON task.id=binding.task_id
             WHERE task.id=ANY(%s) ORDER BY task.image_index
        """, (tasks,)).fetchall()
        for action in actions:
            attempt_id, token = uuid4(), uuid4()
            connection.execute(
                "UPDATE agent_actions SET status='running',"
                "started_at=clock_timestamp() WHERE id=%s", (action[0],),
            )
            connection.execute("""
                INSERT INTO agent_action_attempts(
                  id,action_id,session_id,run_id,org_id,user_id,attempt_number,
                  status,dispatch_phase,worker_id,execution_token,lease_expires_at,
                  idempotency_key,request_hash,retry_disposition,state_version
                ) SELECT %s,action.id,action.session_id,action.run_id,action.org_id,
                  action.user_id,1,'claimed','claimed','media-worker',%s,
                  clock_timestamp()+interval '10 minutes',%s,action.request_hash,
                  action.retry_disposition,0 FROM agent_actions action WHERE id=%s
            """, (
                attempt_id, token, f"attempt:{attempt_id}", action[0],
            ))
            connection.execute("""
                INSERT INTO agent_action_dispatch_intents(
                  attempt_id,action_id,policy_receipt_id,execution_token,
                  request_hash,executor_type,executor_revision,policy_revision,
                  external_idempotency_key,recovery_mode
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'idempotent_replay')
            """, (
                attempt_id, action[0], action[3], token, action[1], action[4],
                action[5], action[6], f"media:{action[0]}",
            ))
            attempts.append(AttemptFact(action[0], attempt_id, token, action[1]))
    for fact in attempts:
        _seed_attempt_fence(database_url, fact)
    return BatchFact(actions[0][2], anchor[4], tuple(attempts))


def _prepare_images(database_url: str, count: int, *, prepared: bool = False):
    if prepared:
        return _prepare_prepared_images(database_url, count)
    _install(database_url)
    batch = _seed_batch(database_url, count, credits=10_000)
    for fact in batch.attempts:
        _seed_attempt_fence(database_url, fact)
    _set_ready(database_url)
    result = _worker_call(
        database_url, "prepare_agent_runtime_media_dispatch_v1",
        batch.attempts[0],
    )
    assert result["outcome"] == "prepared"
    return batch


def _apply_until(
    database_url: str, target_outbox: UUID,
    content_part: dict[str, object] | None = None,
) -> dict[str, object]:
    for _ in range(100):
        with _projection_connection(database_url) as connection:
            claims = connection.execute(
                "SELECT claim_agent_runtime_media_projection_v1(1,30)",
            ).fetchone()[0]
            assert len(claims) == 1
            claim = claims[0]
            outbox_id = UUID(str(claim["id"]))
            lease = UUID(str(claim["lease_token"]))
            readback = connection.execute(
                "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
                (outbox_id, lease),
            ).fetchone()[0]
            event_type = str(readback["event"]["event_type"])
            action_facts = readback.get("action_facts") or {}
            normalized_action = (
                readback["event"].get("action_id") is not None
                or action_facts.get("outcome") == "found"
            )
            projection_action = (
                "action_progress" if event_type in ACTION_EVENTS
                and normalized_action else RUN_ACTIONS.get(
                    event_type, "checkpoint_only",
                )
            )
            result = connection.execute(
                "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,%s)",
                (outbox_id, lease, projection_action,
                 Jsonb(content_part)
                 if outbox_id == target_outbox and content_part else None),
            ).fetchone()[0]
        if outbox_id == target_outbox:
            return result
    raise AssertionError("target image projection was not reached")


def _content(index: int) -> dict[str, object]:
    source = f"https://provider.example/image-{index}.png"
    return {
        "type": "image", "url": f"https://cdn.example/image-{index}.png",
        "source_url": source, "download_url": source,
        "storage_provider": "workspace", "storage_key": f"runtime/{index}.png",
        "name": f"image-{index}.png", "mime_type": "image/png", "size": 2048,
    }


def _record_provider_state(database_url: str, fact, state: str) -> UUID:
    provider_hash, _, submission_id = _provider_request(database_url, fact)
    provider_key = hashlib.sha256(
        f"real-image:{fact.action_id}".encode()
    ).hexdigest()
    if state == "accepted":
        _insert_provider_fact(
            database_url, fact, submission_id, provider_key, "submitted",
            task_ref=f"kie-image-{fact.action_id}",
        )
        receipt = Jsonb({
            "provider": "kie", "provider_task_ref": f"kie-image-{fact.action_id}",
            "evidence": {
                "provider_request_hash": provider_hash,
                "provider_idempotency_key": provider_key,
                "submission_id": str(submission_id), "state_version": 0,
                "provider_fact_state": "submitted",
            },
        })
        result = _worker_rpc(
            database_url, "record_agent_runtime_media_provider_submission_v1", (
                fact.attempt_id, fact.token, fact.request_hash, "kie",
                f"kie-image-{fact.action_id}", "/api/v1/jobs/recordInfo", None,
                provider_key, provider_hash,
                datetime.now(timezone.utc) + timedelta(minutes=2), receipt,
            ),
        )
        event_type = "action.provider.accepted"
    else:
        version = _insert_provider_fact(
            database_url, fact, submission_id, provider_key, "unknown",
        )
        receipt = {
            "provider": "kie", "provider_task_ref": None, "state": "unknown",
            "evidence": {
                "error_code": "KIE_SUBMIT_RESULT_UNKNOWN",
                "submission_id": str(submission_id), "state_version": 1,
                "provider_fact_state": "unknown",
                "provider_request_hash": provider_hash,
                "provider_idempotency_key": provider_key,
            },
        }
        result = _worker_rpc(
            database_url, "record_agent_runtime_media_provider_unknown_v1", (
                fact.attempt_id, fact.token, version, fact.request_hash,
                Jsonb(receipt), Jsonb(receipt),
                datetime.now(timezone.utc) + timedelta(minutes=2),
            ),
        )
        event_type = "action.provider.unknown"
    assert result["outcome"] == state
    return _event_outbox(database_url, fact.action_id, event_type, "web_runtime")[1]


def _finalize_after_cancel(
    database_url: str, fact, terminal: str,
) -> UUID:
    return finalize_after_cancel(database_url, fact, terminal)


def test_model_loop_four_images_complete_in_real_out_of_order(database: str) -> None:
    batch = _prepare_images(database, 4)
    ordered = [batch.attempts[index] for index in (0, 3, 1, 2)]
    targets = []
    for index, fact in enumerate(ordered):
        targets.append((fact, _complete_action(
            database, fact, f"https://provider.example/image-{index}.png",
        ), index))
    for fact, outbox_id, index in targets:
        applied = _apply_until(database, outbox_id, _content(index))
        assert applied["result"]["action_id"] == str(fact.action_id)
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT array_agg(task.status::TEXT ORDER BY binding.action_index),
                   message.content::JSONB,
                   bool_and(event.action_id IS NULL),
                   bool_and(event.correlation_id=binding.action_id)
              FROM agent_runtime_media_action_bindings binding
              JOIN tasks task ON task.id=binding.task_id
              JOIN messages message ON message.id=binding.output_message_id
              JOIN agent_runtime_events event
                ON event.correlation_id=binding.action_id
               AND event.event_type='action.completed'
             WHERE binding.model_step_id=%s GROUP BY message.content
        """, (batch.step_id,)).fetchone()
    assert state[0] == ["completed"] * 4
    assert [part["slot_status"] for part in state[1]] == ["completed"] * 4
    assert state[2:] == (True, True)


def test_prepared_ten_images_merge_real_accepted_unknown_and_terminals(
    database: str,
) -> None:
    batch = _prepare_images(database, 10, prepared=True)
    accepted = _record_provider_state(database, batch.attempts[7], "accepted")
    unknown = _record_provider_state(database, batch.attempts[2], "unknown")
    _apply_until(database, accepted)
    _apply_until(database, unknown)
    terminal_facts = [
        batch.attempts[index] for index in (9, 0, 5, 1, 8, 3, 6, 4)
    ]
    for index, fact in enumerate(terminal_facts):
        outbox = _complete_action(
            database, fact, f"https://provider.example/image-{index}.png",
        )
        _apply_until(database, outbox, _content(index))
    with psycopg.connect(database) as connection:
        content, raw = connection.execute("""
            SELECT message.content::JSONB,
                   bool_and(event.action_id IS NULL)
              FROM messages message
              JOIN agent_runtime_prepared_media_action_bindings binding
                ON binding.output_message_id=message.id
              JOIN agent_runtime_events event
                ON event.correlation_id=binding.action_id
             WHERE message.id=%s AND event.event_type IN(
                 'action.provider.accepted','action.provider.unknown',
                 'action.completed')
             GROUP BY message.content
        """, (batch.output_id,)).fetchone()
    assert len(content) == 10
    assert [part["slot_index"] for part in content] == list(range(10))
    assert content[7]["slot_status"] == "accepted"
    assert content[2]["slot_status"] == "unknown"
    assert raw is True


@pytest.mark.parametrize(
    ("terminal", "expected_task", "expected_credit"),
    (("completed", "completed", "confirmed"),
     ("failed", "failed", "refunded")),
)
def test_prepared_single_image_real_after_cancel(
    database: str, terminal: str, expected_task: str, expected_credit: str,
) -> None:
    batch = _prepare_images(database, 1, prepared=True)
    fact = batch.attempts[0]
    outbox = _finalize_after_cancel(database, fact, terminal)
    content = (
        _content(0) | {
            "source_url": "https://provider.example/late-image.png",
            "download_url": "https://provider.example/late-image.png",
        }
        if terminal == "completed" else None
    )
    projected = _apply_until(database, outbox, content)
    assert projected["result"]["action_id"] == str(fact.action_id)
    assert projected["result"]["projection_action"] == "action_progress"
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT event.action_id,event.correlation_id,task.status::TEXT,
                   binding.credit_state
              FROM agent_runtime_events event
              JOIN agent_runtime_prepared_media_action_bindings binding
                ON binding.action_id=event.correlation_id
              JOIN tasks task ON task.id=binding.task_id
             WHERE event.id=(SELECT event_id FROM agent_projection_outbox
                              WHERE id=%s)
        """, (outbox,)).fetchone()
    assert state == (None, fact.action_id, expected_task, expected_credit)


def test_wrong_image_correlation_stays_checkpoint_only(database: str) -> None:
    batch = _prepare_images(database, 1)
    fact = batch.attempts[0]
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute(
            "SELECT session_id,run_id,model_step_id FROM agent_actions WHERE id=%s",
            (fact.action_id,),
        ).fetchone()
        appended = connection.execute("""
            SELECT append_agent_runtime_event(
                %s,'action.provider.unknown',%s,%s,%s,'executor','bad-image',
                '{"error_code":"wrong_correlation"}'::JSONB,
                ARRAY['web_runtime']::TEXT[])
        """, (action[0], action[1], action[2], uuid4())).fetchone()[0]
        outbox = connection.execute(
            "SELECT id FROM agent_projection_outbox WHERE event_id=%s",
            (appended["event_id"],),
        ).fetchone()[0]
    result = _apply_until(database, outbox)
    assert result["result"]["projection_action"] == "checkpoint_only"
    assert result["result"]["action_id"] is None


@pytest.mark.parametrize(
    ("event_type", "actor_type", "action_status", "attempt_status",
     "expected_slot", "expected_task", "expected_credit"),
    (
        ("action.unknown", "system", "unknown", "unknown",
         "unknown", "running", "pending"),
        ("action.unknown", "executor", "unknown", "unknown",
         "unknown", "running", "pending"),
        ("action.rejected", "system", "rejected", "cancelled",
         "failed", "failed", "refunded"),
        ("action.cancelled", "system", "cancelled", "cancelled",
         "cancelled", "cancelled", "refunded"),
        ("action.cancelled", "executor", "cancelled", "cancelled",
         "cancelled", "cancelled", "refunded"),
        ("action.cancelled", "reconciler", "cancelled", "cancelled",
         "cancelled", "cancelled", "refunded"),
    ),
)
def test_existing_image_event_actor_contracts_are_normalized(
    database: str, event_type: str, actor_type: str, action_status: str,
    attempt_status: str, expected_slot: str, expected_task: str,
    expected_credit: str,
) -> None:
    batch = _prepare_images(database, 1)
    fact = batch.attempts[0]
    terminal = action_status in {"rejected", "cancelled"}
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        scope = connection.execute(
            "SELECT session_id,run_id,model_step_id FROM agent_actions WHERE id=%s",
            (fact.action_id,),
        ).fetchone()
        connection.execute(
            "UPDATE agent_actions SET status=%s,terminal_reason=%s,"
            "completed_at=CASE WHEN %s THEN clock_timestamp() END WHERE id=%s",
            (action_status, event_type, terminal, fact.action_id),
        )
        connection.execute(
            "UPDATE agent_action_attempts SET status=%s,"
            "ambiguity_evidence=CASE WHEN %s='unknown' "
            "THEN '{\"kind\":\"actor_contract\"}'::JSONB ELSE '{}'::JSONB END,"
            "ended_at=CASE WHEN %s='cancelled' THEN clock_timestamp() END "
            "WHERE id=%s",
            (attempt_status, attempt_status, attempt_status, fact.attempt_id),
        )
        appended = connection.execute(
            "SELECT append_agent_runtime_event(%s,%s,%s,%s,%s,%s,"
            "'actor-contract-test','{}'::JSONB,ARRAY['web_runtime']::TEXT[])",
            (scope[0], event_type, scope[1], scope[2], fact.action_id, actor_type),
        ).fetchone()[0]
        outbox = connection.execute(
            "SELECT id FROM agent_projection_outbox WHERE event_id=%s",
            (appended["event_id"],),
        ).fetchone()[0]
    projected = _apply_until(database, outbox)
    assert projected["result"]["action_id"] == str(fact.action_id)
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT message.content::JSONB->0->>'slot_status',task.status::TEXT,
                   binding.credit_state
              FROM agent_runtime_media_action_bindings binding
              JOIN tasks task ON task.id=binding.task_id
              JOIN messages message ON message.id=binding.output_message_id
             WHERE binding.action_id=%s
        """, (fact.action_id,)).fetchone()
    assert state == (expected_slot, expected_task, expected_credit)
