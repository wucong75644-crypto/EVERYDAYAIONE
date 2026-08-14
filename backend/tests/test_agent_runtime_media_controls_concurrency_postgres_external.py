"""Concurrency and AR18 handoff contracts for Runtime media controls."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from threading import Event
import time

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_agent_runtime_ar17_postgres_external import (
    CONVERSATION, ORG, USER, _connect, _settings, database,
)
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    _prepare, _prepare_legacy_schema, _seed_batch,
)
from tests.test_agent_runtime_media_controls_postgres_external import (
    _apply_round_trip, _mark_retry_source_refunded, _prepare_projection_prerequisite,
    _runtime_call,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
PROVIDER_ATTEMPTS = ROOT / "migrations/226_01_agent_runtime_action_provider_reconciliation.sql"


def _prepare_cancel_handoff_prerequisite(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(PROVIDER_ATTEMPTS.read_text(encoding="utf-8"))


def _seed_dispatch_identity(database_url: str, batch: object) -> None:
    fact = batch.attempts[0]
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        binding_hash = connection.execute(
            "SELECT provider_request_hash FROM agent_runtime_media_action_bindings "
            "WHERE action_id=%s", (fact.action_id,),
        ).fetchone()[0]
        connection.execute("""
          UPDATE agent_action_attempts SET status='dispatching',
            dispatch_phase='request_started',provider='kie',
            provider_task_ref='provider-task-cancel',
            provider_idempotency_key='provider-idem-cancel',
            provider_request_hash=%s,
            external_receipt=%s WHERE id=%s
        """, (
            binding_hash, Jsonb({"provider_task_ref": "provider-task-cancel"}),
            fact.attempt_id,
        ))
        connection.execute("""
          INSERT INTO agent_runtime_provider_submission_facts(
            attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,
            provider,provider_revision,external_idempotency_key,request_hash,
            execution_token,state,provider_task_ref)
          SELECT attempt.id,attempt.action_id,attempt.run_id,attempt.org_id,
            attempt.user_id,'user',%s,'kie','v1','provider-idem-cancel',
            attempt.request_hash,attempt.execution_token,'accepted',
            'provider-task-cancel' FROM agent_action_attempts attempt WHERE attempt.id=%s
        """, (str(USER), fact.attempt_id))


def test_cancel_dispatch_handoff_preserves_provider_identity(database: str) -> None:
    _prepare_legacy_schema(database)
    _prepare_cancel_handoff_prerequisite(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    _prepare_projection_prerequisite(database)
    _apply_round_trip(database)
    _seed_dispatch_identity(database, batch)
    fact = batch.attempts[0]
    first = _runtime_call(database, """
      SELECT request_agent_runtime_media_message_cancel_v1(%s,%s,%s,%s)
    """, (batch.output_id, ORG, USER, "dispatch-cancel"))
    replay = _runtime_call(database, """
      SELECT request_agent_runtime_media_message_cancel_v1(%s,%s,%s,%s)
    """, (batch.output_id, ORG, USER, "dispatch-cancel"))
    assert first["outcome"] == replay["outcome"] == "cancel_requested"
    with psycopg.connect(database) as connection:
        attempt = connection.execute("""
          SELECT status,provider_task_ref,provider_idempotency_key,
                 provider_request_hash,external_receipt,ambiguity_evidence
          FROM agent_action_attempts WHERE id=%s
        """, (fact.attempt_id,)).fetchone()
        provider = connection.execute("""
          SELECT state,provider_task_ref,external_idempotency_key,request_hash
          FROM agent_runtime_provider_submission_facts WHERE attempt_id=%s
        """, (fact.attempt_id,)).fetchone()
        assert attempt[:3] == ("unknown", "provider-task-cancel", "provider-idem-cancel")
        assert attempt[4]["provider_task_ref"] == "provider-task-cancel"
        assert attempt[5]["provider_task_ref"] == "provider-task-cancel"
        assert attempt[5]["provider_idempotency_key"] == "provider-idem-cancel"
        assert attempt[5]["provider_request_hash"] == attempt[3]
        assert provider == (
            "accepted", "provider-task-cancel", "provider-idem-cancel", fact.request_hash,
        )
        assert connection.execute("""
          SELECT count(*) FROM agent_runtime_events
          WHERE correlation_id=%s AND event_type='action.unknown'
        """, (fact.action_id,)).fetchone()[0] == 1
        assert connection.execute("""
          SELECT count(*) FROM agent_runtime_media_cancel_requests
          WHERE action_id=%s AND idempotency_key='dispatch-cancel'
        """, (fact.action_id,)).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM agent_runs WHERE id=(SELECT run_id FROM "
            "agent_actions WHERE id=%s)", (fact.action_id,),
        ).fetchone()[0] == "cancelled"


def _retry_call(
    database_url: str, batch: object, key: str, ready: Event, pids: Queue,
) -> dict:
    with _connect(database_url, "everydayai_runtime") as connection:
        _settings(connection, "everydayai_runtime")
        connection.execute("SELECT set_config('app.request_id',%s,false)", (key,))
        pids.put(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
        ready.set()
        return connection.execute("""
          SELECT retry_agent_runtime_media_slot_v1(
            %s,%s,0,%s,0,%s,%s,%s,%s,NULL)
        """, (
            batch.output_id, CONVERSATION, batch.attempts[0].action_id,
            ORG, USER, key, key,
        )).fetchone()[0]


def test_retry_matches_projection_lock_order_without_duplicate_charge(database: str) -> None:
    _prepare_legacy_schema(database)
    batch = _seed_batch(database, 1, credits=1000)
    assert _prepare(database, batch.attempts[0])["outcome"] == "prepared"
    _prepare_projection_prerequisite(database)
    _apply_round_trip(database)
    fact = batch.attempts[0]
    _mark_retry_source_refunded(database, batch, 0)
    executor = ThreadPoolExecutor(max_workers=2)
    futures = []
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET status='failed',completed_at=clock_timestamp() "
            "WHERE id=%s", (fact.action_id,),
        )
        connection.execute("""
          UPDATE messages SET status='failed',content=(SELECT jsonb_agg(
            part||'{"slot_status":"failed"}'::jsonb ORDER BY ordinality)
            FROM jsonb_array_elements(content::jsonb) WITH ORDINALITY source(part,ordinality))::text
          WHERE id=%s
        """, (batch.output_id,))
        task_id = connection.execute(
            "SELECT task_id FROM agent_runtime_media_action_bindings WHERE action_id=%s",
            (fact.action_id,),
        ).fetchone()[0]
        before = connection.execute(
            "SELECT credits FROM users WHERE id=%s", (USER,),
        ).fetchone()[0]
        connection.execute("SELECT id FROM tasks WHERE id=%s FOR UPDATE", (task_id,))
        connection.execute("RESET ROLE")
        try:
            ready = (Event(), Event())
            pids: Queue = Queue()
            futures = [executor.submit(_retry_call, database, batch, key, signal, pids)
                       for key, signal in zip(
                           ("projection-race-1", "projection-race-2"), ready, strict=True,
                       )]
            assert all(signal.wait(timeout=5) for signal in ready)
            backend_pids = [pids.get(timeout=5), pids.get(timeout=5)]
            for _ in range(100):
                activity = connection.execute("""
                  SELECT pid,state,wait_event_type,wait_event FROM pg_stat_activity
                  WHERE pid=ANY(%s) ORDER BY pid
                """, (backend_pids,)).fetchall()
                if sum(row[1] == "active" for row in activity) == 2:
                    break
                time.sleep(0.02)
            lock_order_observed = len(activity) == 2
            connection.execute("""
              SELECT action_id FROM agent_runtime_media_action_bindings
              WHERE action_id=%s FOR UPDATE NOWAIT
            """, (fact.action_id,))
            connection.execute(
                "SELECT id FROM messages WHERE id=%s FOR UPDATE NOWAIT", (batch.output_id,),
            )
        finally:
            connection.commit()
    try:
        outcomes = sorted(future.result(timeout=10)["outcome"] for future in futures)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    assert lock_order_observed, activity
    assert outcomes == ["created", "slot_active"]
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT credits FROM users WHERE id=%s", (USER,),
        ).fetchone()[0] == before - 6
        assert connection.execute(
            "SELECT count(*) FROM agent_runtime_media_retry_lineage",
        ).fetchone()[0] == 1
        assert connection.execute("""
          SELECT count(*) FROM credit_transactions
          WHERE reason='Agent Runtime media slot retry reservation'
        """).fetchone()[0] == 1
