"""Disposable PostgreSQL contract for Runtime media message controls."""

from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    CONVERSATION, ORG, USER, _connect, _settings, database,
)
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _prepare_legacy_schema, _seed_batch,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_07_agent_runtime_media_controls.sql"
ROLLBACK = ROOT / "migrations/rollback/228_07_agent_runtime_media_controls_rollback.sql"


def _prepare_projection_prerequisite(database_url: str) -> None:
    """Install the 228_06 stable-slot column required by this isolated lane."""
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "ALTER TABLE agent_runtime_media_action_bindings ADD COLUMN slot_id UUID",
        )
        connection.execute(
            "UPDATE agent_runtime_media_action_bindings SET slot_id=action_id",
        )
        connection.execute(
            "ALTER TABLE agent_runtime_media_action_bindings "
            "ALTER COLUMN slot_id SET NOT NULL",
        )


def _apply_round_trip(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        rows = connection.execute("""
          SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class
          WHERE relname IN('agent_runtime_media_cancel_requests',
                           'agent_runtime_media_retry_lineage') ORDER BY relname
        """).fetchall()
        assert rows == [
            ("agent_runtime_media_cancel_requests", True, True),
            ("agent_runtime_media_retry_lineage", True, True),
        ]
        assert connection.execute("""
          SELECT has_table_privilege(
            'everydayai_agent_runtime_worker',
            'agent_runtime_media_retry_lineage','SELECT')
        """).fetchone()[0] is False
        connection.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT to_regclass('agent_runtime_media_retry_lineage')",
        ).fetchone()[0] is None
        connection.execute(MIGRATION.read_text(encoding="utf-8"))


def _mark_retry_source_refunded(database_url: str, batch: object, index: int) -> None:
    action_id = batch.attempts[index].action_id
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_media_action_bindings SET credit_state='refunded' "
            "WHERE action_id=%s", (action_id,),
        )
        connection.execute(
            "UPDATE tasks SET status='cancelled',credits_locked=0,"
            "completed_at=clock_timestamp() WHERE id=(SELECT task_id FROM "
            "agent_runtime_media_action_bindings WHERE action_id=%s)", (action_id,),
        )


def _set_cancel_states(database_url: str, actions) -> None:
    completed, accepted, unknown = actions[:3]
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET status='completed',completed_at=clock_timestamp() "
            "WHERE id=%s", (completed.action_id,),
        )
        connection.execute(
            "UPDATE agent_action_attempts SET status='completed',ended_at=clock_timestamp() "
            "WHERE id=%s", (completed.attempt_id,),
        )
        connection.execute(
            "UPDATE agent_actions SET status='accepted',accepted_at=clock_timestamp() "
            "WHERE id=%s", (accepted.action_id,),
        )
        connection.execute("""
          UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',
            accepted_at=clock_timestamp(),external_receipt='{"provider_task_ref":"p-1"}'
          WHERE id=%s
        """, (accepted.attempt_id,))
        connection.execute(
            "UPDATE agent_actions SET status='unknown',retry_disposition='retry_after_reconcile' "
            "WHERE id=%s", (unknown.action_id,),
        )
        connection.execute("""
          UPDATE agent_action_attempts SET status='unknown',dispatch_phase='request_started',
            ambiguity_evidence='{"kind":"provider_timeout"}',
            retry_disposition='retry_after_reconcile'
          WHERE id=%s
        """, (unknown.attempt_id,))


def _runtime_call(database_url: str, sql: str, params: tuple) -> dict:
    with _connect(database_url, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime")
        connection.execute(
            "SELECT set_config('app.request_id','media-controls-test',false)",
        )
        return connection.execute(sql, params).fetchone()[0]


def _prepare_retry_dispatch(database_url: str, retry: dict) -> tuple[dict, UUID]:
    with _connect(database_url, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        connection.execute("SELECT set_config('app.request_id','claim-retry',false)")
        claim = connection.execute(
            "SELECT claim_ready_agent_actions('worker-1','claim-retry',10,120)",
        ).fetchone()[0]
        attempt = next(
            item for item in claim["attempts"] if item["action_id"] == retry["action_id"]
        )
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("UPDATE agent_runtime_control SET action_dispatch_enabled=TRUE,"
                           "non_safe_actions_enabled=TRUE,tool_confirmation_enabled=TRUE "
                           "WHERE singleton")
        connection.execute("INSERT INTO agent_runtime_org_rollout(org_id,enabled,updated_by,"
                           "update_reason) VALUES(%s,TRUE,%s,'media retry contract test') "
                           "ON CONFLICT(org_id) DO UPDATE SET enabled=TRUE", (ORG, USER))
        connection.execute("INSERT INTO agent_runtime_capabilities(capability_name,"
                           "reporter_role,ready,evidence,observed_at) VALUES("
                           "'tool_confirmation_v3_redis','authorization',TRUE,'{}',"
                           "clock_timestamp()) ON CONFLICT(capability_name) DO UPDATE SET "
                           "ready=TRUE,observed_at=clock_timestamp()")
        receipt_id = connection.execute(
            "SELECT id FROM agent_policy_receipts WHERE action_id=%s",
            (UUID(retry["action_id"]),),
        ).fetchone()[0]
    return attempt, receipt_id


def _complete_retry_action(
    database_url: str, retry: dict, attempt: dict, receipt_id: UUID,
) -> None:
    with _connect(database_url, "everydayai_agent_runtime_worker") as connection:
        _settings(connection, "everydayai_agent_runtime_worker")
        connection.execute("SELECT set_config('app.request_id','finish-retry',false)")
        gated = connection.execute("""
          SELECT gate_agent_action_dispatch(%s,%s,%s,%s,%s,
            'runtime_media_generation:generate_image',1,
            'runtime-media-slot-retry-v1','reconcile_only')
        """, (attempt["id"], attempt["execution_token"], attempt["state_version"],
              attempt["request_hash"], receipt_id)).fetchone()[0]
        assert gated["outcome"] == "dispatch_authorized"
        completed = connection.execute("""
          SELECT complete_agent_action(%s,%s,%s,%s,%s::jsonb)
        """, (attempt["id"], attempt["execution_token"], gated["state_version"],
              attempt["request_hash"],
              '{"status":"success","summary":"ok","data":{},"artifact_ids":[],"usage":{},"cost":{},"external_receipt":{}}')).fetchone()[0]
        assert completed["run_status"] == "completed"
    with psycopg.connect(database_url) as connection:
        run_id = UUID(retry["run_id"])
        assert connection.execute(
            "SELECT status,blocking_action_count FROM agent_runs WHERE id=%s", (run_id,),
        ).fetchone() == ("completed", 0)
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_events WHERE run_id=%s AND "
            "event_type='run.resumed'", (run_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_events WHERE run_id=%s AND "
            "event_type='run.completed'", (run_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM tasks WHERE id=(SELECT chat_task_id FROM "
            "agent_runtime_media_action_bindings WHERE action_id=%s)",
            (UUID(retry["action_id"]),),
        ).fetchone()[0] == "running"
        assert connection.execute(
            "SELECT count(*) FROM agent_model_steps WHERE run_id=%s", (run_id,),
        ).fetchone()[0] == 1


def _assert_retry_contract(
    database_url: str, retry: dict, batch: object, source_slot: str, before: int,
) -> None:
    with psycopg.connect(database_url) as connection:
        action_id = UUID(retry["action_id"])
        row = connection.execute("""
          SELECT action.action_index,binding.action_index,binding.slot_id,lineage.slot_id,
                 lineage.source_action_id,task.client_task_id
          FROM agent_actions action
          JOIN agent_runtime_media_action_bindings binding ON binding.action_id=action.id
          JOIN agent_runtime_media_retry_lineage lineage ON lineage.retry_action_id=action.id
          JOIN tasks task ON task.id=action.id WHERE action.id=%s
        """, (action_id,)).fetchone()
        assert (row[0], row[1], str(row[2]), str(row[3]), row[4], row[5]) == (
            0, 3, source_slot, source_slot, batch.attempts[3].action_id, "client-retry-1",
        )
        assert connection.execute(
            "SELECT credits FROM users WHERE id=%s", (USER,),
        ).fetchone()[0] == before - 6
        slot = connection.execute("SELECT part FROM messages,jsonb_array_elements("
                                  "content::jsonb) part WHERE messages.id=%s AND "
                                  "part->>'slot_index'='3'", (batch.output_id,)).fetchone()[0]
        assert (slot["slot_id"], slot["slot_status"], slot["slot_revision"]) == (
            source_slot, "pending", 1,
        )
        run_contract = connection.execute("""
          SELECT run.status,run.capability_snapshot,command.payload,
                 command.payload->>'task_id',binding.chat_task_id,task.id
          FROM agent_runs run JOIN agent_session_commands command ON command.id=run.command_id
          JOIN agent_runtime_media_action_bindings binding ON binding.run_id=run.id
          JOIN tasks task ON task.id=binding.task_id WHERE run.id=%s
        """, (UUID(retry["run_id"]),)).fetchone()
        assert run_contract[0] == "waiting_actions"
        assert run_contract[1]["execution_mode"] == "action_only"
        assert run_contract[1]["model_loop_enabled"] is False
        assert run_contract[2]["source"] == "runtime_media_slot_retry"
        assert run_contract[3] == str(action_id)
        assert run_contract[4] != run_contract[5]


def test_runtime_media_controls_full_database_contract(database: str) -> None:
    _prepare_legacy_schema(database)
    batch = _seed_batch(database, 4, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    _prepare_projection_prerequisite(database)
    _apply_round_trip(database)
    _set_cancel_states(database, batch.attempts)

    cancelled = _runtime_call(database, """
      SELECT request_agent_runtime_media_message_cancel_v1(%s,%s,%s,%s)
    """, (batch.output_id, ORG, USER, "cancel-1"))
    assert cancelled["outcome"] == "cancel_requested"
    assert (cancelled["completed_count"], cancelled["cancelled_count"],
            cancelled["reconcile_count"]) == (1, 1, 2)

    with psycopg.connect(database) as connection:
        states = connection.execute("""
          SELECT action.status,cancel_request.disposition,binding.credit_state
          FROM agent_runtime_media_action_bindings binding
          JOIN agent_actions action ON action.id=binding.action_id
          LEFT JOIN agent_runtime_media_cancel_requests cancel_request
            ON cancel_request.action_id=action.id
          WHERE binding.output_message_id=%s ORDER BY binding.action_index
        """, (batch.output_id,)).fetchall()
        assert states == [
            ("completed", None, "pending"),
            ("accepted", "cancel_reconcile", "pending"),
            ("unknown", "cancel_reconcile", "pending"),
            ("cancelled", "cancel_now", "pending"),
        ]
        connection.execute("SET ROLE everydayai_owner")
        connection.execute("""
          UPDATE messages SET status='failed',content=(
            SELECT jsonb_agg(CASE WHEN part->>'slot_index'='3'
              THEN part||'{"slot_status":"cancelled"}'::jsonb ELSE part END ORDER BY ordinality)
            FROM jsonb_array_elements(content::jsonb)
                 WITH ORDINALITY source(part,ordinality))::text
          WHERE id=%s
        """, (batch.output_id,))
        before = connection.execute(
            "SELECT credits FROM users WHERE id=%s", (USER,),
        ).fetchone()[0]

    source_slot = str(batch.attempts[3].action_id)
    pending = _runtime_call(database, """
      SELECT retry_agent_runtime_media_slot_v1(
        %s,%s,3,%s,0,%s,%s,%s,%s,%s)
    """, (
        batch.output_id, CONVERSATION,
        UUID(source_slot), ORG, USER, "retry-pending", "client-pending", None,
    ))
    assert pending["outcome"] == "projection_pending"
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT credits FROM users WHERE id=%s", (USER,),
        ).fetchone()[0] == before
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_media_retry_lineage",
        ).fetchone()[0] == 0
    _mark_retry_source_refunded(database, batch, 3)
    retry = _runtime_call(database, """
      SELECT retry_agent_runtime_media_slot_v1(
        %s,%s,3,%s,0,%s,%s,%s,%s,%s)
    """, (
        batch.output_id, CONVERSATION,
        UUID(source_slot), ORG, USER, "retry-1", "client-retry-1", "limit-slot-1",
    ))
    assert retry["outcome"] == "created"
    replay = _runtime_call(database, """
      SELECT retry_agent_runtime_media_slot_v1(
        %s,%s,3,%s,0,%s,%s,%s,%s,%s)
    """, (
        batch.output_id, CONVERSATION,
        UUID(source_slot), ORG, USER, "retry-1", "client-retry-1", "limit-slot-1",
    ))
    assert replay["outcome"] == "already_created"
    assert replay["action_id"] == retry["action_id"]

    _assert_retry_contract(database, retry, batch, source_slot, before)

    active = _runtime_call(database, """
      SELECT retry_agent_runtime_media_slot_v1(
        %s,%s,3,%s,1,%s,%s,%s,%s,NULL)
    """, (
        batch.output_id, CONVERSATION,
        UUID(source_slot), ORG, USER, "retry-2", "client-retry-2",
    ))
    assert active["outcome"] == "slot_active"

    retry_attempt, receipt_id = _prepare_retry_dispatch(database, retry)
    _complete_retry_action(database, retry, retry_attempt, receipt_id)

    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(ROLLBACK.read_text(encoding="utf-8"))


def test_runtime_media_controls_cross_tenant_scope_is_zero_write(database: str) -> None:
    _prepare_legacy_schema(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    _prepare_projection_prerequisite(database)
    _apply_round_trip(database)
    other_user = UUID("77777777-7777-7777-7777-777777777777")
    with psycopg.connect(database) as connection:
        before = connection.execute(
            "SELECT credits FROM users WHERE id=%s", (other_user,),
        ).fetchone()[0]
        counts = connection.execute("""
          SELECT (SELECT count(*) FROM agent_runtime_media_retry_lineage),
                 (SELECT count(*) FROM credit_transactions),
                 (SELECT count(*) FROM tasks)
        """).fetchone()
    with _connect(database, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime", user=other_user)
        connection.execute("SELECT set_config('app.request_id','cross-scope',false)")
        cancel = connection.execute(
            "SELECT request_agent_runtime_media_message_cancel_v1(%s,%s,%s,%s)",
            (batch.output_id, ORG, other_user, "cross-cancel"),
        ).fetchone()[0]
        retry = connection.execute("""
          SELECT retry_agent_runtime_media_slot_v1(
            %s,%s,0,%s,0,%s,%s,%s,NULL,NULL)
        """, (
            batch.output_id, CONVERSATION, batch.attempts[0].action_id,
            ORG, other_user, "cross-retry",
        )).fetchone()[0]
        with psycopg.connect(database) as verifier:
            verifier.execute("SET ROLE everydayai_owner")
            assert verifier.execute(
                "SELECT id FROM messages WHERE id=%s FOR UPDATE NOWAIT",
                (batch.output_id,),
            ).fetchone()[0] == batch.output_id
    assert cancel["outcome"] == "not_runtime_media"
    assert retry["outcome"] == "not_runtime_media"
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT credits FROM users WHERE id=%s", (other_user,),
        ).fetchone()[0] == before
        assert connection.execute("""
          SELECT (SELECT count(*) FROM agent_runtime_media_retry_lineage),
                 (SELECT count(*) FROM credit_transactions),
                 (SELECT count(*) FROM tasks)
        """).fetchone() == counts
