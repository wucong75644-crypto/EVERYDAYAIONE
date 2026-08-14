from __future__ import annotations

from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare,
    _seed_batch,
    _worker_call,
)
from tests.test_agent_runtime_media_model_video_postgres_external import (
    _apply_predecessors,
    _seed_model_video,
    _set_ready,
)
from tests.test_agent_runtime_media_model_video_projection_fence_postgres_external import (
    _set_wecom_parent,
)
from tests.test_agent_runtime_media_projection_review_postgres_external import (
    _seed_initial_run_terminal_event,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _projection_connection,
)
from tests.test_agent_runtime_media_real_event_normalization_postgres_external import (
    _install as _install_real_events,
    _prepare_video,
)
from tests.test_agent_runtime_media_real_event_terminal_postgres_external import (
    _apply_in_order_until,
    _apply_run_event,
    _complete_action,
    _complete_run,
)
from tests.test_agent_runtime_media_wecom_delivery_postgres_external import (
    _apply as _apply_wecom,
    _claim as _claim_wecom,
    _install as _install_wecom,
    _set_target,
    _task_id,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
ROLLBACKS = MIGRATIONS / "rollback"

G1 = MIGRATIONS / "228_08g1_agent_runtime_media_real_event_normalization.sql"
G2 = MIGRATIONS / "228_08g2_agent_runtime_media_model_video_wecom_outbox.sql"
G1_ROLLBACK = ROLLBACKS / (
    "228_08g1_agent_runtime_media_real_event_normalization_rollback.sql"
)
G2_ROLLBACK = ROLLBACKS / (
    "228_08g2_agent_runtime_media_model_video_wecom_outbox_rollback.sql"
)
B_ROLLBACK = ROLLBACKS / (
    "228_08b_agent_runtime_media_wecom_delivery_rollback.sql"
)
E1_ROLLBACK = ROLLBACKS / (
    "228_08e1_agent_runtime_media_model_video_fence_rollback.sql"
)
E2_ROLLBACK = ROLLBACKS / (
    "228_08e2_agent_runtime_media_model_video_projection_rollback.sql"
)


def _expect_guard(
    connection: psycopg.Connection, rollback: Path, message: str,
) -> None:
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState, match=message,
    ):
        with connection.transaction():
            connection.execute(rollback.read_text(encoding="utf-8"))


def _set_attempt_state(
    connection: psycopg.Connection, action_id: UUID, state: str,
) -> None:
    action_state = state if state in ("accepted", "unknown") else "running"
    connection.execute("""
        UPDATE agent_actions
           SET status=%s,accepted_at=CASE WHEN %s='accepted'
                  THEN clock_timestamp() ELSE NULL END,
               completed_at=NULL
         WHERE id=%s
    """, (action_state, action_state, action_id))
    connection.execute("""
        UPDATE agent_action_attempts
           SET status=%s,
               dispatch_phase=CASE %s
                   WHEN 'claimed' THEN 'claimed'
                   WHEN 'accepted' THEN 'accepted'
                   ELSE 'request_started' END,
               external_receipt=CASE WHEN %s='accepted'
                   THEN '{"provider_task_ref":"rollback-guard"}'::JSONB
                   ELSE '{}'::JSONB END,
               ambiguity_evidence=CASE WHEN %s='unknown'
                   THEN '{"error_code":"rollback_guard"}'::JSONB
                   ELSE '{}'::JSONB END,
               accepted_at=CASE WHEN %s='accepted'
                   THEN clock_timestamp() ELSE NULL END,
               ended_at=NULL
         WHERE action_id=%s
    """, (state, state, state, state, state, action_id))


def _apply_run_terminal(database_url: str, outbox_id: UUID) -> None:
    with _projection_connection(database_url) as connection:
        [claim] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,30)",
        ).fetchone()[0]
        assert UUID(str(claim["id"])) == outbox_id
        result = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,"
            "'run_completed',NULL)",
            (outbox_id, UUID(str(claim["lease_token"]))),
        ).fetchone()[0]
        assert result["outcome"] == "applied"


@pytest.mark.parametrize(
    "state", ("claimed", "dispatching", "accepted", "unknown"),
)
def test_g1_blocks_each_live_model_video_attempt_state(
    database: str, state: str,
) -> None:
    _install_real_events(database, include_g2=False)
    _batch, fact = _prepare_video(database, "web")
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE agent_runtime_prepared_media_action_bindings
               SET credit_state='confirmed' WHERE action_id=%s
        """, (fact.action_id,))
        connection.execute("""
            UPDATE tasks SET status='completed',credits_locked=0,
                   completed_at=clock_timestamp()
             WHERE id=(SELECT task_id
                         FROM agent_runtime_prepared_media_action_bindings
                        WHERE action_id=%s)
        """, (fact.action_id,))
        _set_attempt_state(connection, fact.action_id, state)
        _expect_guard(
            connection, G1_ROLLBACK,
            "AGENT_RUNTIME_228_08G1_MODEL_VIDEO_NOT_DRAINED",
        )


@pytest.mark.parametrize(
    "state", ("dispatching", "accepted", "unknown"),
)
def test_g2_blocks_dispatch_and_reconcile_attempt_states(
    database: str, state: str,
) -> None:
    _install_real_events(database)
    _batch, fact = _prepare_video(database, "wecom")
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE agent_runtime_prepared_media_action_bindings
               SET credit_state='confirmed' WHERE action_id=%s
        """, (fact.action_id,))
        connection.execute("""
            UPDATE tasks SET status='completed',credits_locked=0,
                   completed_at=clock_timestamp()
             WHERE id=(SELECT task_id
                         FROM agent_runtime_prepared_media_action_bindings
                        WHERE action_id=%s)
        """, (fact.action_id,))
        connection.execute("""
            UPDATE agent_runs SET status='completed',blocking_action_count=0,
                   completed_at=clock_timestamp()
             WHERE id=(SELECT run_id FROM agent_actions WHERE id=%s)
        """, (fact.action_id,))
        connection.execute("""
            UPDATE tasks SET status='completed',credits_locked=0,
                   completed_at=clock_timestamp()
             WHERE id=(
                 SELECT (command.payload->>'task_id')::UUID
                   FROM agent_actions action
                   JOIN agent_runs run ON run.id=action.run_id
                   JOIN agent_session_commands command ON command.id=run.command_id
                  WHERE action.id=%s
             )
        """, (fact.action_id,))
        connection.execute("""
            UPDATE agent_actions SET status='completed',
                   completed_at=clock_timestamp() WHERE id=%s
        """, (fact.action_id,))
        _set_attempt_state(connection, fact.action_id, state)
        connection.execute(
            "UPDATE agent_actions SET status='completed',"
            "completed_at=clock_timestamp() WHERE id=%s", (fact.action_id,),
        )
        _expect_guard(
            connection, G2_ROLLBACK,
            "AGENT_RUNTIME_228_08G2_WECOM_OUTBOX_IN_USE",
        )


def test_g1_allows_fully_projected_terminal_action(database: str) -> None:
    _install_real_events(database, include_g2=False)
    _batch, fact = _prepare_video(database, "web")
    source_url = "https://provider.example/g1-drained-video.mp4"
    terminal_outbox = _complete_action(database, fact, source_url)
    _apply_in_order_until(database, terminal_outbox, {
        "type": "video", "url": "https://cdn.example/g1-drained-video.mp4",
        "source_url": source_url, "download_url": source_url,
        "storage_provider": "workspace", "storage_key": "runtime/g1.mp4",
        "name": "g1.mp4", "mime_type": "video/mp4", "size": 2048,
    })
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(G1_ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_agent_runtime_media_normalize_model_video_event_v1"
             "(agent_runtime_events)",),
        ).fetchone()[0] is None


def test_g2_blocks_terminal_undelivered_then_allows_fully_drained(
    database: str,
) -> None:
    _install_real_events(database)
    _batch, fact = _prepare_video(database, "wecom")
    source_url = "https://provider.example/g2-drained-video.mp4"
    action_outbox = _complete_action(database, fact, source_url)
    _apply_in_order_until(database, action_outbox, {
        "type": "video", "url": "https://cdn.example/g2-drained-video.mp4",
        "source_url": source_url, "download_url": source_url,
        "storage_provider": "workspace", "storage_key": "runtime/g2.mp4",
        "name": "g2.mp4", "mime_type": "video/mp4", "size": 2048,
    })
    _apply_run_event(database, fact.action_id, "run.resumed", "run_running")
    run_id = _complete_run(database, fact.action_id, "G2 drained final text")
    with psycopg.connect(database) as connection:
        rows = connection.execute("""
            SELECT outbox.projection_kind,outbox.id
              FROM agent_runtime_events event
              JOIN agent_projection_outbox outbox ON outbox.event_id=event.id
             WHERE event.run_id=%s AND event.event_type='run.completed'
               AND outbox.projection_kind IN ('web_runtime','wecom')
        """, (run_id,)).fetchall()
    outboxes = {kind: outbox_id for kind, outbox_id in rows}
    assert set(outboxes) == {"web_runtime", "wecom"}
    _apply_run_terminal(database, outboxes["web_runtime"])
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        _expect_guard(
            connection, G2_ROLLBACK,
            "AGENT_RUNTIME_228_08G2_WECOM_DELIVERY_NOT_DRAINED",
        )
    _apply_run_terminal(database, outboxes["wecom"])
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE conversation_deliveries
               SET status='delivered',delivered_at=clock_timestamp()
             WHERE channel='wecom' AND delivery_kind='assistant_terminal'
        """)
        connection.execute(G2_ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_media_wecom_outbox_facts_v1')",
        ).fetchone()[0] is None


@pytest.mark.parametrize("lane", ("media_ingress", "model_loop"))
def test_08b_blocks_active_wecom_media_lanes(database: str, lane: str) -> None:
    if lane == "media_ingress":
        _install_wecom(database)
        batch = _seed_batch(database, 1, credits=1000)
        fact = batch.attempts[0]
        assert _prepare(database, fact)["outcome"] == "prepared"
        task_id = _task_id(database, fact.action_id, chat=True)
        with psycopg.connect(database) as connection:
            connection.execute("SET ROLE everydayai_owner")
            connection.execute(
                "UPDATE tasks SET delivery_context=%s WHERE id=%s",
                (Jsonb({"actor": False, "runtime": True, "channel": "wecom"}),
                 task_id),
            )
    else:
        _apply_predecessors(database)
        with psycopg.connect(database) as connection:
            connection.execute("SET ROLE everydayai_owner")
            for name in (
                "228_08a_agent_runtime_media_model_video.sql",
                "228_08b_agent_runtime_media_wecom_delivery.sql",
            ):
                connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
        batch = _seed_model_video(database, "wecom")
        fact = batch.attempts[0]
        _set_wecom_parent(database, fact.action_id)
        _set_ready(database)
        assert _worker_call(
            database, "prepare_agent_runtime_media_dispatch_v1", fact,
        )["outcome"] == "prepared"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        _expect_guard(
            connection, B_ROLLBACK,
            "AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_IN_FLIGHT",
        )


def test_08b_blocks_terminal_undelivered_then_allows_delivered(
    database: str,
) -> None:
    _install_wecom(database)
    batch = _seed_batch(database, 1, credits=1000)
    fact = batch.attempts[0]
    assert _prepare(database, fact)["outcome"] == "prepared"
    outbox_id = _seed_initial_run_terminal_event(
        database, fact.action_id, "run.failed",
    )
    task_id = _task_id(database, fact.action_id, chat=True)
    _set_target(
        database, task_id=task_id, outbox_id=outbox_id, channel="wecom",
    )
    [claimed] = _claim_wecom(database, "claim_agent_runtime_media_projection_v1")
    _apply_wecom(
        database, outbox_id=outbox_id,
        lease_token=UUID(str(claimed["lease_token"])), action="run_failed",
    )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE agent_actions SET status='failed',terminal_reason='drained',
                   completed_at=clock_timestamp() WHERE id=%s
        """, (fact.action_id,))
        connection.execute("""
            UPDATE agent_action_attempts SET status='failed',
                   ended_at=clock_timestamp() WHERE action_id=%s
        """, (fact.action_id,))
        _expect_guard(
            connection, B_ROLLBACK,
            "AGENT_RUNTIME_MEDIA_WECOM_DELIVERY_NOT_DRAINED",
        )
        connection.execute("""
            UPDATE conversation_deliveries
               SET status='delivered',delivered_at=clock_timestamp()
             WHERE task_id=%s AND channel='wecom'
               AND delivery_kind='assistant_terminal'
        """, (task_id,))
        connection.execute(B_ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_project_agent_runtime_media_wecom_delivery_v1()",),
        ).fetchone()[0] is None


def test_empty_candidate_rollbacks_require_reverse_order_and_reapply(
    database: str,
) -> None:
    _apply_predecessors(database)
    forwards = tuple(MIGRATIONS / name for name in (
        "228_08a_agent_runtime_media_model_video.sql",
        "228_08b_agent_runtime_media_wecom_delivery.sql",
        "228_08c_agent_runtime_media_worker_scope.sql",
        "228_08d_agent_runtime_media_atomic_image_batch.sql",
        "228_08e1_agent_runtime_media_model_video_fence.sql",
        "228_08e2_agent_runtime_media_model_video_projection.sql",
        "228_08g1_agent_runtime_media_real_event_normalization.sql",
        "228_08g2_agent_runtime_media_model_video_wecom_outbox.sql",
    ))
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        for migration in forwards:
            connection.execute(migration.read_text(encoding="utf-8"))
        _expect_guard(
            connection, E2_ROLLBACK,
            "AGENT_RUNTIME_228_08G1_MUST_ROLL_BACK_FIRST",
        )
        _expect_guard(
            connection, G1_ROLLBACK,
            "AGENT_RUNTIME_228_08G2_MUST_ROLL_BACK_FIRST",
        )
        for rollback in (
            G2_ROLLBACK, G1_ROLLBACK, E2_ROLLBACK, E1_ROLLBACK, B_ROLLBACK,
        ):
            connection.execute(rollback.read_text(encoding="utf-8"))
        for migration in (
            forwards[1], forwards[4], forwards[5], forwards[6], forwards[7],
        ):
            connection.execute(migration.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure(%s)",
            ("_derive_agent_runtime_model_video_wecom_outbox_v1()",),
        ).fetchone()[0] is not None
