"""Serial ActionLoop regression for prepared Runtime media batches."""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_media_action_bindings_postgres_external import (
    AttemptFact,
    _assert_atomic_failure,
    _prepare,
    _prepare_legacy_schema,
    _seed_batch,
    _step_counts,
    _worker_call,
)


pytestmark = pytest.mark.external


def _record_provider_fact(database_url: str, action_id: UUID) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        cursor = connection.execute("""
          INSERT INTO agent_runtime_provider_submission_facts(
            attempt_id,action_id,run_id,org_id,user_id,scope_kind,scope_id,
            provider,provider_revision,external_idempotency_key,request_hash,
            execution_token,state,provider_task_ref
          )
          SELECT attempt.id,action.id,action.run_id,action.org_id,action.user_id,
                 session.scope_kind,session.scope_id,'mock','provider-v1',
                 'serial:'||action.id,action.request_hash,attempt.execution_token,
                 'accepted','provider-task:'||action.id
          FROM agent_actions action
          JOIN agent_action_attempts attempt ON attempt.action_id=action.id
          JOIN agent_runtime_sessions session ON session.id=action.session_id
          WHERE action.id=%s
        """, (action_id,))
        assert cursor.rowcount == 1


def _set_attempt_state(database_url: str, fact: AttemptFact, state: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_actions SET status=%s,accepted_at=clock_timestamp() WHERE id=%s",
            (state, fact.action_id),
        )
        if state == "accepted":
            connection.execute("""
              UPDATE agent_action_attempts SET status='accepted',
                dispatch_phase='accepted',
                accepted_at=clock_timestamp(),external_receipt=%s
              WHERE id=%s
            """, (Jsonb({"provider_task_ref": "provider-task-1"}), fact.attempt_id))
        else:
            connection.execute("""
              UPDATE agent_action_attempts SET status='unknown',
                ambiguity_evidence=%s
              WHERE id=%s
            """, (Jsonb({"error_code": "provider_unknown"}), fact.attempt_id))


def test_serial_batch_readback_survives_sibling_provider_fact(database: str) -> None:
    _prepare_legacy_schema(database)

    prepared = _seed_batch(database, 10, credits=1000)
    assert _prepare(database, prepared.attempts[0])["outcome"] == "prepared"
    _set_attempt_state(database, prepared.attempts[0], "accepted")
    _record_provider_fact(database, prepared.attempts[0].action_id)

    assert _prepare(database, prepared.attempts[1])["outcome"] == "already_prepared"
    assert _worker_call(
        database, "read_agent_runtime_media_binding_v1", prepared.attempts[1],
    )["outcome"] == "found"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _prepare(database, prepared.attempts[0])
    assert _worker_call(
        database, "read_agent_runtime_media_binding_v1", prepared.attempts[0],
    )["outcome"] == "found"

    _set_attempt_state(database, prepared.attempts[0], "unknown")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _prepare(database, prepared.attempts[0])
    assert _worker_call(
        database, "read_agent_runtime_media_binding_v1", prepared.attempts[0],
    )["outcome"] == "found"

    with pytest.raises(psycopg.errors.UniqueViolation):
        _worker_call(
            database, "prepare_agent_runtime_media_batch_v1", prepared.attempts[1],
            manifest_hash="f" * 64,
        )
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE agent_runtime_media_action_bindings "
            "SET action_arguments_hash=%s WHERE action_id=%s",
            ("f" * 64, prepared.attempts[1].action_id),
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        _prepare(database, prepared.attempts[1])

    partial = _seed_batch(database, 2, credits=1000)
    assert _prepare(database, partial.attempts[0])["outcome"] == "prepared"
    with psycopg.connect(database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "DELETE FROM agent_runtime_media_action_bindings WHERE action_id=%s",
            (partial.attempts[1].action_id,),
        )
    _record_provider_fact(database, partial.attempts[0].action_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _prepare(database, partial.attempts[1])
    assert _step_counts(database, partial.step_id) == (1, 1, 1)

    unprepared = _seed_batch(database, 2, credits=1000)
    _record_provider_fact(database, unprepared.attempts[0].action_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _prepare(database, unprepared.attempts[1])
    _assert_atomic_failure(database, unprepared)
