"""Real PostgreSQL Sandbox Job idempotency, fencing, role, and rollback."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest


pytestmark = pytest.mark.external
DATABASE_URL = os.getenv("AR222_TEST_DATABASE_URL", "")


@pytest.fixture(scope="module", autouse=True)
def dedicated_database() -> None:
    if os.getenv("RUN_AR222_DB_TEST") != "1" or not DATABASE_URL:
        pytest.skip("RUN_AR222_DB_TEST=1 and AR222_TEST_DATABASE_URL required")
    if "ar222" not in DATABASE_URL.lower():
        pytest.skip("dedicated AR222 database name required")


def _execute(
    sql: str, params: tuple[object, ...] = (), *,
    role: str | None = None, user_id: str = "", org_id: str = "",
) -> list[dict[str, object]]:
    with psycopg.connect(
        DATABASE_URL, row_factory=dict_row,
        cursor_factory=psycopg.ClientCursor,
    ) as connection:
        with connection.cursor() as cursor:
            if role:
                access_kind = (
                    "sandbox_worker"
                    if role == "everydayai_sandbox_worker" else "runtime"
                )
                cursor.execute(f"SET SESSION AUTHORIZATION {role}")
                cursor.execute(
                    "SELECT set_config('app.actor_user_id',%s,false)",
                    (user_id,),
                )
                cursor.execute(
                    "SELECT set_config('app.org_id',%s,false)", (org_id,),
                )
                cursor.execute(
                    "SELECT set_config('app.access_kind',%s,false)",
                    (access_kind,),
                )
                cursor.execute(
                    "SELECT set_config('app.request_id','ar222-test',false)",
                )
            cursor.execute(sql, params)
            return list(cursor.fetchall()) if cursor.description else []


def _decoded(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else json.loads(str(value))


def _seed_dispatch() -> dict[str, object]:
    _execute(
        "SET ROLE everydayai_owner; TRUNCATE agent_sandbox_jobs; RESET ROLE",
    )
    ids = {name: uuid4() for name in (
        "user", "conversation", "session", "command", "run", "step",
        "action", "attempt", "intent", "receipt", "execution",
    )}
    _execute(
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
          %(command)s,%(session)s,%(user)s,'submit_input',%(command_key)s,
          '{}','11111111111111111111111111111111'
        );
        INSERT INTO agent_runs(
          id,session_id,command_id,user_id,run_kind,status,idempotency_key,
          request_hash,blocking_action_count
        ) VALUES (
          %(run)s,%(session)s,%(command)s,%(user)s,'user','waiting_actions',
          %(run_key)s,'22222222222222222222222222222222',1
        );
        INSERT INTO agent_model_steps(
          id,run_id,session_id,user_id,step_number,status,model_id,provider,
          model_revision,prompt_revision,tool_catalog_revision,stop_reason,
          completed_at
        ) VALUES (
          %(step)s,%(run)s,%(session)s,%(user)s,1,'completed','model',
          'provider','v1','v1','v1','tool_calls',clock_timestamp()
        );
        INSERT INTO agent_actions(
          id,session_id,run_id,model_step_id,user_id,action_index,
          stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,
          batch_hash,blocking,policy_decision,policy_snapshot,policy_revision,
          retry_disposition,status
        ) VALUES (
          %(action)s,%(session)s,%(run)s,%(step)s,%(user)s,0,
          %(tool_call)s,'code_execute','{"code":"print(1)"}',%(arguments_hash)s,
          %(request_hash)s,%(batch_hash)s,true,'preauthorized',
          '{"source":"ar222-test"}','policy-v1','retry_after_reconcile','running'
        );
        INSERT INTO agent_action_attempts(
          id,action_id,session_id,run_id,user_id,attempt_number,status,
          dispatch_phase,worker_id,execution_token,lease_expires_at,
          idempotency_key,request_hash,retry_disposition,dispatched_at
        ) VALUES (
          %(attempt)s,%(action)s,%(session)s,%(run)s,%(user)s,1,'dispatching',
          'request_started','runtime',%(execution)s,
          clock_timestamp()+interval '10 minutes',%(attempt_key)s,
          %(request_hash)s,'retry_after_reconcile',clock_timestamp()
        );
        INSERT INTO agent_policy_receipts(
          id,action_id,session_id,run_id,user_id,decision,arguments_hash,
          executor_type,executor_revision,policy_revision,effective_scope,
          reason_codes,receipt_hash,expires_at
        ) VALUES (
          %(receipt)s,%(action)s,%(session)s,%(run)s,%(user)s,'allow',
          %(arguments_hash)s,'sandbox.python',1,'policy-v1','{}',
          ARRAY['approved'],%(receipt_hash)s,clock_timestamp()+interval '1 hour'
        );
        INSERT INTO agent_action_dispatch_intents(
          id,attempt_id,action_id,policy_receipt_id,execution_token,request_hash,
          executor_type,executor_revision,policy_revision,
          external_idempotency_key,recovery_mode
        ) VALUES (
          %(intent)s,%(attempt)s,%(action)s,%(receipt)s,%(execution)s,
          %(request_hash)s,'sandbox.python',1,'policy-v1',%(external_key)s,
          'reconcile_only'
        );
        RESET ROLE;
        """,
        {
            **ids,
            "user_text": str(ids["user"]),
            "command_key": f"command:{ids['command']}",
            "run_key": f"run:{ids['run']}",
            "tool_call": f"call:{ids['action']}",
            "attempt_key": f"attempt:{ids['attempt']}",
            "external_key": f"action:{ids['action']}:{'b' * 64}",
            "arguments_hash": "a" * 64,
            "request_hash": "b" * 64,
            "batch_hash": "c" * 64,
            "receipt_hash": uuid4().hex * 2,
        },
    )
    ids["external_key"] = f"action:{ids['action']}:{'b' * 64}"
    return ids


def _create(ids: dict[str, object], *, code_hash: str = "d" * 64) -> dict:
    result = _execute(
        """
        SELECT create_or_get_sandbox_job(
          %s,%s,%s,0,0,%s,%s,'sandbox.python',1,'python-v1',
          %s,%s,%s::jsonb,%s::jsonb
        ) AS value
        """,
        (
            ids["action"], ids["attempt"], ids["intent"],
            ids["external_key"], "b" * 64,
            f"ws-scope:user:{ids['user']}", code_hash,
            json.dumps({"schema_revision": 1, "items": []}),
            json.dumps({"timeout_seconds": 120, "memory_bytes": 1024}),
        ),
        role="everydayai_runtime", user_id=str(ids["user"]),
    )[0]["value"]
    return _decoded(result)


def _worker_rpc(sql: str, params: tuple[object, ...]) -> dict[str, object]:
    return _decoded(_execute(
        sql, params, role="everydayai_sandbox_worker",
    )[0]["value"])


def _claim() -> dict[str, object]:
    return _worker_rpc(
        "SELECT claim_next_sandbox_job('sandbox-1',60) AS value", (),
    )["job"]


def _receipt(*, partial: bool = False) -> dict[str, object]:
    empty_hash = (
        "e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855"
    )
    items = [{
        "temporary_object_ref": f"sandbox-temp:{uuid4()}",
        "content_sha256": "f" * 64,
        "size_bytes": 12,
        "media_type": "text/plain",
    }] if partial else []
    return {
        "receipt_revision": 1,
        "execution_outcome": "error",
        "stdout_summary": "",
        "stdout_original_length": 0,
        "stdout_sha256": empty_hash,
        "stdout_truncated": False,
        "stderr_summary": "",
        "stderr_original_length": 0,
        "stderr_sha256": empty_hash,
        "stderr_truncated": False,
        "artifact_manifest": {"schema_revision": 1, "items": []},
        "partial_effects": {"schema_revision": 1, "items": items},
        "materialization_status": "not_started",
        "cleanup_status": "completed" if partial else "not_required",
        "cleanup_evidence": (
            {"kind": "CLEANUP_CONFIRMED"} if partial else {}
        ),
    }


def _receipt_hash(receipt: dict[str, object]) -> str:
    return str(_execute(
        "SELECT _agent_sandbox_receipt_hash(%s::jsonb) AS value",
        (json.dumps(receipt),),
    )[0]["value"])


def test_50_concurrent_create_has_one_job_and_strict_readback() -> None:
    ids = _seed_dispatch()
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: _create(ids), range(50)))
    assert sum(item["outcome"] == "created" for item in results) == 1
    assert all(
        item["outcome"] in {"created", "already_created"} for item in results
    )
    assert len({item["job"]["id"] for item in results}) == 1
    count = _execute(
        "SELECT count(*) AS count FROM agent_sandbox_jobs "
        "WHERE external_idempotency_key=%s",
        (ids["external_key"],),
    )[0]["count"]
    assert count == 1


def test_same_key_different_binding_conflicts_without_mutation() -> None:
    ids = _seed_dispatch()
    created = _create(ids)
    conflict = _create(ids, code_hash="e" * 64)
    assert created["outcome"] == "created"
    assert conflict["outcome"] == "idempotency_conflict"
    assert _execute(
        "SELECT code_sha256 FROM agent_sandbox_jobs WHERE id=%s",
        (created["job"]["id"],),
    )[0]["code_sha256"] == "d" * 64


def test_claim_start_expiry_becomes_unknown_and_cannot_requeue() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _decoded(_execute(
        "SELECT claim_next_sandbox_job('sandbox-1',60) AS value",
        role="everydayai_sandbox_worker",
    )[0]["value"])["job"]
    starting = _decoded(_execute(
        "SELECT mark_sandbox_job_started(%s,%s,%s,%s,'starting') AS value",
        (
            claimed["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"],
        ),
        role="everydayai_sandbox_worker",
    )[0]["value"])["job"]
    _execute(
        "SET ROLE everydayai_owner; UPDATE agent_sandbox_jobs "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE id=%s; RESET ROLE",
        (job["id"],),
    )
    recovered = _decoded(_execute(
        "SELECT recover_expired_sandbox_job(%s,%s) AS value",
        (job["id"], starting["state_version"]),
        role="everydayai_sandbox_worker",
    )[0]["value"])
    assert recovered["outcome"] == "unknown"
    assert recovered["job"]["status"] == "unknown"
    assert _decoded(_execute(
        "SELECT claim_next_sandbox_job('sandbox-2',60) AS value",
        role="everydayai_sandbox_worker",
    )[0]["value"])["outcome"] == "not_found"


def test_unstarted_claim_requeues_and_fencing_blocks_stale_owner() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    renewed = _worker_rpc(
        "SELECT renew_sandbox_job_lease(%s,%s,%s,%s,60) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"],
        ),
    )
    assert renewed["outcome"] == "renewed"
    stale = _worker_rpc(
        "SELECT renew_sandbox_job_lease(%s,%s,%s,%s,60) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"],
        ),
    )
    assert stale["outcome"] == "stale_version"
    _execute(
        "SET ROLE everydayai_owner; UPDATE agent_sandbox_jobs "
        "SET lease_expires_at=clock_timestamp()-interval '1 second' "
        "WHERE id=%s; RESET ROLE",
        (job["id"],),
    )
    requeued = _worker_rpc(
        "SELECT recover_expired_sandbox_job(%s,%s) AS value",
        (job["id"], renewed["job"]["state_version"]),
    )
    assert requeued["outcome"] == "requeued"
    reclaimed = _claim()
    assert reclaimed["fencing_token"] == claimed["fencing_token"] + 1
    assert reclaimed["claim_token"] != claimed["claim_token"]


def test_cancel_requires_process_tree_confirmation_and_terminal_is_unique() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    cancelled = _decoded(_execute(
        "SELECT request_sandbox_job_cancel(%s,%s) AS value",
        (job["id"], claimed["state_version"]),
        role="everydayai_runtime", user_id=str(ids["user"]),
    )[0]["value"])
    assert cancelled["outcome"] == "cancel_requested"
    receipt = json.dumps(_receipt())
    receipt_hash = _receipt_hash(_receipt())
    blocked = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'cancelled',"
        "'PROCESS_TREE_TERMINATED',%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            cancelled["job"]["state_version"], receipt_hash, receipt,
        ),
    )
    assert blocked["outcome"] == "terminal_guard_failed"
    accepted = _worker_rpc(
        "SELECT record_sandbox_cancel_signal(%s,%s,%s,%s,'accepted') AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            cancelled["job"]["state_version"],
        ),
    )
    confirmed = _worker_rpc(
        "SELECT record_sandbox_cancel_signal(%s,%s,%s,%s,'confirmed') AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            accepted["job"]["state_version"],
        ),
    )
    finished = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'cancelled',"
        "'PROCESS_TREE_TERMINATED',%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            confirmed["job"]["state_version"], receipt_hash, receipt,
        ),
    )
    assert finished["outcome"] == "cancelled"
    duplicate = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'cancelled',"
        "'PROCESS_TREE_TERMINATED',%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            confirmed["job"]["state_version"], receipt_hash, receipt,
        ),
    )
    assert duplicate["outcome"] == "already_terminal"
    null_receipt = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'cancelled',"
        "'PROCESS_TREE_TERMINATED',%s,NULL::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            confirmed["job"]["state_version"], receipt_hash,
        ),
    )
    assert null_receipt["outcome"] == "receipt_hash_conflict"
    conflict = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'failed',"
        "'EXECUTION_FAILED',%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            confirmed["job"]["state_version"], receipt_hash, receipt,
        ),
    )
    assert conflict["outcome"] == "terminal_conflict"


def test_unknown_partial_contract_reconcile_and_sensitive_rejection() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    partial = _receipt(partial=True)["partial_effects"]
    unknown = _worker_rpc(
        "SELECT record_sandbox_job_unknown(%s,%s,%s,%s,%s::jsonb,"
        "%s::jsonb,clock_timestamp()+interval '3 days') AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], json.dumps({"kind": "OUTPUT_UNPROVEN"}),
            json.dumps(partial),
        ),
    )
    assert unknown["outcome"] == "unknown"
    assert unknown["job"]["cleanup_status"] == "pending"
    assert (
        datetime.fromisoformat(unknown["job"]["cleanup_deadline_at"])
        <= datetime.fromisoformat(unknown["job"]["partial_effects_recorded_at"])
        + timedelta(hours=24)
    )
    reconciled = _worker_rpc(
        "SELECT claim_sandbox_job_reconciliation(%s,%s,'scanner-1',60) AS value",
        (job["id"], unknown["job"]["state_version"]),
    )
    still = _worker_rpc(
        "SELECT resolve_sandbox_job_reconciliation(%s,%s,%s,'still_unknown',"
        "'STILL_UNKNOWN',%s,%s::jsonb) AS value",
        (
            job["id"], reconciled["job"]["reconciliation_token"],
            reconciled["job"]["state_version"], "3" * 64,
            json.dumps(_receipt()),
        ),
    )
    assert still["outcome"] == "still_unknown"

    ids = _seed_dispatch()
    job = _create(ids)["job"]
    claimed = _claim()
    unsafe = _receipt()
    unsafe["stderr_summary"] = "/Users/example/secret.py"
    rejected = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'failed','EXECUTION_FAILED',"
        "%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], _receipt_hash(unsafe), json.dumps(unsafe),
        ),
    )
    assert rejected["outcome"] == "malformed_receipt"
    unsafe["stderr_summary"] = "x" * 8193
    rejected = _worker_rpc(
        "SELECT finish_sandbox_job(%s,%s,%s,%s,'failed','EXECUTION_FAILED',"
        "%s,%s::jsonb) AS value",
        (
            job["id"], claimed["claim_token"], claimed["fencing_token"],
            claimed["state_version"], _receipt_hash(unsafe), json.dumps(unsafe),
        ),
    )
    assert rejected["outcome"] == "malformed_receipt"


def test_runtime_scope_mismatch_cannot_read_or_reuse_job() -> None:
    ids = _seed_dispatch()
    job = _create(ids)["job"]
    wrong_user = str(uuid4())
    with pytest.raises(psycopg.Error, match="AGENT_SANDBOX_SCOPE_MISMATCH"):
        _execute(
            "SELECT get_sandbox_job(%s) AS value", (job["id"],),
            role="everydayai_runtime", user_id=wrong_user,
        )
    with pytest.raises(psycopg.Error, match="AGENT_SANDBOX_SCOPE_MISMATCH"):
        _execute(
            "SELECT create_or_get_sandbox_job("
            "%s,%s,%s,0,0,%s,%s,'sandbox.python',1,'python-v1',"
            "%s,%s,%s::jsonb,%s::jsonb) AS value",
            (
                ids["action"], ids["attempt"], ids["intent"],
                ids["external_key"], "b" * 64,
                f"ws-scope:user:{ids['user']}", "d" * 64,
                json.dumps({"schema_revision": 1, "items": []}),
                json.dumps({"timeout_seconds": 120}),
            ),
            role="everydayai_runtime", user_id=wrong_user,
        )
