"""Real PostgreSQL AR-12 atomic terminal, fencing, blocker, and hash tests."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
import hashlib
from uuid import uuid4

import psycopg
import pytest


pytestmark = pytest.mark.external
DATABASE_URL = os.getenv("AR12_TEST_DATABASE_URL", "")


def execute(
    sql: str, params: tuple[object, ...] = (),
    *, worker: bool = False,
) -> list[tuple[object, ...]]:
    with psycopg.connect(
        DATABASE_URL, cursor_factory=psycopg.ClientCursor,
    ) as connection:
        with connection.cursor() as cursor:
            if worker:
                cursor.execute("SET SESSION AUTHORIZATION everydayai_worker")
                cursor.execute(
                    "SELECT set_config('app.access_kind','worker',false)"
                )
                cursor.execute(
                    "SELECT set_config('app.request_id','ar12-test',false)"
                )
            cursor.execute(sql, params, prepare=False)
            return cursor.fetchall() if cursor.description else []


def decoded(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else json.loads(str(value))


def seed_running_tool_step() -> dict[str, object]:
    ids = {
        name: uuid4() for name in (
            "user", "conversation", "session", "command", "run",
            "run_attempt", "step", "attempt", "token",
        )
    }
    execute(
        """
        SET ROLE everydayai_owner;
        INSERT INTO users(id,credits) VALUES (%(user)s,100);
        INSERT INTO conversations(id,user_id,scope_type,scope_id)
        VALUES (%(conversation)s,%(user)s,'user',%(user_text)s);
        INSERT INTO agent_runtime_sessions(
            id,conversation_id,user_id,scope_kind,scope_id,created_by_user_id,
            agent_definition_id,agent_definition_revision
        ) VALUES (
            %(session)s,%(conversation)s,%(user)s,'user',%(user_text)s,
            %(user)s,'default','v1'
        );
        INSERT INTO agent_session_commands(
            id,session_id,user_id,command_type,idempotency_key,payload,request_hash
        ) VALUES (
            %(command)s,%(session)s,%(user)s,'submit_input','command', '{}',
            '11111111111111111111111111111111'
        );
        INSERT INTO agent_runs(
            id,session_id,command_id,user_id,run_kind,status,idempotency_key,
            request_hash,execution_token,lease_expires_at,attempt_count,started_at
        ) VALUES (
            %(run)s,%(session)s,%(command)s,%(user)s,'user','running','run',
            '22222222222222222222222222222222',%(token)s,
            clock_timestamp()+interval '10 minutes',1,clock_timestamp()
        );
        INSERT INTO agent_run_attempts(
            id,run_id,user_id,attempt_number,execution_token,worker_id,
            lease_expires_at
        ) VALUES (
            %(run_attempt)s,%(run)s,%(user)s,1,%(token)s,'worker',
            clock_timestamp()+interval '10 minutes'
        );
        INSERT INTO agent_model_steps(
            id,run_id,session_id,user_id,step_number,status,model_id,provider,
            model_revision,prompt_revision,tool_catalog_revision,request_receipt
        ) VALUES (
            %(step)s,%(run)s,%(session)s,%(user)s,1,'running','model','provider',
            'v1','v1','tools-v1','{}'
        );
        INSERT INTO agent_model_attempts(
            id,model_step_id,run_id,session_id,user_id,attempt_number,
            request_hash,idempotency_key,provider,status,dispatch_phase,
            request_receipt,worker_id,execution_token,lease_expires_at,
            dispatched_at
        ) VALUES (
            %(attempt)s,%(step)s,%(run)s,%(session)s,%(user)s,1,
            %(model_hash)s,'attempt','provider','dispatching','request_started',
            '{}','worker',%(token)s,clock_timestamp()+interval '10 minutes',
            clock_timestamp()
        );
        INSERT INTO agent_model_credit_settlements(
            model_step_id,reservation_attempt_id,billing_user_id,reservation_key,
            status,reserved_credits
        ) VALUES (%(step)s,%(attempt)s,%(user)s,%(reserve_key)s,'reserved',0);
        """,
        {
            **ids,
            "user_text": str(ids["user"]),
            "model_hash": "a" * 64,
            "reserve_key": f"reserve:{ids['step']}",
        },
    )
    return ids


def action_batch(ids: dict[str, object], *, blocking: bool) -> list[dict]:
    return [{
        "action_id": str(uuid4()),
        "index": 0,
        "stable_tool_call_id": "call-0",
        "provider_call_id": "provider-call-0",
        "tool_name": "search_knowledge",
        "arguments": {"query": "inventory"},
        "arguments_hash": hashlib.md5(
            json.dumps({"query": "inventory"}).encode()
        ).hexdigest(),
        "request_hash": "c" * 32,
        "wave": 0,
        "dependencies": [],
        "blocking": blocking,
        "policy_decision": "preauthorized",
        "policy_snapshot": {"source": "explicit-test-contract"},
        "policy_revision": "v1",
        "retry_disposition": "retry_safe",
    }]


def database_batch_hash(
    step_id: object, actions: list[dict],
) -> str:
    return str(execute(
        """
        SELECT _agent_action_batch_hash(
            _canonical_agent_action_batch(step,%s::jsonb)
        ) FROM agent_model_steps step WHERE id=%s
        """,
        (json.dumps(actions), step_id),
    )[0][0])


def terminal(
    ids: dict[str, object], actions: list[dict], batch_hash: str,
) -> dict[str, object]:
    return decoded(execute(
        """
        SELECT complete_model_attempt_step_and_create_actions(
            %s,%s,0,0,%s,'{}',%s,'tool_calls','{}',0,%s,%s::jsonb
        )
        """,
        (
            ids["attempt"], ids["token"], "a" * 64, "d" * 64,
            batch_hash, json.dumps(actions),
        ),
        worker=True,
    )[0][0])


@pytest.fixture(autouse=True)
def dedicated_database() -> None:
    if os.getenv("RUN_AR12_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR12_DB_TEST=1 and AR12_TEST_DATABASE_URL required")
    if "ar12" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR12 database name required")


def test_blocking_terminal_waits_and_last_result_wakes_once() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    receipt = terminal(ids, actions, database_batch_hash(ids["step"], actions))
    assert receipt["run_status"] == "waiting_actions"
    run = execute(
        "SELECT status,blocking_action_count,execution_token,lease_expires_at "
        "FROM agent_runs WHERE id=%s", (ids["run"],),
    )[0]
    assert run == ("waiting_actions", 1, None, None)
    assert execute(
        "SELECT outcome,ended_at IS NOT NULL FROM agent_run_attempts WHERE id=%s",
        (ids["run_attempt"],),
    )[0] == ("completed", True)

    claim = decoded(execute(
        "SELECT claim_ready_agent_actions('action-worker',10,120)",
        worker=True,
    )[0][0])
    attempt = next(
        item for item in claim["attempts"]
        if item["run_id"] == str(ids["run"])
    )
    dispatch = decoded(execute(
        "SELECT mark_agent_action_dispatching(%s,%s,0,%s)",
        (attempt["id"], attempt["execution_token"], "c" * 32),
        worker=True,
    )[0][0])
    result = {
        "status": "success", "summary": "ok", "data": {},
        "artifact_ids": [], "usage": {}, "cost": {},
        "external_receipt": {},
    }
    completed = decoded(execute(
        "SELECT complete_agent_action(%s,%s,%s,%s,%s::jsonb)",
        (
            attempt["id"], attempt["execution_token"],
            dispatch["state_version"], "c" * 32, json.dumps(result),
        ),
        worker=True,
    )[0][0])
    assert completed["run_status"] == "queued"
    assert completed["blocking_action_count"] == 0
    replay = decoded(execute(
        "SELECT complete_agent_action(%s,%s,%s,%s,%s::jsonb)",
        (
            attempt["id"], attempt["execution_token"],
            dispatch["state_version"], "c" * 32, json.dumps(result),
        ),
        worker=True,
    )[0][0])
    assert replay["outcome"] == "already_completed"
    assert execute(
        "SELECT count(*) FROM agent_runtime_events "
        "WHERE run_id=%s AND event_type='run.resumed'", (ids["run"],),
    )[0][0] == 1


def test_zero_blocking_keeps_run_claim() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=False)
    receipt = terminal(ids, actions, database_batch_hash(ids["step"], actions))
    assert receipt["run_status"] == "running"
    assert execute(
        "SELECT execution_token,lease_expires_at IS NOT NULL "
        "FROM agent_runs WHERE id=%s", (ids["run"],),
    )[0] == (ids["token"], True)
    assert execute(
        "SELECT ended_at FROM agent_run_attempts WHERE id=%s",
        (ids["run_attempt"],),
    )[0][0] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_id", "99999999-9999-9999-9999-999999999999"),
        ("index", 7),
        ("stable_tool_call_id", "call-tampered"),
        ("provider_call_id", "provider-tampered"),
        ("tool_name", "artifact_get"),
        ("arguments_hash", "e" * 32),
        ("wave", 2),
        ("dependencies", ["88888888-8888-8888-8888-888888888888"]),
        ("blocking", False),
        ("policy_revision", "v2"),
        ("retry_disposition", "non_retryable"),
    ],
)
def test_tampered_batch_with_reused_hash_has_zero_mutation(
    field: str, value: object,
) -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    original_hash = database_batch_hash(ids["step"], actions)
    actions[0][field] = value
    response = terminal(ids, actions, original_hash)
    assert response["outcome"] == "batch_hash_conflict"
    assert execute(
        "SELECT status FROM agent_model_attempts WHERE id=%s",
        (ids["attempt"],),
    )[0][0] == "dispatching"
    assert execute(
        "SELECT status FROM agent_model_credit_settlements WHERE model_step_id=%s",
        (ids["step"],),
    )[0][0] == "reserved"
    assert execute(
        "SELECT count(*) FROM agent_actions WHERE model_step_id=%s",
        (ids["step"],),
    )[0][0] == 0


def test_same_tool_terminal_concurrent_replay_creates_one_batch() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    batch_hash = database_batch_hash(ids["step"], actions)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(
            lambda _: terminal(ids, actions, batch_hash), range(2),
        ))
    assert sorted(item["outcome"] for item in receipts) == [
        "already_completed", "completed",
    ]
    assert execute(
        "SELECT count(*) FROM agent_actions WHERE model_step_id=%s",
        (ids["step"],),
    )[0][0] == 1


def test_accepted_to_unknown_and_claimed_cancelled_by_run() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    claim = decoded(execute(
        "SELECT claim_ready_agent_actions('action-worker',10,120)",
        worker=True,
    )[0][0])
    attempt = next(
        item for item in claim["attempts"]
        if item["run_id"] == str(ids["run"])
    )
    dispatch = decoded(execute(
        "SELECT mark_agent_action_dispatching(%s,%s,0,%s)",
        (attempt["id"], attempt["execution_token"], "c" * 32), worker=True,
    )[0][0])
    accepted = decoded(execute(
        "SELECT mark_agent_action_accepted(%s,%s,%s,%s,%s::jsonb)",
        (
            attempt["id"], attempt["execution_token"],
            dispatch["state_version"], "c" * 32,
            json.dumps({"external_id": "provider-1"}),
        ), worker=True,
    )[0][0])
    unknown = decoded(execute(
        "SELECT record_agent_action_unknown(%s,%s,%s,%s,%s::jsonb)",
        (
            attempt["id"], attempt["execution_token"],
            accepted["state_version"], "c" * 32,
            json.dumps({"kind": "outcome_unproven"}),
        ), worker=True,
    )[0][0])
    assert unknown["outcome"] == "unknown"
    run_version = execute(
        "SELECT state_version FROM agent_runs WHERE id=%s", (ids["run"],),
    )[0][0]
    cancelled = decoded(execute(
        "SELECT cancel_agent_run(%s,%s,'user_cancelled')",
        (ids["run"], run_version), worker=True,
    )[0][0])
    assert cancelled["outcome"] == "cancelled"
    assert execute(
        "SELECT status FROM agent_action_attempts WHERE id=%s",
        (attempt["id"],),
    )[0][0] == "cancelled"
    assert execute(
        "SELECT blocking_action_count FROM agent_runs WHERE id=%s",
        (ids["run"],),
    )[0][0] == 0


def test_claimed_before_dispatch_can_fail_with_error_result() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    claim = decoded(execute(
        "SELECT claim_ready_agent_actions('action-worker',10,120)",
        worker=True,
    )[0][0])
    attempt = next(
        item for item in claim["attempts"]
        if item["run_id"] == str(ids["run"])
    )
    failed = decoded(execute(
        "SELECT fail_claimed_agent_action(%s,%s,0,%s,%s)",
        (
            attempt["id"], attempt["execution_token"], "c" * 32,
            "PREPARE_FAILED",
        ), worker=True,
    )[0][0])
    assert failed["outcome"] == "failed"
    assert failed["action_status"] == "queued"
    assert execute(
        "SELECT status,blocking_action_count FROM agent_runs WHERE id=%s",
        (ids["run"],),
    )[0] == ("waiting_actions", 1)
    retried = decoded(execute(
        "SELECT claim_ready_agent_actions('action-worker-2',10,120)",
        worker=True,
    )[0][0])
    retry_attempt = next(
        item for item in retried["attempts"]
        if item["run_id"] == str(ids["run"])
    )
    assert retry_attempt["attempt_number"] == 2


def test_claimed_before_dispatch_is_cancelled_only_by_run() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    claim = decoded(execute(
        "SELECT claim_ready_agent_actions('action-worker',10,120)",
        worker=True,
    )[0][0])
    attempt = next(
        item for item in claim["attempts"]
        if item["run_id"] == str(ids["run"])
    )
    run_version = execute(
        "SELECT state_version FROM agent_runs WHERE id=%s", (ids["run"],),
    )[0][0]
    execute(
        "SELECT cancel_agent_run(%s,%s,'cancel_before_dispatch')",
        (ids["run"], run_version), worker=True,
    )
    assert execute(
        "SELECT status FROM agent_action_attempts WHERE id=%s",
        (attempt["id"],),
    )[0][0] == "cancelled"


def test_permission_matrix_denies_table_and_runtime_rpc_access() -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        execute(
            "SELECT count(*) FROM agent_actions",
            worker=True,
        )
    with psycopg.connect(
        DATABASE_URL, cursor_factory=psycopg.ClientCursor,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION everydayai_runtime")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(
                    "SELECT claim_ready_agent_actions('runtime',1,120)"
                )


def test_sensitive_argument_key_rejects_entire_batch() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    actions[0]["arguments"] = {"Authorization": "Bearer secret"}
    actions[0]["arguments_hash"] = hashlib.md5(
        json.dumps(actions[0]["arguments"]).encode()
    ).hexdigest()
    batch_hash = database_batch_hash(ids["step"], actions)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        terminal(ids, actions, batch_hash)
    assert execute(
        "SELECT status FROM agent_model_attempts WHERE id=%s",
        (ids["attempt"],),
    )[0][0] == "dispatching"
    assert execute(
        "SELECT count(*) FROM agent_actions WHERE model_step_id=%s",
        (ids["step"],),
    )[0][0] == 0


def test_expired_claim_retries_but_expired_dispatch_becomes_unknown() -> None:
    ids = seed_running_tool_step()
    actions = action_batch(ids, blocking=True)
    terminal(ids, actions, database_batch_hash(ids["step"], actions))
    claim = decoded(execute(
        "SELECT claim_ready_agent_actions('worker-1',10,120)", worker=True,
    )[0][0])
    first = next(
        item for item in claim["attempts"]
        if item["run_id"] == str(ids["run"])
    )
    execute(
        "SET ROLE everydayai_owner; UPDATE agent_action_attempts "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
        (first["id"],),
    )
    recovered = decoded(execute(
        "SELECT recover_expired_agent_action_attempt(%s,0,'worker-2',120)",
        (first["id"],), worker=True,
    )[0][0])
    assert recovered["outcome"] == "claimed"
    dispatch = decoded(execute(
        "SELECT mark_agent_action_dispatching(%s,%s,0,%s)",
        (recovered["attempt_id"], recovered["execution_token"], "c" * 32),
        worker=True,
    )[0][0])
    execute(
        "SET ROLE everydayai_owner; UPDATE agent_action_attempts "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
        (recovered["attempt_id"],),
    )
    ambiguous = decoded(execute(
        "SELECT recover_expired_agent_action_attempt(%s,%s,'worker-3',120)",
        (recovered["attempt_id"], dispatch["state_version"]), worker=True,
    )[0][0])
    assert ambiguous["outcome"] == "unknown"
    assert execute(
        "SELECT status FROM agent_actions WHERE id=%s",
        (recovered["action_id"],),
    )[0][0] == "unknown"
