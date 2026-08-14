from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _worker_rpc
from tests.test_agent_runtime_media_projection_postgres_external import (
    _projection_connection,
)
from tests.test_agent_runtime_media_real_event_normalization_postgres_external import (
    _install as _install_real_events,
)
from tests.test_agent_runtime_media_real_event_terminal_postgres_external import (
    _complete_action,
)
from tests.test_agent_runtime_media_real_image_events_postgres_external import (
    ACTION_EVENTS,
    _apply_until,
    _content,
    _prepare_images,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
I1 = ROOT / (
    "migrations/228_08i1_agent_runtime_media_real_image_event_normalization.sql"
)
I2 = ROOT / (
    "migrations/228_08i2_agent_runtime_media_model_image_wecom_outbox.sql"
)
I1_ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08i1_agent_runtime_media_real_image_event_normalization_rollback.sql"
)
I2_ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08i2_agent_runtime_media_model_image_wecom_outbox_rollback.sql"
)
G1_ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08g1_agent_runtime_media_real_event_normalization_rollback.sql"
)
G2_ROLLBACK = ROOT / (
    "migrations/rollback/"
    "228_08g2_agent_runtime_media_model_video_wecom_outbox_rollback.sql"
)


def _install(database_url: str) -> None:
    _install_real_events(database_url)
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(I1.read_text(encoding="utf-8"))
        connection.execute(I2.read_text(encoding="utf-8"))


def _configure_channel(database_url: str, action_id: UUID, channel: str) -> UUID:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        run_id, parent_id = connection.execute("""
            SELECT run.id,(command.payload->>'task_id')::UUID
              FROM agent_actions action
              JOIN agent_runs run ON run.id=action.run_id
              JOIN agent_session_commands command ON command.id=run.command_id
             WHERE action.id=%s
        """, (action_id,)).fetchone()
        connection.execute(
            "UPDATE agent_runs SET capability_snapshot="
            "capability_snapshot||jsonb_build_object('channel',%s::TEXT) WHERE id=%s",
            (channel, run_id),
        )
        connection.execute(
            "UPDATE tasks SET delivery_context=delivery_context||%s WHERE id=%s",
            (Jsonb({"actor": False, "runtime": True, "channel": channel}),
             parent_id),
        )
    return run_id


def _project_target(
    database_url: str, target_id: UUID, target_action: str,
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
            action = target_action if outbox_id == target_id else (
                "action_progress" if event_type in ACTION_EVENTS
                else "checkpoint_only"
            )
            result = connection.execute(
                "SELECT apply_agent_runtime_media_projection_v1(%s,%s,%s,NULL)",
                (outbox_id, lease, action),
            ).fetchone()[0]
        if outbox_id == target_id:
            return result
    raise AssertionError(f"projection outbox was not reached: {target_id}")


def _run_outbox(
    database_url: str, run_id: UUID, event_type: str, kind: str,
) -> UUID:
    with psycopg.connect(database_url) as connection:
        row = connection.execute("""
            SELECT outbox.id FROM agent_runtime_events event
              JOIN agent_projection_outbox outbox ON outbox.event_id=event.id
             WHERE event.run_id=%s AND event.event_type=%s
               AND outbox.projection_kind=%s
        """, (run_id, event_type, kind)).fetchone()
    assert row is not None
    return row[0]


def _resume_and_finish_run(
    database_url: str, run_id: UUID, terminal: str,
) -> None:
    resumed = _run_outbox(database_url, run_id, "run.resumed", "web_runtime")
    assert _project_target(database_url, resumed, "run_running")["outcome"] == "applied"
    claim = _worker_rpc(database_url, "claim_next_agent_run", (
        f"model-image-{terminal}", 90, 3,
    ))
    assert UUID(str(claim["entity_id"])) == run_id
    if terminal == "completed":
        final_text = "Final image explanation"
        result_hash = hashlib.sha256(final_text.encode()).hexdigest()
        with psycopg.connect(database_url) as connection:
            connection.execute("SET ROLE everydayai_owner")
            run = connection.execute(
                "SELECT session_id,org_id,user_id FROM agent_runs WHERE id=%s",
                (run_id,),
            ).fetchone()
            step_id = uuid4()
            connection.execute("""
                INSERT INTO agent_model_steps(
                  id,run_id,session_id,org_id,user_id,step_number,status,model_id,
                  provider,model_revision,prompt_revision,tool_catalog_revision,
                  request_receipt,response_receipt,stop_reason,completed_at
                ) VALUES(%s,%s,%s,%s,%s,2,'completed','qwen3.5-plus','dashscope',
                  'v1','batch-media-v1','catalog-v7','{}','{}','final',
                  clock_timestamp())
            """, (step_id, run_id, run[0], run[1], run[2]))
            connection.execute("""
                INSERT INTO agent_model_results(
                  model_step_id,run_id,session_id,org_id,user_id,output_kind,
                  text_content,content_hash
                ) VALUES(%s,%s,%s,%s,%s,'text',%s,%s)
            """, (step_id, run_id, run[0], run[1], run[2], final_text,
                  result_hash))
        result = _worker_rpc(database_url, "complete_agent_run", (
            run_id, claim["execution_token"], claim["state_version"], result_hash,
        ))
    else:
        result = _worker_rpc(database_url, "fail_agent_run", (
            run_id, claim["execution_token"], claim["state_version"],
            "model_image_failed",
        ))
    assert result["outcome"] == terminal


def _complete_images(database_url: str, batch) -> None:
    for index, fact in enumerate(batch.attempts):
        source = f"https://provider.example/image-{index}.png"
        outbox = _complete_action(database_url, fact, source)
        assert _apply_until(database_url, outbox, _content(index))["outcome"] == "applied"
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM conversation_deliveries",
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_media_image_wecom_outbox_facts_v1",
        ).fetchone()[0] == 0


@pytest.mark.parametrize("terminal", ("completed", "failed"))
def test_model_image_wecom_run_emits_one_terminal_delivery(
    database: str, terminal: str,
) -> None:
    batch = _prepare_images(database, 2)
    run_id = _configure_channel(database, batch.attempts[0].action_id, "wecom")
    _complete_images(database, batch)
    _resume_and_finish_run(database, run_id, terminal)

    event_type = f"run.{terminal}"
    projection_action = f"run_{terminal}"
    web_outbox = _run_outbox(database, run_id, event_type, "web_runtime")
    wecom_outbox = _run_outbox(database, run_id, event_type, "wecom")
    web_result = _project_target(database, web_outbox, projection_action)
    wecom_result = _project_target(database, wecom_outbox, projection_action)
    assert web_result["result"]["projection_action"] == projection_action
    assert wecom_result["result"]["projection_action"] == projection_action

    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT parent.status::TEXT,message.status::TEXT,
                   (SELECT count(*) FROM conversation_deliveries delivery
                     WHERE delivery.task_id=parent.id
                       AND delivery.channel='wecom'
                       AND delivery.delivery_kind='assistant_terminal'),
                   (SELECT count(*)
                      FROM agent_runtime_media_image_wecom_outbox_facts_v1 fact
                     WHERE fact.run_id=run.id),
                   (SELECT count(*) FROM agent_projection_outbox outbox
                      JOIN agent_runtime_events event ON event.id=outbox.event_id
                     WHERE event.run_id=run.id AND event.event_type=%s
                       AND outbox.projection_kind='wecom'),
                   (SELECT count(*) FROM agent_runtime_media_projection_results result
                     WHERE result.outbox_id IN (%s,%s))
              FROM agent_runs run
              JOIN agent_session_commands command ON command.id=run.command_id
              JOIN tasks parent ON parent.id=(command.payload->>'task_id')::UUID
              JOIN messages message ON message.id=parent.assistant_message_id
             WHERE run.id=%s
        """, (event_type, web_outbox, wecom_outbox, run_id)).fetchone()
    expected_message = "completed" if terminal == "completed" else "failed"
    assert state == (terminal, expected_message, 1, 1, 1, 2)


def test_model_image_web_run_never_emits_wecom_delivery(database: str) -> None:
    batch = _prepare_images(database, 1)
    run_id = _configure_channel(database, batch.attempts[0].action_id, "web")
    _complete_images(database, batch)
    _resume_and_finish_run(database, run_id, "completed")
    web_outbox = _run_outbox(database, run_id, "run.completed", "web_runtime")
    result = _project_target(database, web_outbox, "run_completed")
    assert result["result"]["projection_action"] == "run_completed"
    with psycopg.connect(database) as connection:
        state = connection.execute("""
            SELECT count(*) FILTER(WHERE outbox.projection_kind='wecom'),
                   (SELECT count(*) FROM conversation_deliveries),
                   (SELECT count(*)
                      FROM agent_runtime_media_image_wecom_outbox_facts_v1)
              FROM agent_runtime_events event
              JOIN agent_projection_outbox outbox ON outbox.event_id=event.id
             WHERE event.run_id=%s AND event.event_type='run.completed'
        """, (run_id,)).fetchone()
    assert state == (0, 0, 0)


def test_i1_i2_empty_rollback_order_reapply_acl_and_rls(database: str) -> None:
    _install(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        security = connection.execute("""
            SELECT class.relrowsecurity,class.relforcerowsecurity,
                   has_table_privilege(
                     'everydayai_agent_runtime_worker',
                     'agent_runtime_media_image_wecom_outbox_facts_v1','SELECT'),
                   has_function_privilege(
                     'everydayai_agent_runtime_worker',
                     '_derive_agent_runtime_model_media_wecom_outbox_v2()',
                     'EXECUTE')
              FROM pg_class class
             WHERE class.oid=
                   'agent_runtime_media_image_wecom_outbox_facts_v1'::REGCLASS
        """).fetchone()
        assert security == (True, True, False, False)
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_228_08I1_MUST_ROLL_BACK_FIRST",
        ):
            with connection.transaction():
                connection.execute(G1_ROLLBACK.read_text(encoding="utf-8"))
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState,
            match="AGENT_RUNTIME_228_08I2_MUST_ROLL_BACK_FIRST",
        ):
            with connection.transaction():
                connection.execute(G2_ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(I2_ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(I1_ROLLBACK.read_text(encoding="utf-8"))
        connection.execute(I1.read_text(encoding="utf-8"))
        connection.execute(I2.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_derive_agent_runtime_model_media_wecom_outbox_v2()",),
        ).fetchone()[0] is not None
