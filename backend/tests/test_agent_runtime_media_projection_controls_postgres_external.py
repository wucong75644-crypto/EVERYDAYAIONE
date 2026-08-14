from datetime import UTC, datetime, timedelta
from pathlib import Path
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
ROOT = Path(__file__).resolve().parents[1]
ISOLATION = ROOT / "migrations/228_06a_agent_runtime_media_projection_isolation.sql"
ISOLATION_ROLLBACK = ROOT / "migrations/rollback/228_06a_agent_runtime_media_projection_isolation_rollback.sql"


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


def test_terminal_poison_isolation_refunds_and_advances_checkpoint(database: str) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(ISOLATION.read_text(encoding="utf-8"))
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    action_id = batch.attempts[0].action_id
    outbox_id = _seed_terminal_event(
        database, action_id, "https://provider.example/poison.webp",
    )
    with _projection_connection(database) as connection:
        connection.execute(
            "SELECT set_config('app.request_id','media-isolation-worker',false)",
        )
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
        result = connection.execute(
            "SELECT isolate_agent_runtime_media_projection_v1(%s,%s,%s)",
            (outbox_id, UUID(claimed["lease_token"]), "contract_ssrf_forbidden"),
        ).fetchone()[0]
        assert result["outcome"] == "isolated"
    with psycopg.connect(database) as connection:
        task_status, used, locked, credit_state, tx_status = connection.execute("""
            SELECT task.status,task.credits_used,task.credits_locked,
                   binding.credit_state,transaction.status
              FROM agent_runtime_media_action_bindings binding
              JOIN tasks task ON task.id=binding.task_id
              JOIN credit_transactions transaction
                ON transaction.id=binding.credit_transaction_id
             WHERE binding.action_id=%s
        """, (action_id,)).fetchone()
        assert (task_status, used, locked, credit_state, tx_status) == (
            "failed", 0, 0, "refunded", "refunded",
        )
        outbox_status, isolated = connection.execute("""
            SELECT status,checkpoint->>'isolated'
              FROM agent_projection_outbox WHERE id=%s
        """, (outbox_id,)).fetchone()
        assert (outbox_status, isolated) == ("delivered", "true")
        assert connection.execute("""
            SELECT count(*) FROM agent_runtime_media_projection_isolations
             WHERE outbox_id=%s AND error_code='contract_ssrf_forbidden'
        """, (outbox_id,)).fetchone()[0] == 1


def test_isolation_migration_rollback_reapply_without_audit(database: str) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(ISOLATION.read_text(encoding="utf-8"))
        connection.execute(ISOLATION_ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_media_projection_isolations') IS NULL",
        ).fetchone()[0] is True
        connection.execute(ISOLATION.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regprocedure('isolate_agent_runtime_media_projection_v1(uuid,uuid,text)') IS NOT NULL",
        ).fetchone()[0] is True


def test_admin_can_fenced_isolate_dead_terminal_event(database: str) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(ISOLATION.read_text(encoding="utf-8"))
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    outbox_id = _seed_terminal_event(
        database, batch.attempts[0].action_id,
        "https://provider.example/dead-poison.webp",
    )
    with psycopg.connect(database) as connection:
        connection.execute("""
            UPDATE agent_projection_outbox SET status='dead',attempt_count=8,
                   recovery_version=2,last_error_code='asset_contract_invalid'
             WHERE id=%s
        """, (outbox_id,))
    admin_url = database.replace("postgres@", "everydayai_runtime_admin@")
    request_id = uuid4()
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
            "SELECT set_config('app.request_id','media-isolation-test',false)",
        )
        stale = connection.execute("""
            SELECT isolate_dead_agent_runtime_media_projection_v1(
                %s,1,8,%s,'stale review'
            )
        """, (outbox_id, uuid4())).fetchone()[0]
        assert stale["outcome"] == "stale_version"
        result = connection.execute("""
            SELECT isolate_dead_agent_runtime_media_projection_v1(
                %s,2,8,%s,'operator verified deterministic poison'
            )
        """, (outbox_id, request_id)).fetchone()[0]
        assert result["outcome"] == "isolated"
    with psycopg.connect(database) as connection:
        audit = connection.execute("""
            SELECT actor_user_id,worker_id,expected_recovery_version,
                   expected_attempt_count,database_request_id
              FROM agent_runtime_media_projection_isolations
             WHERE isolation_request_id=%s
        """, (request_id,)).fetchone()
        assert audit == (USER, None, 2, 8, "media-isolation-test")


def test_isolation_inner_fence_rejects_released_worker_without_writes(
    database: str,
) -> None:
    _prepare_legacy_schema(database)
    _install_projection_migration(database)
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(ISOLATION.read_text(encoding="utf-8"))
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    action_id = batch.attempts[0].action_id
    outbox_id = _seed_terminal_event(
        database, action_id, "https://provider.example/fence.webp",
    )
    with _projection_connection(database) as connection:
        [claimed] = connection.execute(
            "SELECT claim_agent_runtime_media_projection_v1(1,15)",
        ).fetchone()[0]
    with psycopg.connect(database) as connection:
        before = connection.execute("""
            SELECT task.status,binding.credit_state,message.status,
                   transaction.status,
                   (SELECT count(*) FROM agent_runtime_media_projection_results),
                   (SELECT count(*) FROM agent_runtime_media_projection_isolations)
              FROM agent_runtime_media_action_bindings binding
              JOIN tasks task ON task.id=binding.task_id
              JOIN messages message ON message.id=binding.output_message_id
              JOIN credit_transactions transaction
                ON transaction.id=binding.credit_transaction_id
             WHERE binding.action_id=%s
        """, (action_id,)).fetchone()
        connection.execute("""
            UPDATE agent_projection_outbox
               SET lease_token=gen_random_uuid(),
                   lease_expires_at=clock_timestamp()+interval '30 seconds'
             WHERE id=%s
        """, (outbox_id,))
    with _projection_connection(database) as connection:
        connection.execute(
            "SELECT set_config('app.request_id','stale-isolation-worker',false)",
        )
        result = connection.execute(
            "SELECT isolate_agent_runtime_media_projection_v1(%s,%s,'stale')",
            (outbox_id, UUID(claimed["lease_token"])),
        ).fetchone()[0]
        assert result["outcome"] == "ownership_lost"
    with psycopg.connect(database) as connection:
        after = connection.execute("""
            SELECT task.status,binding.credit_state,message.status,
                   transaction.status,
                   (SELECT count(*) FROM agent_runtime_media_projection_results),
                   (SELECT count(*) FROM agent_runtime_media_projection_isolations)
              FROM agent_runtime_media_action_bindings binding
              JOIN tasks task ON task.id=binding.task_id
              JOIN messages message ON message.id=binding.output_message_id
              JOIN credit_transactions transaction
                ON transaction.id=binding.credit_transaction_id
             WHERE binding.action_id=%s
        """, (action_id,)).fetchone()
        assert after == before


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
