"""Real PostgreSQL contract for AR-13 Command claim coordination."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("AR13_TEST_DATABASE_URL", "")
BOOTSTRAP = ROOT / "tests/fixtures/agent_runtime_core_postgres_bootstrap.sql"
MIGRATIONS = [
    ROOT / "migrations/212_agent_runtime_core_foundation.sql",
    ROOT / "migrations/213_agent_runtime_session_run_rpcs.sql",
    ROOT / "migrations/214_agent_runtime_run_lifecycle_rpcs.sql",
    ROOT / "migrations/215_agent_runtime_model_event_projection_rpcs.sql",
    ROOT / "migrations/216_agent_runtime_read_projection_capabilities.sql",
    ROOT / "migrations/219_01_agent_runtime_command_claim_foundation.sql",
    ROOT / "migrations/219_02_agent_runtime_command_claim_lifecycle.sql",
    ROOT
    / "migrations/219_02a_agent_runtime_command_claim_terminal_compatibility.sql",
]
ROLLBACK_01 = (
    ROOT / "migrations/rollback"
    / "219_01_agent_runtime_command_claim_foundation_rollback.sql"
)
ROLLBACK_02 = (
    ROOT / "migrations/rollback"
    / "219_02_agent_runtime_command_claim_lifecycle_rollback.sql"
)
ROLLBACK_02A = (
    ROOT / "migrations/rollback"
    / "219_02a_agent_runtime_command_claim_terminal_compatibility_rollback.sql"
)
USER_ID = "11111111-1111-1111-1111-111111111111"
CONVERSATION_ID = "33333333-3333-3333-3333-333333333333"


def psql(
    sql: str = "", *, path: Path | None = None, check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        "psql", "--no-psqlrc", "--quiet", "--set=ON_ERROR_STOP=1",
        "--tuples-only", "--no-align", "--dbname", DATABASE_URL,
    ]
    command.extend(
        ["--single-transaction", "--file", str(path)]
        if path else ["--command", sql]
    )
    return subprocess.run(
        command, check=check, capture_output=True, text=True,
    )


def value(sql: str) -> str:
    return psql(sql).stdout.strip().splitlines()[-1]


def worker(sql: str) -> dict[str, object]:
    result = value(
        "SET SESSION AUTHORIZATION everydayai_worker;"
        "SELECT set_config('app.access_kind','worker',false);"
        "SELECT set_config('app.request_id','ar13-worker',false);"
        + sql
    )
    return json.loads(result)


def runtime(sql: str) -> dict[str, object]:
    result = value(
        "SET SESSION AUTHORIZATION everydayai_runtime;"
        f"SELECT set_config('app.actor_user_id','{USER_ID}',false);"
        "SELECT set_config('app.org_id','',false);"
        "SELECT set_config('app.access_kind','runtime',false);"
        "SELECT set_config('app.request_id','ar13-runtime',false);"
        + sql
    )
    return json.loads(result)


def envelope(session_id: str, idempotency_key: str) -> dict[str, object]:
    return {
        "run_envelope": {
            "run_kind": "user",
            "context_receipt": {"revision": "v1"},
            "config_snapshot": {"revision": "v1"},
            "capability_snapshot": {"revision": "v1"},
            "request_identity": {
                "session_id": session_id,
                "idempotency_key": idempotency_key,
            },
        },
    }


def insert_command(
    session_id: str, *, command_type: str = "submit_input",
    payload: dict[str, object] | None = None,
    user_id: str = USER_ID, target_run_id: str | None = None,
) -> str:
    command_id = str(uuid4())
    body = payload or envelope(session_id, command_id)
    if target_run_id is not None:
        body["target_run_id"] = target_run_id
        body["reason"] = "user_cancelled"
    value(
        "SET ROLE everydayai_owner;"
        "INSERT INTO agent_session_commands("
        "id,session_id,user_id,command_type,idempotency_key,payload,request_hash"
        f") VALUES ('{command_id}','{session_id}','{user_id}',"
        f"'{command_type}','{command_id}',"
        f"'{json.dumps(body)}'::jsonb,md5(jsonb_build_object("
        f"'command_type','{command_type}','payload',"
        f"'{json.dumps(body)}'::jsonb)::text)) RETURNING id;"
    )
    return command_id


@pytest.fixture(scope="module", autouse=True)
def database() -> None:
    if not DATABASE_URL or "ar13" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR13_TEST_DATABASE_URL is required")
    psql(path=BOOTSTRAP)
    for migration in MIGRATIONS:
        psql(path=migration)


def create_session() -> str:
    return value(
        "SET ROLE everydayai_owner;"
        "INSERT INTO agent_runtime_sessions("
        "conversation_id,user_id,scope_kind,scope_id,created_by_user_id,"
        "agent_definition_id,agent_definition_revision"
        f") VALUES ('{CONVERSATION_ID}','{USER_ID}','user','{USER_ID}',"
        f"'{USER_ID}','default','v1') RETURNING id;"
    )


def expire(command_id: str) -> None:
    psql(
        "SET ROLE everydayai_owner;"
        "UPDATE agent_command_claims SET lease_expires_at="
        f"clock_timestamp()-interval '1 second' WHERE command_id='{command_id}';"
    )


def assert_claim_recovery(session_id: str) -> None:
    initial_payload = envelope(session_id, "rpc-idempotency")
    payload_json = json.dumps(initial_payload)
    submitted = runtime(
        "SELECT submit_session_command("
        f"'{session_id}','submit_input','rpc-idempotency',"
        f"'{payload_json}'::jsonb);"
    )
    same_request = runtime(
        "SELECT submit_session_command("
        f"'{session_id}','submit_input','rpc-idempotency',"
        f"'{payload_json}'::jsonb);"
    )
    changed_request = runtime(
        "SELECT submit_session_command("
        f"'{session_id}','submit_input','rpc-idempotency',"
        f"jsonb_set('{payload_json}'::jsonb,"
        "'{run_envelope,config_snapshot,revision}','\"v2\"'));"
    )
    assert same_request["outcome"] == "already_exists"
    assert same_request["entity_id"] == submitted["entity_id"]
    assert changed_request["outcome"] == "idempotency_conflict"
    first_id = submitted["entity_id"]
    first = worker(
        "SELECT claim_pending_agent_command_and_ensure_run('worker-a',90,3);"
    )
    assert first["outcome"] == "claimed"
    assert first["command_id"] == first_id
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT count(*) FROM agent_runs WHERE command_id='{first_id}';"
    ) == "1"

    discovery = worker(
        "SELECT get_agent_command_run_claim(NULL,'worker-a');"
    )
    assert discovery["command_id"] == first_id
    assert discovery["command_type"] == "submit_input"
    readback = worker(
        "SELECT get_agent_command_run_claim("
        f"'{first_id}','worker-a');"
    )
    assert readback["outcome"] == "found"
    assert readback["fencing_token"] == first["fencing_token"]
    renewed = worker(
        "SELECT renew_agent_command_claim("
        f"'{first_id}','{first['fencing_token']}',90);"
    )
    assert renewed["outcome"] == "renewed"

    expire(first_id)
    recovered = worker(
        "SELECT claim_pending_agent_command_and_ensure_run('worker-b',90,3);"
    )
    assert recovered["command_id"] == first_id
    assert recovered["attempt_number"] == 2
    stale = worker(
        "SELECT renew_agent_command_claim("
        f"'{first_id}','{first['fencing_token']}',90);"
    )
    assert stale["outcome"] == "ownership_lost"
    finished = worker(
        "SELECT finish_agent_command_claim("
        f"'{first_id}','{recovered['fencing_token']}','completed',NULL);"
    )
    assert finished["outcome"] == "completed"


def assert_concurrency_and_exhaustion(
    session_id: str,
) -> list[dict[str, object]]:
    command_ids = [insert_command(session_id) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(
            lambda worker_id: worker(
                "SELECT claim_pending_agent_command_and_ensure_run("
                f"'{worker_id}',90,3);"
            ),
            ("worker-c", "worker-d"),
        ))
    assert {item["command_id"] for item in claims} == set(command_ids)
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT count(*) FROM agent_runs WHERE command_id IN "
        f"('{command_ids[0]}','{command_ids[1]}');"
    ) == "2"

    exhausted_id = insert_command(session_id)
    exhausted_claim = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'worker-exhausted',90,1);"
    )
    assert exhausted_claim["command_id"] == exhausted_id
    expire(exhausted_id)
    exhausted = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'worker-exhausted-2',90,1);"
    )
    assert exhausted["outcome"] == "attempts_exhausted"
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT count(*) FROM agent_runtime_events "
        f"WHERE correlation_id='{exhausted_id}' "
        "AND event_type='command.attempts_exhausted';"
    ) == "1"
    return claims


def assert_scope_and_cancel(
    session_id: str, claims: list[dict[str, object]],
) -> None:
    wrong_scope_id = insert_command(
        session_id, user_id="44444444-4444-4444-4444-444444444444",
    )
    rejected = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'worker-scope',90,3);"
    )
    assert rejected["outcome"] == "scope_rejected"
    assert rejected["command_id"] == wrong_scope_id
    assert value(
        "SET ROLE everydayai_owner;"
        "SELECT status || ':' || error_class FROM agent_command_claims "
        f"WHERE command_id='{wrong_scope_id}';"
    ) == "failed:scope_rejected"
    psql(
        "SET ROLE everydayai_owner;"
        f"DELETE FROM agent_command_claims WHERE command_id='{wrong_scope_id}';"
        f"DELETE FROM agent_session_commands WHERE id='{wrong_scope_id}';"
    )

    pending_id = insert_command(session_id)
    target_run_id = claims[0]["run_id"]
    cancel_id = insert_command(
        session_id, command_type="cancel", target_run_id=target_run_id,
    )
    cancelled = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'worker-cancel',90,3);"
    )
    assert cancelled["command_id"] == cancel_id
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT status FROM agent_runs WHERE id='{target_run_id}';"
    ) == "cancelled"
    next_pending = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'worker-after-cancel',90,3);"
    )
    assert next_pending["command_id"] == pending_id

    missing_target = str(uuid4())
    cancel_before_run = insert_command(
        session_id, command_type="cancel", target_run_id=missing_target,
    )
    durable_cancel = worker(
        "SELECT claim_pending_agent_command_and_ensure_run("
        "'worker-cancel-before',90,3);"
    )
    assert durable_cancel["command_id"] == cancel_before_run
    assert value(
        "SET ROLE everydayai_owner;"
        f"SELECT status FROM agent_runs WHERE id='{missing_target}';"
    ) == "cancelled"


def assert_permissions_and_rollback() -> None:
    permission_matrix = value("""
        SELECT
            has_function_privilege(
                'everydayai_worker',
                'claim_pending_agent_command_and_ensure_run(text,integer,integer)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'everydayai_runtime',
                'claim_pending_agent_command_and_ensure_run(text,integer,integer)',
                'EXECUTE')
            AND NOT has_function_privilege(
                'everydayai_wecom_runtime',
                'get_agent_command_run_claim(uuid,text)', 'EXECUTE')
            AND NOT has_table_privilege(
                'everydayai_worker', 'agent_command_claims', 'SELECT')
            AND NOT has_table_privilege(
                'everydayai_runtime', 'agent_command_claims', 'SELECT')
            AND NOT has_table_privilege(
                'everydayai_wecom_runtime', 'agent_command_claims', 'SELECT')
            AND (SELECT relrowsecurity AND relforcerowsecurity
                   FROM pg_class WHERE relname='agent_command_claims');
    """)
    assert permission_matrix == "t"

    psql(path=ROLLBACK_02A)
    rollback_with_facts = psql(path=ROLLBACK_02, check=False)
    assert rollback_with_facts.returncode != 0
    assert "AGENT_COMMAND_CLAIM_ROLLBACK_FACTS_PRESENT" in (
        rollback_with_facts.stderr
    )
    psql("SET ROLE everydayai_owner;TRUNCATE agent_command_claims;")
    psql(path=ROLLBACK_02)
    psql(path=ROLLBACK_01)
    psql(path=MIGRATIONS[-3])
    psql(path=MIGRATIONS[-2])
    psql(path=MIGRATIONS[-1])
    assert value(
        "SELECT to_regclass('agent_command_claims') IS NOT NULL;"
    ) == "t"


def test_real_claim_recovery_fencing_permissions_and_rollback() -> None:
    session_id = create_session()
    assert_claim_recovery(session_id)
    claims = assert_concurrency_and_exhaustion(session_id)
    assert_scope_and_cancel(session_id, claims)
    assert_permissions_and_rollback()
