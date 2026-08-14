from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_media_real_event_normalization_postgres_external import (
    _apply_outbox, _event_outbox, _insert_provider_fact, _install,
    _prepare_video, _provider_request,
)


pytestmark = pytest.mark.external


def _complete_action(database: str, fact, source_url: str) -> UUID:
    dispatched = _worker_rpc(database, "mark_agent_action_dispatching_v2", (
        fact.attempt_id, fact.token, 0, fact.request_hash,
    ))
    assert dispatched["outcome"] == "dispatching"
    result = {
        "status": "success", "summary": "video generated",
        "data": {"result_urls": [source_url]}, "artifact_ids": [],
        "usage": {}, "cost": {}, "external_receipt": {},
    }
    completed = _worker_rpc(database, "complete_agent_action_v2", (
        fact.attempt_id, fact.token, dispatched["state_version"],
        fact.request_hash, Jsonb(result),
    ))
    assert completed["outcome"] == "completed"
    return _event_outbox(
        database, fact.action_id, "action.completed", "web_runtime",
    )[1]


def _complete_run(database: str, action_id: UUID, final_text: str) -> UUID:
    claim = _worker_rpc(database, "claim_next_agent_run", (
        "model-video-final", 90, 3,
    ))
    assert claim["outcome"] == "claimed"
    final_step = uuid4()
    content_hash = hashlib.sha256(final_text.encode()).hexdigest()
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute("""
            SELECT session_id,run_id,org_id,user_id
              FROM agent_actions WHERE id=%s
        """, (action_id,)).fetchone()
        connection.execute("""
            INSERT INTO agent_model_steps(
                id,run_id,session_id,org_id,user_id,step_number,status,model_id,
                provider,model_revision,prompt_revision,tool_catalog_revision,
                request_receipt,response_receipt,stop_reason,completed_at
            ) VALUES(%s,%s,%s,%s,%s,2,'completed','qwen3.5-plus','dashscope',
                     'v1','batch-media-v1','catalog-v7','{}','{}','final',
                     clock_timestamp())
        """, (final_step, action[1], action[0], action[2], action[3]))
        connection.execute("""
            INSERT INTO agent_model_results(
                model_step_id,run_id,session_id,org_id,user_id,output_kind,
                text_content,content_hash
            ) VALUES(%s,%s,%s,%s,%s,'text',%s,%s)
        """, (
            final_step, action[1], action[0], action[2], action[3],
            final_text, content_hash,
        ))
    completed = _worker_rpc(database, "complete_agent_run", (
        claim["entity_id"], claim["execution_token"],
        claim["state_version"], content_hash,
    ))
    assert completed["outcome"] == "completed"
    return UUID(str(claim["entity_id"]))


def _apply_run_event(
    database: str, action_id: UUID, event_type: str, projection_action: str,
) -> None:
    outbox_id = _event_outbox(
        database, action_id, event_type, "web_runtime",
    )[1]
    from tests.test_agent_runtime_media_projection_postgres_external import (
        _projection_connection,
    )
    with _projection_connection(database) as connection:
        claims = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,30)",
        ).fetchone()[0]
        claim = next(
            item for item in claims if UUID(str(item["id"])) == outbox_id
        )
        result = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
            (outbox_id, UUID(str(claim["lease_token"])), projection_action),
        ).fetchone()[0]
    assert result["outcome"] == "applied"


@pytest.mark.parametrize("channel", ("web", "wecom"))
def test_real_action_resume_and_run_terminal_projection(
    database: str, channel: str,
) -> None:
    _install(database)
    batch, fact = _prepare_video(database, channel)
    source_url = "https://provider.example/runtime-model-video.mp4"
    action_outbox = _complete_action(database, fact, source_url)
    action_result = _apply_outbox(database, action_outbox, {
        "type": "video", "url": "https://cdn.example/runtime-model-video.mp4",
        "source_url": source_url, "download_url": source_url,
        "storage_provider": "workspace", "storage_key": "runtime/video.mp4",
        "name": "video.mp4", "mime_type": "video/mp4", "size": 4096,
    }, batch_size=1)
    assert action_result["result"]["slot_status"] == "completed"
    _apply_run_event(database, fact.action_id, "run.resumed", "run_running")
    run_id = _complete_run(database, fact.action_id, "Final video explanation")
    with psycopg.connect(database) as connection:
        outboxes = connection.execute("""
            SELECT outbox.projection_kind,outbox.id,event.action_id
              FROM agent_runtime_events event
              JOIN agent_projection_outbox outbox ON outbox.event_id=event.id
             WHERE event.run_id=%s AND event.event_type='run.completed'
             ORDER BY outbox.projection_kind
        """, (run_id,)).fetchall()
    kinds = [row[0] for row in outboxes]
    assert kinds == (
        ["audit", "web_runtime", "wecom"]
        if channel == "wecom" else ["audit", "web_runtime"]
    )
    assert all(row[2] is None for row in outboxes)
    target_kind = "wecom" if channel == "wecom" else "web_runtime"
    run_outbox = next(row[1] for row in outboxes if row[0] == target_kind)
    with _projection_apply_run(database, run_outbox) as result:
        assert result["result"]["projection_action"] == "run_completed"
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT parent.status::TEXT,parent.credits_used,message.status::TEXT,
                   message.content::JSONB,
                   (SELECT count(*) FROM conversation_deliveries),
                   (SELECT count(*)
                      FROM agent_runtime_media_wecom_outbox_facts_v1)
              FROM agent_actions action
              JOIN agent_runs run ON run.id=action.run_id
              JOIN agent_session_commands command ON command.id=run.command_id
              JOIN tasks parent ON parent.id=(command.payload->>'task_id')::UUID
              JOIN messages message ON message.id=parent.assistant_message_id
             WHERE action.id=%s
        """, (fact.action_id,)).fetchone()
    assert state[:3] == ("completed", 31, "completed")
    assert [part["type"] for part in state[3]] == ["video", "text"]
    assert state[4:] == ((1, 1) if channel == "wecom" else (0, 0))


class _projection_apply_run:
    def __init__(self, database: str, outbox_id: UUID) -> None:
        self.database = database
        self.outbox_id = outbox_id
        self.connection = None
        self.result = None

    def __enter__(self):
        from tests.test_agent_runtime_media_projection_postgres_external import (
            _projection_connection,
        )
        self.connection = _projection_connection(self.database)
        claims = self.connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(50,30)",
        ).fetchone()[0]
        claim = next(
            item for item in claims
            if UUID(str(item["id"])) == self.outbox_id
        )
        lease = UUID(str(claim["lease_token"]))
        self.result = self.connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,'run_completed',NULL)",
            (self.outbox_id, lease),
        ).fetchone()[0]
        self.connection.commit()
        return self.result

    def __exit__(self, exc_type, exc_value, traceback):
        assert self.connection is not None
        self.connection.close()


def _apply_in_order_until(
    database: str, target_outbox: UUID,
    content_part: dict[str, object] | None,
) -> dict[str, object]:
    from tests.test_agent_runtime_media_projection_postgres_external import (
        _projection_connection,
    )
    action_events = {
        "action.requested", "action.accepted", "action.unknown",
        "action.completed", "action.failed", "action.rejected",
        "action.cancelled", "action.provider.accepted",
        "action.provider.unknown", "action.completed_after_cancel",
        "action.failed_after_cancel",
    }
    for _ in range(20):
        with _projection_connection(database) as connection:
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
            projection_action = (
                "action_progress" if event_type in action_events
                else "checkpoint_only"
            )
            result = connection.execute(
                "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,%s)",
                (outbox_id, lease, projection_action,
                 Jsonb(content_part)
                 if outbox_id == target_outbox and content_part else None),
            ).fetchone()[0]
        if outbox_id == target_outbox:
            return result
    raise AssertionError("target projection outbox was not reached")


@pytest.mark.parametrize(
    ("terminal", "event_type", "expected_task", "expected_credit"),
    (("completed", "action.completed_after_cancel", "completed", "confirmed"),
     ("failed", "action.failed_after_cancel", "failed", "refunded")),
)
def test_real_after_cancel_finalize_maps_to_logical_terminal(
    database: str, terminal: str, event_type: str,
    expected_task: str, expected_credit: str,
) -> None:
    _install(database)
    _batch, fact = _prepare_video(database, "web")
    provider_hash, provider_key, submission_id = _provider_request(database, fact)
    _insert_provider_fact(
        database, fact, submission_id, provider_key, "submitted",
        task_ref="kie-video-after-cancel",
    )
    accepted_receipt = {
        "provider": "kie", "provider_task_ref": "kie-video-after-cancel",
        "evidence": {
            "provider_request_hash": provider_hash,
            "provider_idempotency_key": provider_key,
            "submission_id": str(submission_id), "state_version": 0,
            "provider_fact_state": "submitted",
        },
    }
    assert _worker_rpc(database, "record_agent_runtime_media_provider_submission_v1", (
        fact.attempt_id, fact.token, fact.request_hash, "kie",
        "kie-video-after-cancel", "/api/v1/jobs/recordInfo", None,
        provider_key, provider_hash,
        datetime.now(timezone.utc) + timedelta(minutes=2),
        Jsonb(accepted_receipt),
    ))["outcome"] == "accepted"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runs SET status='cancelled',blocking_action_count=0,"
            "execution_token=NULL,lease_expires_at=NULL,completed_at=clock_timestamp(),"
            "state_version=state_version+1 WHERE id=(SELECT run_id FROM "
            "agent_actions WHERE id=%s)", (fact.action_id,),
        )
    cancel_claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", (
        "video-cancel", 120, 0,
    ))
    cancel_fact = _worker_rpc(database, "request_agent_runtime_provider_cancel", (
        submission_id, fact.token, fact.request_hash, 0, "runtime_cancel_unproven",
    ))
    cancel_receipt = {
        "provider": "kie", "provider_task_ref": "kie-video-after-cancel",
        "status_locator": "/api/v1/jobs/recordInfo", "state": "unknown",
        "evidence": {
            "error_code": "CANCEL_UNPROVEN", "cancel_unproven": True,
            "submission_id": str(submission_id),
            "state_version": cancel_fact["state_version"],
            "provider_fact_state": "cancel_requested",
            "provider_request_hash": provider_hash,
            "provider_idempotency_key": provider_key,
        },
    }
    assert _worker_rpc(database, "record_agent_runtime_media_cancel_unproven_v1", (
        fact.attempt_id, cancel_claim["execution_token"],
        cancel_claim["state_version"], fact.request_hash,
        Jsonb(cancel_receipt), Jsonb(cancel_receipt),
        datetime.now(timezone.utc) + timedelta(minutes=2),
    ))["outcome"] == "still_unknown"
    readback = _worker_rpc(database, "record_agent_runtime_provider_readback", (
        submission_id, fact.token, fact.request_hash,
        cancel_fact["state_version"], terminal, "e" * 64,
        "kie-video-after-cancel", "/api/v1/jobs/recordInfo",
        Jsonb({"provider_state": terminal}),
    ))
    final_claim = _worker_rpc(database, "claim_next_agent_action_reconciliation", (
        "video-cancel-final", 120, 0,
    ))
    receipt = {
        **cancel_receipt, "state": terminal,
        "evidence": {
            **cancel_receipt["evidence"],
            "state_version": readback["state_version"],
            "provider_fact_state": (
                "readback_confirmed" if terminal == "completed" else "failed"
            ),
            "provider_state": terminal,
        },
    }
    source_url = "https://provider.example/late-video.mp4"
    result = {
        "status": "success" if terminal == "completed" else "error",
        "summary": f"{terminal} after cancel",
        "data": ({"result_urls": [source_url]} if terminal == "completed"
                 else {"error_code": "provider_failed"}),
        "artifact_ids": [], "usage": {}, "cost": {},
        "external_receipt": receipt,
        **({} if terminal == "completed" else {"error_code": "provider_failed"}),
    }
    finalized = _worker_rpc(database, "finalize_agent_runtime_media_after_cancel_v1", (
        fact.attempt_id, None, final_claim["execution_token"],
        final_claim["state_version"], fact.request_hash, terminal,
        Jsonb(receipt), Jsonb(result), None, 0, 0, "credits", "runtime", "f" * 64,
    ))
    assert finalized["outcome"] == terminal
    event_id, outbox_id = _event_outbox(
        database, fact.action_id, event_type, "web_runtime",
    )
    content = None if terminal == "failed" else {
        "type": "video", "url": "https://cdn.example/late-video.mp4",
        "source_url": source_url, "download_url": source_url,
        "storage_provider": "workspace", "storage_key": "runtime/late.mp4",
        "name": "late.mp4", "mime_type": "video/mp4", "size": 2048,
    }
    projected = _apply_in_order_until(database, outbox_id, content)
    assert projected["result"]["slot_status"] == expected_task
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT event.action_id,event.correlation_id,task.status::TEXT,
                   binding.credit_state
              FROM agent_runtime_events event
              JOIN agent_runtime_prepared_media_action_bindings binding
                ON binding.action_id=event.correlation_id
              JOIN tasks task ON task.id=binding.task_id
             WHERE event.id=%s
        """, (event_id,)).fetchone()
    assert state == (None, fact.action_id, expected_task, expected_credit)
