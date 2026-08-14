from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import ORG, USER, database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _prepare_legacy_schema, _seed_batch,
)
from tests.test_agent_runtime_media_projection_postgres_external import (
    _install_projection_migration, _projection_connection,
    _seed_retry_run_event, _seed_terminal_event,
)


pytestmark = pytest.mark.external


def test_projection_failure_dead_threshold_and_admin_requeue(database: str) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    outbox_id = _seed_terminal_event(
        database, batch.attempts[0].action_id,
        "https://provider.example/dead.png",
    )

    for attempt in range(1, 9):
        with _projection_connection(database) as connection:
            [claimed] = connection.execute(
                "SELECT claim_agent_runtime_media_projection_v1(1,15)",
            ).fetchone()[0]
            failed = connection.execute(
                "SELECT fail_agent_runtime_media_projection_v1(%s,%s,'asset_io')",
                (outbox_id, UUID(claimed["lease_token"])),
            ).fetchone()[0]
            assert failed["status"] == ("dead" if attempt == 8 else "pending")
        with psycopg.connect(database) as connection:
            status, attempts, delay = connection.execute("""
                SELECT status,attempt_count,
                       extract(epoch FROM next_attempt_at-clock_timestamp())
                  FROM agent_projection_outbox WHERE id=%s
            """, (outbox_id,)).fetchone()
            assert (status, attempts) == (
                "dead" if attempt == 8 else "pending", attempt,
            )
            if attempt == 1:
                assert delay >= 8
            if attempt < 8:
                connection.execute(
                    "UPDATE agent_projection_outbox SET next_attempt_at=clock_timestamp() "
                    "WHERE id=%s", (outbox_id,),
                )

    recovery_id = uuid4()
    not_before = datetime.now(UTC) + timedelta(seconds=1)
    admin_url = database.replace("postgres@", "everydayai_runtime_admin@")
    with psycopg.connect(admin_url) as connection:
        connection.execute(
            "SELECT set_config('app.actor_user_id',%s,false)", (str(USER),),
        )
        connection.execute(
            "SELECT set_config('app.org_id',%s,false)", (str(ORG),),
        )
        connection.execute(
            "SELECT set_config('app.access_kind','runtime_admin',false)",
        )
        connection.execute(
            "SELECT set_config('app.request_id','media-recovery-test',false)",
        )
        result = connection.execute("""
            SELECT requeue_agent_runtime_media_projection_v1(%s,0,8,%s,%s,%s)
        """, (
            outbox_id, recovery_id, "verified transient asset IO", not_before,
        )).fetchone()[0]
        assert result["outcome"] == "requeued"

    with _projection_connection(database) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("""
                SELECT requeue_agent_runtime_media_projection_v1(
                    %s,1,8,%s,'forbidden',clock_timestamp()
                )
            """, (outbox_id, uuid4()))
    with psycopg.connect(database) as connection:
        assert connection.execute("""
            SELECT status='pending' AND recovery_version=1 AND recovery_count=1
              FROM agent_projection_outbox WHERE id=%s
        """, (outbox_id,)).fetchone()[0] is True
        assert connection.execute("""
            SELECT count(*) FROM agent_runtime_media_projection_recoveries
             WHERE recovery_request_id=%s
        """, (recovery_id,)).fetchone()[0] == 1


def test_retry_one_shot_run_is_checkpoint_only_without_model_final(database: str) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    action_id = batch.attempts[0].action_id
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
            UPDATE tasks SET request_params=jsonb_build_object(
                '_task_slot_id','runtime-retry-slot'
            ) WHERE id=(SELECT task_id FROM agent_runtime_media_action_bindings
                         WHERE action_id=%s)
        """, (action_id,))
        before = connection.execute("""
            SELECT message.content,chat_task.status,chat_task.result
              FROM agent_runtime_media_action_bindings binding
              JOIN messages message ON message.id=binding.output_message_id
              JOIN tasks chat_task ON chat_task.id=binding.chat_task_id
             WHERE binding.action_id=%s
        """, (action_id,)).fetchone()
    outbox_id = _seed_retry_run_event(database, action_id)

    with _projection_connection(database) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        readback = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert readback["action_facts"]["run"]["capability_snapshot"] == {
            "source": "runtime_media_retry",
            "execution_mode": "one_shot_action",
            "projection_mode": "media_action_only",
        }
        assert readback["action_facts"]["task"]["request_params"][
            "_task_slot_id"
        ] == "runtime-retry-slot"
        applied = connection.execute(
            "SELECT apply_agent_runtime_media_projection_v1(%s,%s,'run_completed',NULL)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert applied["outcome"] == "applied"
        assert applied["result"]["projection_action"] == "checkpoint_only"
        replayed = connection.execute(
            "SELECT read_agent_runtime_media_projection_v1(%s,%s)",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert replayed["outcome"] == "already_applied"
        assert replayed["action_facts"]["task"]["id"]

    with psycopg.connect(database) as connection:
        after = connection.execute("""
            SELECT message.content,chat_task.status,chat_task.result
              FROM agent_runtime_media_action_bindings binding
              JOIN messages message ON message.id=binding.output_message_id
              JOIN tasks chat_task ON chat_task.id=binding.chat_task_id
             WHERE binding.action_id=%s
        """, (action_id,)).fetchone()
        assert after == before
        assert connection.execute("""
            SELECT count(*) FROM agent_model_results result
              JOIN agent_actions action ON action.model_step_id=result.model_step_id
             WHERE action.id=%s
        """, (action_id,)).fetchone()[0] == 0
