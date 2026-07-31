"""Hosted-Linux real PostgreSQL + Sandbox daemon composition contract."""

from __future__ import annotations

import argparse
import atexit
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from core.database import close_async_worker_db, get_async_worker_db
from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
)
from services.agent.runtime.domain import (
    ActionAttempt,
    ActionAttemptId,
    ActionAttemptStatus,
    ActionId,
    FencingToken,
    IdempotencyKey,
    Lease,
    RuntimeScope,
    ScopeKind,
)
from services.agent.runtime.executors.registry import ExecutorRegistry
from services.agent.runtime.executors.sandbox_job import SANDBOX_JOB_DESCRIPTOR
from services.agent.runtime.ports.executor import ExecutionOutcome
from services.agent.runtime.sandbox.composition import (
    build_sandbox_executor_components,
)
from tests.sandbox_daemon_health import wait_ready

ROOT = Path(os.environ["SANDBOX_JOB_ROOT"])
RUNTIME_REVISION = os.environ["SANDBOX_RUNTIME_REVISION"]
CASES_FILE = ROOT / "daemon-e2e-cases.json"
DAEMON_LOG = ROOT / "daemon-e2e-worker.log"
DAEMONS: list[subprocess.Popen] = []

def _cleanup_database_facts(cases: list[dict]) -> None:
    _admin(
        """
        SET ROLE everydayai_owner;
        DELETE FROM agent_runtime_worker_heartbeats
         WHERE worker_id LIKE 'sandbox-daemon-e2e%';
        DELETE FROM users WHERE id = ANY(%s);
        RESET ROLE
        """,
        ([case["user"] for case in cases],),
    )

def _admin(sql: str, params: tuple[object, ...] = ()) -> list[dict]:
    with psycopg.connect(
        os.environ["AR223_TEST_DATABASE_URL"], row_factory=dict_row,
        cursor_factory=psycopg.ClientCursor,
    ) as connection:
        cursor = connection.execute(sql, params)
    return list(cursor.fetchall()) if cursor.description else []

def _seed_case(name: str) -> dict[str, str]:
    ids = {
        key: str(uuid4()) for key in (
            "user", "conversation", "session", "command", "run", "step",
            "action", "attempt", "receipt", "intent", "execution",
        )
    }
    request_hash = hashlib.sha256(f"request:{name}".encode()).hexdigest()
    core_request_hash = request_hash[:32]
    arguments_hash = hashlib.sha256(f"arguments:{name}".encode()).hexdigest()
    external_key = f"daemon-e2e:{name}:{ids['action']}"
    _admin(
        """
        SET ROLE everydayai_owner;
        INSERT INTO users(id,credits) VALUES (%(user)s,100);
        INSERT INTO conversations(id,user_id,scope_type,scope_id)
        VALUES (%(conversation)s,%(user)s,'user',%(user)s::text);
        INSERT INTO agent_runtime_sessions(
          id,conversation_id,user_id,scope_kind,scope_id,created_by_user_id,
          agent_definition_id,agent_definition_revision
        ) VALUES (
          %(session)s,%(conversation)s,%(user)s,'user',%(user)s::text,
          %(user)s,'daemon-e2e','v1'
        );
        INSERT INTO agent_session_commands(
          id,session_id,user_id,command_type,idempotency_key,payload,request_hash
        ) VALUES (
          %(command)s,%(session)s,%(user)s,'submit_input',%(command_key)s,
          '{}'::jsonb,%(core_request_hash)s
        );
        INSERT INTO agent_runs(
          id,session_id,command_id,user_id,run_kind,status,idempotency_key,
          request_hash,blocking_action_count
        ) VALUES (
          %(run)s,%(session)s,%(command)s,%(user)s,'user','waiting_actions',
          %(run_key)s,%(core_request_hash)s,1
        );
        INSERT INTO agent_model_steps(
          id,run_id,session_id,user_id,step_number,status,model_id,provider,
          model_revision,prompt_revision,tool_catalog_revision,stop_reason,
          completed_at
        ) VALUES (
          %(step)s,%(run)s,%(session)s,%(user)s,1,'completed','model',
          'daemon-e2e','v1','v1','v1','tool_calls',clock_timestamp()
        );
        INSERT INTO agent_actions(
          id,session_id,run_id,model_step_id,user_id,action_index,
          stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,
          batch_hash,blocking,policy_decision,policy_snapshot,policy_revision,
          retry_disposition,status
        ) VALUES (
          %(action)s,%(session)s,%(run)s,%(step)s,%(user)s,0,
          %(tool_call)s,'code_execute','{}'::jsonb,%(arguments_hash)s,
          %(request_hash)s,%(batch_hash)s,true,'preauthorized',
          '{"source":"daemon-e2e"}'::jsonb,'policy-v1',
          'retry_after_reconcile','running'
        );
        INSERT INTO agent_action_attempts(
          id,action_id,session_id,run_id,user_id,attempt_number,status,
          dispatch_phase,worker_id,execution_token,lease_expires_at,
          idempotency_key,request_hash,retry_disposition,dispatched_at
        ) VALUES (
          %(attempt)s,%(action)s,%(session)s,%(run)s,%(user)s,1,'dispatching',
          'request_started','runtime-e2e',%(execution)s,
          clock_timestamp()+interval '10 minutes',%(attempt_key)s,
          %(request_hash)s,'retry_after_reconcile',clock_timestamp()
        );
        INSERT INTO agent_policy_receipts(
          id,action_id,session_id,run_id,user_id,decision,arguments_hash,
          executor_type,executor_revision,policy_revision,effective_scope,
          reason_codes,receipt_hash,expires_at
        ) VALUES (
          %(receipt)s,%(action)s,%(session)s,%(run)s,%(user)s,'allow',
          %(arguments_hash)s,'sandbox_job',1,'policy-v1','{}'::jsonb,
          ARRAY['approved'],%(receipt_hash)s,clock_timestamp()+interval '1 hour'
        );
        INSERT INTO agent_action_dispatch_intents(
          id,attempt_id,action_id,policy_receipt_id,execution_token,request_hash,
          executor_type,executor_revision,policy_revision,
          external_idempotency_key,recovery_mode
        ) VALUES (
          %(intent)s,%(attempt)s,%(action)s,%(receipt)s,%(execution)s,
          %(request_hash)s,'sandbox_job',1,'policy-v1',%(external_key)s,
          'reconcile_only'
        );
        RESET ROLE
        """,
        {
            **ids,
            "command_key": f"command:{ids['command']}",
            "run_key": f"run:{ids['run']}",
            "tool_call": f"call:{ids['action']}",
            "attempt_key": f"attempt:{ids['attempt']}",
            "request_hash": request_hash,
            "core_request_hash": core_request_hash,
            "arguments_hash": arguments_hash,
            "batch_hash": hashlib.sha256(f"batch:{name}".encode()).hexdigest(),
            "receipt_hash": hashlib.sha256(f"receipt:{name}".encode()).hexdigest(),
            "external_key": external_key,
        },
    )
    return {
        **ids, "name": name, "request_hash": request_hash,
        "external_key": external_key,
    }


def prepare() -> None:
    _admin(
        """
        SELECT set_config('app.access_kind','runtime_admin',false);
        SET ROLE everydayai_owner;
        UPDATE agent_runtime_control SET
          command_claim_enabled=true, projection_enabled=true,
          authorization_recovery_enabled=true, action_dispatch_enabled=true,
          tool_confirmation_enabled=true, non_safe_actions_enabled=true,
          code_execute_enabled=true, updated_at=clock_timestamp()
        WHERE singleton;
        RESET ROLE;
        SELECT current_setting('app.access_kind') AS access_kind
        """
    )
    cases = [_seed_case(name) for name in ("success", "crash", "cancel")]
    ROOT.mkdir(parents=True, exist_ok=True)
    CASES_FILE.write_text(json.dumps(cases), encoding="utf-8")
    os.chmod(CASES_FILE, 0o600)

def _attempt(case: dict, status: ActionAttemptStatus) -> ActionAttempt:
    accepted = status in {ActionAttemptStatus.ACCEPTED, ActionAttemptStatus.UNKNOWN}
    return ActionAttempt(
        attempt_id=ActionAttemptId(case["attempt"]),
        action_id=ActionId(case["action"]),
        scope=RuntimeScope(
            kind=ScopeKind.USER, scope_id=case["user"],
            user_id=case["user"], org_id=None,
        ),
        attempt_number=1, status=status, worker_id="runtime-e2e",
        idempotency_key=IdempotencyKey(f"attempt:{case['attempt']}"),
        request_hash=case["request_hash"],
        lease=Lease(
            fencing_token=FencingToken("daemon-e2e"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
        started_at=datetime.now(timezone.utc),
        accepted_at=datetime.now(timezone.utc) if accepted else None,
        session_id=case["session"], run_id=case["run"],
        external_receipt=(
            {"sandbox_job_id": case["job_id"]} if accepted else {}
        ),
    )

async def _runtime_components():
    raw = await get_async_worker_db(os.environ["RUNTIME_DATABASE_URL"])
    await raw.pool.wait(timeout=10)
    scoped = AsyncScopedDatabaseClient(raw, DatabaseScope(
        actor_user_id=None, org_id=None,
        access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="daemon-e2e",
    ))
    return build_sandbox_executor_components(
        runtime_database=scoped, workspace_root=ROOT,
        runtime_revision=RUNTIME_REVISION, registry=ExecutorRegistry(),
    )

async def _submit(components, case: dict, code: str) -> None:
    attempt = _attempt(case, ActionAttemptStatus.DISPATCHING)
    gate = type("_Gate", (), {
        "intent_id": case["intent"],
        "external_idempotency_key": case["external_key"],
    })()
    capability = components.capability_issuer.issue(
        attempt=attempt, descriptor=SANDBOX_JOB_DESCRIPTOR,
        phase="dispatch", dispatch_gate=gate,
    )["sandbox_job"]
    attempt = replace(attempt, capabilities={"sandbox_job": capability})
    receipt = await components.executor.dispatch(attempt, {
        "code": code,
        "external_idempotency_key": case["external_key"],
        "resource_limits": {
            "timeout_seconds": 90, "cpu_millis": 500,
            "memory_bytes": 128 * 1024 * 1024, "pids": 32,
            "disk_bytes": 4 * 1024 * 1024, "file_count": 16,
        },
        "_dispatch_context": {
            "dispatch_intent_id": case["intent"],
            "expected_action_version": 0, "expected_attempt_version": 0,
        },
        "input_manifest": {"schema_revision": 1, "items": []},
    })
    assert receipt.outcome is ExecutionOutcome.ACCEPTED
    case["job_id"] = receipt.external_receipt["sandbox_job_id"]

def _daemon() -> subprocess.Popen:
    with DAEMON_LOG.open("ab") as log:
        process = subprocess.Popen(
            [
                os.environ["SANDBOX_DAEMON_PYTHON"],
                "-m", "agent_runtime_worker_main",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(), start_new_session=True,
            stdout=log, stderr=subprocess.STDOUT,
        )
    DAEMONS.append(process)
    return process

def _stop_daemons() -> None:
    for process in DAEMONS:
        if process.poll() is not None:
            continue
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)

async def _job(runtime_db, job_id: str) -> dict:
    response = await runtime_db.rpc(
        "get_sandbox_job", {"p_job_id": job_id},
    ).execute()
    value = response.data
    return value if isinstance(value, dict) else json.loads(value)

async def _wait_status(runtime_db, job_id: str, wanted: set[str], timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await _job(runtime_db, job_id)
        job = value.get("job") or {}
        if job.get("status") in wanted:
            return job
        if job.get("status") in {"failed", "unknown", "cancelled"}:
            raise AssertionError(
                f"job {job_id} reached unexpected terminal status "
                f"{job.get('status')} reason={job.get('terminal_reason')} "
                f"ambiguity={job.get('ambiguity_evidence')} "
                f"stderr_length={job.get('stderr_original_length')} "
                f"stderr_sha256={job.get('stderr_sha256')}"
            )
        await asyncio.sleep(0.2)
    raise AssertionError(f"job {job_id} did not reach {sorted(wanted)}")


async def exercise() -> None:
    assert os.geteuid() != 0 and os.getegid() != 0
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    components = await _runtime_components()
    runtime_db = components.capability_issuer._jobs._database
    success, crash, cancel = cases
    await _submit(components, success, """
from pathlib import Path
import socket
import time
try:
    Path('/job/input/code.py').write_text('mutated')
    raise RuntimeError('input was writable')
except OSError:
    pass
try:
    socket.create_connection(('127.0.0.1', 55432), timeout=1)
    raise RuntimeError('network was available')
except OSError:
    pass
Path('/job/output/result.txt').write_text('daemon-e2e-success')
time.sleep(22)
print('daemon-e2e-success')
""")
    daemon = _daemon()
    await wait_ready(
        daemon, os.environ["AGENT_RUNTIME_HEALTH_SOCKET"], DAEMON_LOG,
    )
    terminal = await _wait_status(
        runtime_db, success["job_id"], {"succeeded"}, timeout=120,
    )
    assert terminal["artifact_manifest"]["items"]
    before = terminal["terminal_at"]
    reconcile_capability = components.capability_issuer.issue(
        attempt=_attempt(success, ActionAttemptStatus.ACCEPTED),
        descriptor=SANDBOX_JOB_DESCRIPTOR, phase="reconcile",
    )["sandbox_job"]
    readback_attempt = replace(
        _attempt(success, ActionAttemptStatus.ACCEPTED),
        capabilities={"sandbox_job": reconcile_capability},
    )
    readback = await components.executor.reconcile(readback_attempt)
    assert readback.outcome is ExecutionOutcome.COMPLETED
    repeated = await components.executor.reconcile(readback_attempt)
    assert repeated.outcome is ExecutionOutcome.COMPLETED
    assert (await _job(runtime_db, success["job_id"]))["job"]["terminal_at"] == before

    await _submit(components, crash, """
import time
time.sleep(60)
""")
    crashed_running = await _wait_status(
        runtime_db, crash["job_id"], {"running"},
    )
    os.killpg(daemon.pid, signal.SIGKILL)
    daemon.wait(timeout=10)
    subprocess.run(["pkill", "-KILL", "-x", "nsjail"], check=False)
    await asyncio.sleep(62)
    replacement = _daemon()
    unknown = await _wait_status(
        runtime_db, crash["job_id"], {"unknown"}, timeout=90,
    )
    deadline = time.monotonic() + 10
    while unknown["state_version"] < crashed_running["state_version"] + 2:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.1)
        unknown = (await _job(runtime_db, crash["job_id"]))["job"]
    assert unknown["ambiguity_evidence"]["kind"] in {
        "SANDBOX_EXECUTION_LEASE_EXPIRED", "EXECUTION_STATE_UNPROVEN",
    }

    await _submit(components, cancel, """
import time
while True:
    time.sleep(1)
""")
    await _wait_status(runtime_db, cancel["job_id"], {"running"})
    cancel_capability = components.capability_issuer.issue(
        attempt=_attempt(cancel, ActionAttemptStatus.ACCEPTED),
        descriptor=SANDBOX_JOB_DESCRIPTOR, phase="cancel",
    )["sandbox_job"]
    cancel_attempt = replace(
        _attempt(cancel, ActionAttemptStatus.ACCEPTED),
        capabilities={"sandbox_job": cancel_capability},
    )
    requested = await components.executor.cancel(cancel_attempt)
    assert requested.outcome is ExecutionOutcome.ACCEPTED
    cancelled = await _wait_status(
        runtime_db, cancel["job_id"], {"cancelled", "unknown"}, timeout=90,
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_accepted_at"] and cancelled["cancel_confirmed_at"]
    assert cancelled["cleanup_status"] == "completed"
    replacement.send_signal(signal.SIGTERM)
    replacement.wait(timeout=20)
    await close_async_worker_db()


def verify() -> None:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    ids = tuple(case["job_id"] for case in cases if "job_id" in case)
    atexit.register(_cleanup_database_facts, cases)
    rows = _admin(
        """
        SELECT status, state_version, ambiguity_evidence,
               cancel_accepted_at, cancel_confirmed_at, cleanup_status
          FROM agent_sandbox_jobs WHERE id = ANY(%s)
         ORDER BY queued_at
        """,
        (list(ids),),
    )
    if ids:
        assert [row["status"] for row in rows] == [
            "succeeded", "unknown", "cancelled",
        ]
        assert rows[0]["state_version"] >= 6
        assert rows[1]["state_version"] >= 5
        assert rows[2]["cancel_accepted_at"] and rows[2]["cancel_confirmed_at"]
    functions = _admin(
        """
        SELECT p.oid::regprocedure::text AS function_name
          FROM pg_proc p
         JOIN pg_namespace n ON n.oid=p.pronamespace
         JOIN pg_roles r ON r.oid=p.proowner
         WHERE n.nspname='public' AND r.rolname='everydayai_owner'
           AND p.proname NOT LIKE '\\_%%'
         ORDER BY 1
        """
    )
    allowed = {
        "claim_next_sandbox_job(text,integer)",
        "renew_sandbox_job_lease(uuid,uuid,bigint,bigint,integer)",
        "mark_sandbox_job_started(uuid,uuid,bigint,bigint,text)",
        "recover_expired_sandbox_job(uuid,bigint)",
        "record_sandbox_job_unknown(uuid,uuid,bigint,bigint,jsonb,jsonb,timestamp with time zone)",
        "record_sandbox_cancel_signal(uuid,uuid,bigint,bigint,text)",
        "finish_sandbox_job(uuid,uuid,bigint,bigint,text,text,text,jsonb)",
        "claim_sandbox_job_reconciliation(uuid,bigint,text,integer)",
        "renew_sandbox_job_reconciliation(uuid,uuid,bigint,integer)",
        "resolve_sandbox_job_reconciliation(uuid,uuid,bigint,text,text,text,jsonb)",
        "record_sandbox_job_cleanup(uuid,uuid,bigint,text,jsonb)",
        "claim_next_recoverable_sandbox_job(text,integer)",
        "claim_next_sandbox_job_reconciliation(text,integer)",
        "get_owned_sandbox_job(uuid,text,uuid,bigint)",
        "record_reconciled_sandbox_partials(uuid,uuid,bigint,jsonb)",
        "get_agent_runtime_worker_control(text)",
        "report_agent_runtime_worker_heartbeat(text,text,text,boolean,boolean,text,jsonb)",
    }
    effective = {
        row["function_name"] for row in functions
        if _admin(
            "SELECT has_function_privilege("
            "'everydayai_sandbox_worker',%s,'EXECUTE') AS allowed",
            (row["function_name"],),
        )[0]["allowed"]
    }
    assert effective <= allowed, sorted(effective - allowed)
    assert not _admin(
        "SELECT has_table_privilege("
        "'everydayai_sandbox_worker','agent_sandbox_jobs','SELECT') AS allowed"
    )[0]["allowed"]
    _cleanup_database_facts(cases)
    atexit.unregister(_cleanup_database_facts)
    if ids:
        assert not _admin(
            "SELECT id FROM agent_sandbox_jobs WHERE id = ANY(%s)",
            (list(ids),),
        )
    for child in ("inputs", "jobs", "checkpoints", "objects", "quarantine"):
        target = ROOT / child
        if target.exists():
            shutil.rmtree(target)
    CASES_FILE.unlink(missing_ok=True)
    DAEMON_LOG.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "exercise", "verify"))
    phase = parser.parse_args().phase
    if phase == "prepare":
        prepare()
    elif phase == "exercise":
        try:
            asyncio.run(asyncio.wait_for(exercise(), timeout=240))
        finally:
            _stop_daemons()
            if DAEMON_LOG.exists():
                print(DAEMON_LOG.read_text(
                    encoding="utf-8", errors="replace",
                )[-12000:])
    else:
        verify()


if __name__ == "__main__":
    main()
