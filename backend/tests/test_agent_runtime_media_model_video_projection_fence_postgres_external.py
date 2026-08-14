from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import (
    ORG, USER, _connect, _settings, database,
)
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _seed_batch, _worker_call,
)
from tests.test_agent_runtime_media_manifest_readback_postgres_external import (
    _seed_attempt_fence,
)
from tests.test_agent_runtime_media_model_video_postgres_external import (
    _apply_predecessors, _definition, _seed_model_video, _set_ready,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _convert_to_prepared_binding, _projection_connection, _seed_terminal_event,
)
from tests.test_agent_runtime_media_projection_review_postgres_external import (
    _seed_initial_run_terminal_event,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
BASE = tuple(ROOT / "migrations" / name for name in (
    "228_08a_agent_runtime_media_model_video.sql",
    "228_08b_agent_runtime_media_wecom_delivery.sql",
    "228_08c_agent_runtime_media_worker_scope.sql",
    "228_08d_agent_runtime_media_atomic_image_batch.sql",
))
E1 = ROOT / "migrations/228_08e1_agent_runtime_media_model_video_fence.sql"
E2 = ROOT / "migrations/228_08e2_agent_runtime_media_model_video_projection.sql"
E1_ROLLBACK = ROOT / (
    "migrations/rollback/228_08e1_agent_runtime_media_model_video_fence_rollback.sql"
)
E2_ROLLBACK = ROOT / (
    "migrations/rollback/228_08e2_agent_runtime_media_model_video_projection_rollback.sql"
)


def _install_base(database_url: str) -> None:
    _apply_predecessors(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for migration in BASE:
            connection.execute(migration.read_text(encoding="utf-8"))


def _install(database_url: str) -> None:
    _install_base(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(E1.read_text(encoding="utf-8"))
        connection.execute(E2.read_text(encoding="utf-8"))


def _parent_task(database_url: str, action_id: UUID) -> UUID:
    with psycopg.connect(database_url) as connection:
        return connection.execute("""
            SELECT NULLIF(command.payload->>'task_id','')::UUID
              FROM agent_actions action
              JOIN agent_runs run ON run.id=action.run_id
              JOIN agent_session_commands command ON command.id=run.command_id
             WHERE action.id=%s
        """, (action_id,)).fetchone()[0]


def _set_wecom_parent(database_url: str, action_id: UUID) -> UUID:
    task_id = _parent_task(database_url, action_id)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE tasks SET delivery_context=%s WHERE id=%s",
            (Jsonb({
                "actor": False, "runtime": True, "channel": "wecom",
                "chatid": "model-video-final-target",
            }), task_id),
        )
    return task_id


def _seed_progress_event(
    database_url: str, action_id: UUID, event_type: str,
) -> UUID:
    event_id, outbox_id = uuid4(), uuid4()
    status = event_type.removeprefix("action.")
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute("""
            SELECT session_id,run_id,model_step_id,org_id,user_id
              FROM agent_actions WHERE id=%s
        """, (action_id,)).fetchone()
        if event_type == "action.accepted":
            connection.execute("""
                INSERT INTO agent_runtime_provider_submission_facts(
                    attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,
                    provider,provider_revision,external_idempotency_key,request_hash,
                    execution_token,state,provider_task_ref,status_locator
                ) SELECT attempt.id,attempt.action_id,attempt.run_id,attempt.org_id,
                         attempt.user_id,session.scope_kind,session.scope_id,'kie',
                         'kie-runtime-media-v1',%s,attempt.request_hash,
                         attempt.execution_token,'submitted','kie-video-progress',
                         '/api/v1/jobs/recordInfo'
                    FROM agent_action_attempts attempt
                    JOIN agent_runtime_sessions session ON session.id=attempt.session_id
                   WHERE attempt.action_id=%s
            """, ("e" * 64, action_id))
        connection.execute(
            "UPDATE agent_actions SET status=%s,accepted_at=%s WHERE id=%s",
            (status, datetime.now(timezone.utc)
             if status == "accepted" else None, action_id),
        )
        sequence = connection.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM agent_runtime_events "
            "WHERE session_id=%s", (action[0],),
        ).fetchone()[0]
        connection.execute("""
            INSERT INTO agent_runtime_events(
                id,session_id,sequence,org_id,user_id,scope_kind,scope_id,
                event_type,run_id,model_step_id,action_id,correlation_id,
                actor_type,payload,payload_hash
            ) VALUES(%s,%s,%s,%s,%s,'user',%s,%s,%s,%s,%s,%s,
                     'executor','{}',%s)
        """, (
            event_id, action[0], sequence, action[3], action[4], str(action[4]),
            event_type, action[1], action[2], action_id, action_id,
            f"model-video-{event_type}",
        ))
        connection.execute("""
            INSERT INTO agent_projection_outbox(
                id,event_id,session_id,org_id,user_id,projection_kind
            ) VALUES(%s,%s,%s,%s,%s,'wecom')
        """, (outbox_id, event_id, action[0], action[3], action[4]))
    return outbox_id


def _apply_projection(
    database_url: str, outbox_id: UUID, action: str,
    content_part: dict[str, object] | None = None,
) -> dict[str, object]:
    with _projection_connection(database_url) as connection:
        claims = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(10,15)",
        ).fetchone()[0]
        claim = next(item for item in claims if UUID(str(item["id"])) == outbox_id)
        readback = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(str(claim["lease_token"]))),
        ).fetchone()[0]
        assert readback["outcome"] == "found"
        return connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,%s)",
            (outbox_id, UUID(str(claim["lease_token"])), action,
             Jsonb(content_part) if content_part else None),
        ).fetchone()[0]


def _seed_final_run(database_url: str, action_id: UUID) -> UUID:
    final_step, event_id, outbox_id = uuid4(), uuid4(), uuid4()
    final_text = "The video is ready, with the requested final explanation."
    content_hash = hashlib.sha256(final_text.encode()).hexdigest()
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        action = connection.execute("""
            SELECT session_id,run_id,org_id,user_id FROM agent_actions WHERE id=%s
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
        connection.execute("""
            UPDATE agent_runs SET status='completed',blocking_action_count=0,
                   result_hash=%s,completed_at=clock_timestamp()
             WHERE id=%s
        """, (content_hash, action[1]))
        sequence = connection.execute(
            "SELECT COALESCE(max(sequence),0)+1 FROM agent_runtime_events "
            "WHERE session_id=%s", (action[0],),
        ).fetchone()[0]
        connection.execute("""
            INSERT INTO agent_runtime_events(
                id,session_id,sequence,org_id,user_id,scope_kind,scope_id,
                event_type,run_id,correlation_id,actor_type,payload,payload_hash
            ) VALUES(%s,%s,%s,%s,%s,'user',%s,'run.completed',%s,%s,
                     'system','{}','model-video-run-completed')
        """, (
            event_id, action[0], sequence, action[2], action[3], str(action[3]),
            action[1], action[1],
        ))
        connection.execute("""
            INSERT INTO agent_projection_outbox(
                id,event_id,session_id,org_id,user_id,projection_kind
            ) VALUES(%s,%s,%s,%s,%s,'wecom')
        """, (outbox_id, event_id, action[0], action[2], action[3]))
    return outbox_id


def test_apply_rollback_reapply_order_acl_and_rls(database: str) -> None:
    _install_base(database)
    prepare_signature = (
        "prepare_agent_runtime_media_dispatch_v1(uuid,uuid,text,uuid,bigint,text)"
    )
    prepared_signature = (
        "_agent_runtime_media_prepared_action_projection_v1"
        "(agent_runtime_events,jsonb)"
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        before = (
            _definition(connection, prepare_signature),
            _definition(connection, prepared_signature),
        )
        connection.execute(E1.read_text(encoding="utf-8"))
        connection.execute(E2.read_text(encoding="utf-8"))
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_228_08E2_MUST_ROLL_BACK_FIRST",
        ):
            with connection.transaction():
                connection.execute(E1_ROLLBACK.read_text(encoding="utf-8"))
        security = connection.execute("""
            SELECT relrowsecurity,relforcerowsecurity,
                   has_function_privilege(
                     'everydayai_agent_runtime_worker',
                     '_prepare_agent_runtime_model_video_fenced_v1(jsonb,text)',
                     'EXECUTE'),
                   has_function_privilege(
                     'everydayai_projection_worker',
                     '_project_agent_runtime_model_video_run_v1()','EXECUTE')
              FROM pg_class
             WHERE oid='agent_runtime_prepared_media_action_bindings'::regclass
        """).fetchone()
        assert security == (True, True, False, False)
        connection.execute(E2_ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(E1_ROLLBACK.read_text(encoding="utf-8"))
        assert (
            _definition(connection, prepare_signature),
            _definition(connection, prepared_signature),
        ) == before
        connection.execute(E1.read_text(encoding="utf-8"))
        connection.execute(E2.read_text(encoding="utf-8"))


def test_action_resume_run_projection_and_wecom_final_delivery(database: str) -> None:
    _install(database)
    batch = _seed_model_video(database, "wecom")
    fact = batch.attempts[0]
    parent_task_id = _set_wecom_parent(database, fact.action_id)
    _set_ready(database)
    assert _worker_call(
        database, "prepare_agent_runtime_media_dispatch_v1", fact,
    )["outcome"] == "prepared"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_228_08E2_ACTIVE_MODEL_VIDEO_FACTS",
        ):
            with connection.transaction():
                connection.execute(E2_ROLLBACK.read_text(encoding="utf-8"))

    for event_type, expected_slot in (
        ("action.accepted", "accepted"), ("action.unknown", "unknown"),
    ):
        outbox_id = _seed_progress_event(database, fact.action_id, event_type)
        result = _apply_projection(database, outbox_id, "action_progress")
        assert result["result"]["slot_status"] == expected_slot
        with psycopg.connect(database) as connection:
            state = connection.execute("""
                SELECT message.status::TEXT,
                       message.content::JSONB->0->>'slot_status',
                       (SELECT count(*) FROM conversation_deliveries)
                  FROM messages message WHERE id=%s
            """, (batch.output_id,)).fetchone()
        assert state == ("pending", expected_slot, 0)

    source_url = "https://provider.example/model-video.mp4"
    action_outbox = _seed_terminal_event(
        database, fact.action_id, source_url,
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_projection_outbox SET projection_kind='wecom' WHERE id=%s",
            (action_outbox,),
        )
    action_result = _apply_projection(database, action_outbox, "action_progress", {
        "type": "video", "url": "https://cdn.example/model-video.mp4",
        "source_url": source_url, "download_url": "https://cdn.example/model-video.mp4",
        "storage_provider": "workspace", "storage_key": "runtime/model-video.mp4",
        "name": "model-video.mp4", "mime_type": "video/mp4", "size": 4096,
    })
    assert action_result["result"]["slot_status"] == "completed"
    with psycopg.connect(database) as connection:
        action_state = connection.execute("""
            SELECT child.status::TEXT,child.credits_used,parent.status::TEXT,
                   message.status::TEXT,message.content::JSONB->0->>'type',
                   (SELECT count(*) FROM conversation_deliveries)
              FROM agent_runtime_prepared_media_action_bindings binding
              JOIN tasks child ON child.id=binding.task_id
              JOIN tasks parent ON parent.id=%s
              JOIN messages message ON message.id=binding.output_message_id
             WHERE binding.action_id=%s
        """, (parent_task_id, fact.action_id)).fetchone()
    assert action_state == ("completed", 31, "running", "pending", "video", 0)

    run_outbox = _seed_final_run(database, fact.action_id)
    run_result = _apply_projection(database, run_outbox, "run_completed")
    assert run_result["result"]["projection_action"] == "run_completed"
    with psycopg.connect(database) as connection:
        final_state = connection.execute("""
            SELECT parent.status::TEXT,parent.credits_used,message.status::TEXT,
                   message.content::JSONB,
                   (SELECT array_agg(delivery.task_id ORDER BY delivery.task_id)
                      FROM conversation_deliveries delivery)
              FROM tasks parent JOIN messages message
                ON message.id=parent.assistant_message_id
             WHERE parent.id=%s
        """, (parent_task_id,)).fetchone()
    assert final_state[:3] == ("completed", 31, "completed")
    assert [part["type"] for part in final_state[3]] == ["video", "text"]
    assert final_state[4] == [parent_task_id]


def test_media_ingress_projection_keeps_228_06_semantics(database: str) -> None:
    _install(database)
    batch = _seed_batch(database, 1, credits=1000)
    fact = batch.attempts[0]
    _seed_attempt_fence(database, fact)
    _set_ready(database)
    assert _prepare(database, fact)["outcome"] == "prepared"
    _convert_to_prepared_binding(database, fact.action_id, "image")
    source_url = "https://provider.example/ingress-image.webp"
    action_outbox = _seed_terminal_event(database, fact.action_id, source_url)
    action_result = _apply_projection(database, action_outbox, "action_progress", {
        "type": "image", "url": "https://cdn.example/ingress-image.webp",
        "source_url": source_url, "download_url": source_url,
        "storage_provider": "workspace", "storage_key": "runtime/ingress.webp",
        "name": "ingress.webp", "mime_type": "image/webp", "size": 2048,
    })
    assert action_result["result"]["projection_action"] == "action_progress"
    run_outbox = _seed_initial_run_terminal_event(
        database, fact.action_id, "run.failed",
    )
    run_result = _apply_projection(database, run_outbox, "run_failed")
    assert run_result["result"]["projection_action"] == "checkpoint_only"
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT message.status::TEXT,message.content::JSONB->0->>'type'
              FROM messages message WHERE id=%s
        """, (batch.output_id,)).fetchone()
    assert state == ("completed", "image")


@pytest.mark.parametrize("mutation", ("lease", "version", "kill"))
def test_lock_wait_fence_change_has_no_task_or_credit(
    database: str, mutation: str,
) -> None:
    _install(database)
    batch = _seed_model_video(database)
    fact = batch.attempts[0]
    _set_ready(database)
    blocker = psycopg.connect(database)
    blocker.execute("SET ROLE everydayai_owner")
    blocker.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"runtime-model-video:{batch.step_id}",),
    )

    def prepare() -> tuple[str | None, str]:
        with _connect(database, "everydayai_agent_runtime_worker") as connection:
            _settings(connection, "everydayai_agent_runtime_worker")
            connection.execute("SET application_name='model-video-fence-waiter'")
            try:
                connection.execute(
                    "SELECT prepare_agent_runtime_media_dispatch_v1(%s,%s,%s,%s,0,%s)",
                    (fact.action_id, fact.attempt_id, "media-worker", fact.token,
                     fact.request_hash),
                )
            except psycopg.Error as error:
                connection.rollback()
                return error.sqlstate, str(error)
        return None, "unexpected success"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(prepare)
        for _ in range(200):
            with psycopg.connect(database) as observer:
                waiting = observer.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_stat_activity
                         WHERE application_name='model-video-fence-waiter'
                           AND wait_event='advisory'
                    )
                """).fetchone()[0]
            if waiting:
                break
            time.sleep(0.02)
        assert waiting is True
        with psycopg.connect(database) as connection:
            connection.execute("SET ROLE everydayai_owner")
            if mutation == "lease":
                connection.execute(
                    "UPDATE agent_action_attempts SET lease_expires_at="
                    "clock_timestamp()-interval '1 second' WHERE id=%s",
                    (fact.attempt_id,),
                )
            elif mutation == "version":
                connection.execute(
                    "UPDATE agent_action_attempts SET state_version=1 WHERE id=%s",
                    (fact.attempt_id,),
                )
            else:
                connection.execute("""
                    INSERT INTO agent_runtime_tenant_gate_controls(
                        org_id,gate_scope,scope_key,dispatch_blocked,kill_epoch,
                        state_version,reason,updated_by
                    ) VALUES(%s,'tenant','tenant',TRUE,1,1,'fence wait test',%s)
                """, (ORG, USER))
        blocker.rollback()
        sqlstate, error = future.result(timeout=10)
    blocker.close()
    assert sqlstate == "42501"
    assert "AGENT_RUNTIME_MEDIA_ATTEMPT" in error
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT app_user.credits,
                   (SELECT count(*) FROM tasks WHERE id=%s),
                   (SELECT count(*) FROM credit_transactions WHERE task_id=%s),
                   (SELECT count(*) FROM credits_history
                     WHERE description='Agent Runtime model video reservation'),
                   (SELECT count(*)
                      FROM agent_runtime_prepared_media_action_bindings
                     WHERE action_id=%s)
              FROM users app_user WHERE app_user.id=%s
        """, (fact.action_id, fact.action_id, fact.action_id, USER)).fetchone()
    assert state == (1000, 0, 0, 0, 0)
