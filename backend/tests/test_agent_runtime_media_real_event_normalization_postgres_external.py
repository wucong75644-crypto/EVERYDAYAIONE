from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import _connect, _settings, database
from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_media_action_bindings_postgres_external import _worker_call
from tests.test_agent_runtime_media_model_video_postgres_external import (
    _apply_predecessors, _seed_model_video, _set_ready,
)
from tests.test_agent_runtime_media_model_video_projection_fence_postgres_external import (
    _set_wecom_parent,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _projection_connection,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
BASE = tuple(ROOT / "migrations" / name for name in (
    "228_08a_agent_runtime_media_model_video.sql",
    "228_08b_agent_runtime_media_wecom_delivery.sql",
    "228_08c_agent_runtime_media_worker_scope.sql",
    "228_08d_agent_runtime_media_atomic_image_batch.sql",
    "228_08e1_agent_runtime_media_model_video_fence.sql",
    "228_08e2_agent_runtime_media_model_video_projection.sql",
    "228_08f1_agent_runtime_media_prepared_image_batch_projection.sql",
    "228_08f2_agent_runtime_media_atomic_image_batch_ownership.sql",
))
G1 = ROOT / "migrations/228_08g1_agent_runtime_media_real_event_normalization.sql"
G2 = ROOT / "migrations/228_08g2_agent_runtime_media_model_video_wecom_outbox.sql"
G1_ROLLBACK = ROOT / (
    "migrations/rollback/228_08g1_agent_runtime_media_real_event_normalization_rollback.sql"
)
G2_ROLLBACK = ROOT / (
    "migrations/rollback/228_08g2_agent_runtime_media_model_video_wecom_outbox_rollback.sql"
)


def _install(database_url: str, *, include_g2: bool = True) -> None:
    _apply_predecessors(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for migration in BASE:
            connection.execute(migration.read_text(encoding="utf-8"))
        connection.execute(G1.read_text(encoding="utf-8"))
        if include_g2:
            connection.execute(G2.read_text(encoding="utf-8"))


def _event_outbox(
    database_url: str, action_id: UUID, event_type: str, kind: str,
) -> tuple[UUID, UUID]:
    with psycopg.connect(database_url) as connection:
        row = connection.execute("""
            SELECT event.id,outbox.id
              FROM agent_runtime_events event
              JOIN agent_projection_outbox outbox ON outbox.event_id=event.id
             WHERE event.correlation_id=%s AND event.event_type=%s
               AND outbox.projection_kind=%s
             ORDER BY event.sequence DESC LIMIT 1
        """, (action_id, event_type, kind)).fetchone()
    assert row is not None
    return row


def _apply_outbox(
    database_url: str, outbox_id: UUID,
    content_part: dict[str, object] | None = None,
    *, batch_size: int = 50,
) -> dict[str, object]:
    with _projection_connection(database_url) as connection:
        claims = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(%s,30)",
            (batch_size,),
        ).fetchone()[0]
        claim = next(item for item in claims if UUID(str(item["id"])) == outbox_id)
        lease = UUID(str(claim["lease_token"]))
        readback = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, lease),
        ).fetchone()[0]
        assert readback["outcome"] == "found"
        return connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,%s)",
            (outbox_id, lease, "action_progress",
             Jsonb(content_part) if content_part else None),
        ).fetchone()[0]


def _prepare_video(database_url: str, channel: str = "wecom"):
    batch = _seed_model_video(database_url, channel)
    fact = batch.attempts[0]
    if channel == "wecom":
        _set_wecom_parent(database_url, fact.action_id)
    _set_ready(database_url)
    assert _worker_call(
        database_url, "prepare_agent_runtime_media_dispatch_v1", fact,
    )["outcome"] == "prepared"
    return batch, fact


def _provider_request(database_url: str, fact) -> tuple[str, str, UUID]:
    request = _worker_call(
        database_url, "read_agent_runtime_media_provider_request_v1", fact,
    )
    provider_key = "c" * 64
    submission_id = uuid4()
    return str(request["provider_request_hash"]), provider_key, submission_id


def _insert_provider_fact(
    database_url: str, fact, submission_id: UUID, provider_key: str,
    state: str, *, task_ref: str | None = None,
) -> int:
    status_locator = "/api/v1/jobs/recordInfo" if task_ref else None
    ambiguity = Jsonb(
        {"error_code": "timeout"} if state == "unknown" else {}
    )
    fact_version = 1 if state == "unknown" else 0
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        version = connection.execute(
            "UPDATE agent_action_attempts SET status='dispatching',"
            "dispatch_phase='request_started' WHERE id=%s RETURNING state_version",
            (fact.attempt_id,),
        ).fetchone()[0]
        connection.execute("""
            INSERT INTO agent_runtime_provider_submission_facts(
                id,attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,
                provider,provider_revision,external_idempotency_key,request_hash,
                execution_token,state,provider_task_ref,status_locator,
                ambiguity_evidence,state_version
            ) SELECT %s,attempt.id,attempt.action_id,attempt.run_id,attempt.org_id,
                     attempt.user_id,session.scope_kind,session.scope_id,'kie',
                     'kie-runtime-media-v1',%s,attempt.request_hash,
                     attempt.execution_token,%s,%s,%s,%s,%s
                FROM agent_action_attempts attempt
                JOIN agent_runtime_sessions session ON session.id=attempt.session_id
               WHERE attempt.id=%s
        """, (
            submission_id, provider_key, state, task_ref, status_locator,
            ambiguity, fact_version, fact.attempt_id,
        ))
    return version


@pytest.mark.parametrize(
    ("event_type", "expected_slot"),
    (("action.provider.accepted", "accepted"),
     ("action.provider.unknown", "unknown")),
)
def test_real_provider_events_normalize_without_action_id(
    database: str, event_type: str, expected_slot: str,
) -> None:
    _install(database)
    batch, fact = _prepare_video(database)
    provider_hash, provider_key, submission_id = _provider_request(database, fact)
    if expected_slot == "accepted":
        _insert_provider_fact(
            database, fact, submission_id, provider_key, "submitted",
            task_ref="kie-video-real-event",
        )
        receipt = Jsonb({
            "provider": "kie", "provider_task_ref": "kie-video-real-event",
            "evidence": {
                "provider_request_hash": provider_hash,
                "provider_idempotency_key": provider_key,
                "submission_id": str(submission_id), "state_version": 0,
                "provider_fact_state": "submitted",
            },
        })
        result = _worker_rpc(database, "record_agent_runtime_media_provider_submission_v1", (
            fact.attempt_id, fact.token, fact.request_hash, "kie",
            "kie-video-real-event", "/api/v1/jobs/recordInfo", None,
            provider_key, provider_hash,
            datetime.now(timezone.utc) + timedelta(minutes=2), receipt,
        ))
    else:
        version = _insert_provider_fact(
            database, fact, submission_id, provider_key, "unknown",
        )
        receipt = {
            "provider": "kie", "provider_task_ref": None,
            "status_locator": None, "state": "unknown",
            "evidence": {
                "error_code": "KIE_SUBMIT_RESULT_UNKNOWN",
                "submission_id": str(submission_id), "state_version": 1,
                "provider_fact_state": "unknown",
                "provider_request_hash": provider_hash,
                "provider_idempotency_key": provider_key,
            },
        }
        result = _worker_rpc(database, "record_agent_runtime_media_provider_unknown_v1", (
            fact.attempt_id, fact.token, version, fact.request_hash,
            Jsonb(receipt), Jsonb(receipt),
            datetime.now(timezone.utc) + timedelta(minutes=2),
        ))
    assert result["outcome"] == expected_slot
    event_id, outbox_id = _event_outbox(
        database, fact.action_id, event_type, "wecom",
    )
    projected = _apply_outbox(database, outbox_id)
    assert projected["result"]["action_id"] == str(fact.action_id)
    assert projected["result"]["slot_status"] == expected_slot
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT event.action_id,event.correlation_id,
                   message.content::JSONB->0->>'slot_status',
                   (SELECT count(*) FROM agent_action_attempts
                     WHERE action_id=%s),
                   (SELECT count(*) FROM conversation_deliveries)
              FROM agent_runtime_events event
              JOIN messages message ON message.id=%s
             WHERE event.id=%s
        """, (fact.action_id, batch.output_id, event_id)).fetchone()
    assert state == (None, fact.action_id, expected_slot, 1, 0)


def test_empty_rollback_order_and_reapply(database: str) -> None:
    _install(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        security = connection.execute("""
            SELECT
              (SELECT bool_and(class.relrowsecurity AND class.relforcerowsecurity)
                 FROM pg_class class
                WHERE class.oid=ANY(ARRAY[
                    'agent_runtime_media_normalized_projection_inputs_v1'::REGCLASS,
                    'agent_runtime_media_wecom_outbox_facts_v1'::REGCLASS
                ])),
              has_table_privilege(
                  'everydayai_agent_runtime_worker',
                  'agent_runtime_media_normalized_projection_inputs_v1','SELECT'),
              has_table_privilege(
                  'everydayai_projection_worker',
                  'agent_runtime_media_normalized_projection_inputs_v1','SELECT'),
              has_function_privilege(
                  'everydayai_projection_worker',
                  'apply_agent_runtime_media_projection_v1'
                  '(uuid,uuid,text,jsonb)','EXECUTE'),
              has_function_privilege(
                  'everydayai_projection_worker',
                  '_apply_agent_runtime_media_projection_228_06_v1'
                  '(uuid,uuid,text,jsonb)','EXECUTE'),
              has_function_privilege(
                  'everydayai_agent_runtime_worker',
                  '_derive_agent_runtime_model_video_wecom_outbox_v1()','EXECUTE')
        """).fetchone()
        assert security == (True, False, False, True, False, False)
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_228_08G2_MUST_ROLL_BACK_FIRST",
        ):
            with connection.transaction():
                connection.execute(G1_ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(G2_ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(G1_ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(G1.read_text(encoding="utf-8"))
        connection.execute(G2.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_derive_agent_runtime_model_video_wecom_outbox_v1()",),
        ).fetchone()[0] is not None


def test_unbound_correlation_stays_checkpoint_only(database: str) -> None:
    _install(database)
    batch, fact = _prepare_video(database, "web")
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute("""
            SELECT session_id,run_id,model_step_id
              FROM agent_actions WHERE id=%s
        """, (fact.action_id,)).fetchone()
        appended = connection.execute("""
            SELECT append_agent_runtime_event(
                %s,'action.provider.unknown',%s,%s,%s,'executor','negative-test',
                '{"error_code":"unbound"}'::JSONB,ARRAY['web_runtime']::TEXT[]
            )
        """, (action[0], action[1], action[2], uuid4())).fetchone()[0]
        outbox_id = connection.execute("""
            SELECT id FROM agent_projection_outbox
             WHERE event_id=%s AND projection_kind='web_runtime'
        """, (appended["event_id"],)).fetchone()[0]
    with _projection_connection(database) as connection:
        claims = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,30)",
        ).fetchone()[0]
        claim = next(
            item for item in claims if UUID(str(item["id"])) == outbox_id
        )
        result = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,"
            "'checkpoint_only',NULL)",
            (outbox_id, UUID(str(claim["lease_token"]))),
        ).fetchone()[0]
    assert result["result"]["projection_action"] == "checkpoint_only"
    assert result["result"]["action_id"] is None
    with psycopg.connect(database) as connection:
        state = connection.execute(
            "SELECT content::JSONB->0->>'slot_status' FROM messages WHERE id=%s",
            (batch.output_id,),
        ).fetchone()[0]
    assert state == "pending"


def test_08a_rollback_requires_08e1_first_on_empty_database(database: str) -> None:
    _apply_predecessors(database)
    paths = tuple(ROOT / "migrations" / name for name in (
        "228_08a_agent_runtime_media_model_video.sql",
        "228_08b_agent_runtime_media_wecom_delivery.sql",
        "228_08c_agent_runtime_media_worker_scope.sql",
        "228_08d_agent_runtime_media_atomic_image_batch.sql",
        "228_08e1_agent_runtime_media_model_video_fence.sql",
    ))
    e1_rollback = ROOT / (
        "migrations/rollback/228_08e1_agent_runtime_media_model_video_fence_rollback.sql"
    )
    a_rollback = ROOT / (
        "migrations/rollback/228_08a_agent_runtime_media_model_video_rollback.sql"
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for path in paths:
            connection.execute(path.read_text(encoding="utf-8"))
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_228_08E1_MUST_ROLL_BACK_FIRST",
        ):
            with connection.transaction():
                connection.execute(a_rollback.read_text(encoding="utf-8"))
        connection.execute(e1_rollback.read_text(encoding="utf-8"))
        connection.execute(a_rollback.read_text(encoding="utf-8"))
