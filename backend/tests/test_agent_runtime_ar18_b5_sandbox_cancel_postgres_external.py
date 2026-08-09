from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from services.agent.runtime.sandbox.receipt import build_receipt
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import (
    _apply, _seed_specialist_action,
)


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "227_26_agent_runtime_sandbox_cancel_handoff.sql"
ROLLBACK = "rollback/227_26_agent_runtime_sandbox_cancel_handoff_rollback.sql"
ORG = "22222222-2222-2222-2222-222222222222"


def _prepare(database: str) -> None:
    with psycopg.connect(database) as conn:
        conn.execute("DO $$ BEGIN IF to_regrole('everydayai_agent_model_gateway') IS NULL THEN CREATE ROLE everydayai_agent_model_gateway LOGIN; END IF; END $$")
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("CREATE TABLE deleted_files(id BIGSERIAL PRIMARY KEY,org_id UUID,user_id UUID,relative_path TEXT NOT NULL,oss_object_key TEXT NOT NULL,purged BOOLEAN NOT NULL DEFAULT FALSE)")
        conn.execute("CREATE TABLE scheduled_tasks(id UUID PRIMARY KEY,org_id UUID,user_id UUID,status TEXT NOT NULL DEFAULT 'active',updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())")
        conn.commit()
    for index in range(1, 20):
        _apply(database, next((ROOT / "migrations").glob(f"226_{index:02d}_*.sql")).name)
    for name in (
        "227_01_agent_runtime_production_closure.sql",
        "227_04_agent_runtime_provider_submission_facts.sql",
        "227_05_agent_runtime_scheduler_cas.sql",
        "227_06_agent_runtime_tenant_kill_control.sql",
        "227_07_agent_runtime_kill_epoch_fence.sql",
        "227_08_agent_runtime_facts_recovery_fence.sql",
        "227_24_agent_runtime_provider_cancel_handoff.sql",
        MIGRATION,
    ):
        _apply(database, name)


def _rpc(database: str, role: str, function: str, params: tuple[object, ...]):
    url = database.replace("postgres@", f"{role}@")
    kind = "sandbox_worker" if role == "everydayai_sandbox_worker" else "agent_runtime"
    with psycopg.connect(url) as conn:
        conn.execute("SELECT set_config('app.access_kind',%s,false)", (kind,))
        conn.execute("SELECT set_config('app.request_id','ar18-b5',false)")
        adapted = tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in params)
        value = conn.execute(
            f"SELECT {function}({','.join(['%s'] * len(params))})", adapted,
        ).fetchone()[0]
        conn.commit()
        return value


def _seed(database: str, status: str) -> tuple[dict[str, str], dict[str, object]]:
    conversation_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_policy_receipts SET receipt_hash=%s WHERE receipt_hash=%s", (uuid4().hex * 2, "d" * 64))
        conn.execute("INSERT INTO conversations(id,user_id,org_id,scope_type,scope_id) VALUES(%s,%s,%s,'user',%s)", (conversation_id, "44444444-4444-4444-4444-444444444444", ORG, "44444444-4444-4444-4444-444444444444"))
        conn.commit()
    ids = _seed_specialist_action(database, conversation_id)
    claim_token = str(uuid4())
    job_id = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_actions SET tool_name='code_execute',arguments=%s,status='accepted',accepted_at=clock_timestamp() WHERE id=%s", (Jsonb({"code": "print(1)"}), ids["action"]))
        conn.execute("UPDATE agent_action_attempts SET status='accepted',dispatch_phase='accepted',accepted_at=clock_timestamp(),external_receipt=%s,next_reconcile_at=clock_timestamp(),state_version=1 WHERE id=%s", (Jsonb({"sandbox_job_id": job_id}), ids["attempt"]))
        conn.execute("UPDATE agent_action_dispatch_intents SET executor_type='sandbox_job',executor_revision=1,recovery_mode='reconcile_only' WHERE attempt_id=%s", (ids["attempt"],))
        conn.execute("UPDATE agent_runs SET status='cancelled',blocking_action_count=0,execution_token=NULL,lease_expires_at=NULL,completed_at=clock_timestamp(),state_version=2 WHERE id=%s", (ids["run"],))
        conn.execute("INSERT INTO agent_runtime_owner_fences(owner_kind,owner_id,org_id,execution_token,tenant_kill_epoch,state_version,status) VALUES('attempt',%s,%s,%s,0,1,'active')", (ids["attempt"], ORG, ids["token"]))
        conn.execute("""
            INSERT INTO agent_sandbox_jobs(
              id,session_id,run_id,action_id,attempt_id,dispatch_intent_id,
              org_id,user_id,external_idempotency_key,request_hash,
              executor_type,executor_revision,runtime,runtime_revision,
              workspace_scope_ref,code_ref,code_sha256,input_manifest,
              resource_limits,status,ambiguity_evidence,claim_worker_id,claim_token,
              fencing_token,lease_expires_at
            ) SELECT %s,%s,%s,%s,%s,intent.id,%s,%s,%s,%s,
              'sandbox_job',1,'python','sandbox-v1',%s,%s,%s,%s,%s,%s,%s,
              CASE WHEN %s IN ('claimed','starting','running') THEN 'old-worker' END,
              CASE WHEN %s IN ('claimed','starting','running') THEN %s::uuid END,
              CASE WHEN %s IN ('claimed','starting','running') THEN 1 ELSE 0 END,
              CASE WHEN %s IN ('claimed','starting','running') THEN clock_timestamp()+interval '10 minutes' END
            FROM agent_action_dispatch_intents intent WHERE intent.attempt_id=%s
        """, (
            job_id, ids["session"], ids["run"], ids["action"], ids["attempt"],
            ORG, "44444444-4444-4444-4444-444444444444", ids["attempt"],
            ids["request_hash"], "ws-scope:user:44444444-4444-4444-4444-444444444444",
            f"agent-action:{ids['action']}:arguments.code", "b" * 64,
              Jsonb({"schema_revision": 1, "items": []}), Jsonb({"timeout_seconds": 60}),
            status, Jsonb({"kind": "SANDBOX_WORKER_CRASH"} if status == "unknown" else {}),
            status, status, claim_token, status, status, ids["attempt"],
        ))
        conn.commit()
    claim = _rpc(database, "everydayai_agent_runtime_worker",
                 "claim_next_agent_action_reconciliation", ("b5-runtime", 120, 0))
    assert claim["operation"] == "cancel"
    return ids, {"id": job_id, "claim_token": claim_token, "action_claim": claim}


def _request(database: str, ids, facts):
    claim = facts["action_claim"]
    return _rpc(database, "everydayai_agent_runtime_worker",
                "request_agent_runtime_sandbox_cancel_v1", (
                    facts["id"], ids["attempt"], claim["execution_token"],
                    claim["state_version"], ids["request_hash"],
                ))


def test_b5_queued_proof_finalizer_acl_and_rollback(database: str) -> None:
    _prepare(database)
    ids, facts = _seed(database, "queued")
    requested = _request(database, ids, facts)
    assert requested["outcome"] == "cancel_requested"
    claimed = _rpc(database, "everydayai_sandbox_worker",
                   "claim_next_sandbox_cancel_v1", ("queued-cancel", 60))
    assert claimed["outcome"] == "claimed"
    assert claimed["job"]["cancel_confirmed_at"] is not None
    digest, receipt = build_receipt(
        execution_outcome="interrupted", stdout=b"", stderr=b"", cleaned=True,
    )
    cancelled = _rpc(database, "everydayai_sandbox_worker", "finish_sandbox_job", (
        facts["id"], claimed["job"]["claim_token"],
        claimed["job"]["fencing_token"], claimed["job"]["state_version"],
        "cancelled", "CANCELLED_BEFORE_START", digest, receipt,
    ))
    assert cancelled["outcome"] == "cancelled"
    claim = facts["action_claim"]
    for cleanup_status in ("failed", "unknown"):
        with psycopg.connect(database) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("UPDATE agent_sandbox_jobs SET cleanup_status=%s,cleanup_evidence=%s WHERE id=%s", (cleanup_status, Jsonb({"kind": "CLEANUP_UNPROVEN"}), facts["id"]))
            conn.commit()
        with pytest.raises(Exception, match="CANCEL_PROOF_INVALID"):
            _rpc(database, "everydayai_agent_runtime_worker",
                 "finalize_agent_action_sandbox_cancel_v1", (
                     ids["attempt"], claim["execution_token"], claim["state_version"],
                     ids["request_hash"], facts["id"],
                     cancelled["job"]["state_version"], cancelled["job"]["receipt_hash"],
                 ))
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_sandbox_jobs SET cleanup_status='not_required',cleanup_evidence='{}' WHERE id=%s", (facts["id"],))
        conn.commit()
    finalized = _rpc(database, "everydayai_agent_runtime_worker",
                     "finalize_agent_action_sandbox_cancel_v1", (
                         ids["attempt"], claim["execution_token"], claim["state_version"],
                         ids["request_hash"], facts["id"],
                         cancelled["job"]["state_version"], cancelled["job"]["receipt_hash"],
                     ))
    assert finalized["outcome"] == "cancelled"
    assert finalized["run_status"] == "cancelled"
    assert finalized["blocking_action_count"] == 0
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT a.status,t.status,r.status,r.blocking_action_count FROM agent_actions a JOIN agent_action_attempts t ON t.id=%s JOIN agent_runs r ON r.id=a.run_id WHERE a.id=%s", (ids["attempt"], ids["action"])).fetchone() == ("cancelled", "cancelled", "cancelled", 0)
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='agent_sandbox_jobs'::regclass").fetchone() == (True, True)
        assert conn.execute("SELECT has_table_privilege('everydayai_agent_runtime_worker','agent_sandbox_jobs','SELECT')").fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','request_agent_runtime_sandbox_cancel_v1(uuid,uuid,uuid,bigint,text)','EXECUTE')").fetchone()[0] is True
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','request_sandbox_job_cancel(uuid,bigint)','EXECUTE')").fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_sandbox_worker','request_agent_runtime_sandbox_cancel_v1(uuid,uuid,uuid,bigint,text)','EXECUTE')").fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_sandbox_worker','claim_next_sandbox_cancel_v1(text,integer)','EXECUTE')").fetchone()[0] is True
        function_def = conn.execute("SELECT pg_get_functiondef('request_agent_runtime_sandbox_cancel_v1(uuid,uuid,uuid,bigint,text)'::regprocedure)").fetchone()[0]
        assert "SET search_path TO 'pg_catalog', 'public'" in function_def
    with pytest.raises(Exception, match="ROLLBACK_PENDING_FACTS"):
        _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("DELETE FROM agent_sandbox_jobs WHERE id=%s", (facts["id"],))
        conn.execute("UPDATE agent_action_attempts SET reconciliation_operation=NULL,reconciliation_parent_run_state_version=NULL WHERE id=%s", (ids["attempt"],))
        conn.execute("UPDATE agent_runs SET status='running',execution_token=%s,lease_expires_at=clock_timestamp()+interval '10 minutes',completed_at=NULL,terminal_reason=NULL WHERE id=%s", (ids["token"], ids["run"]))
        conn.commit()
    _apply(database, ROLLBACK)
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker','request_sandbox_job_cancel(uuid,bigint)','EXECUTE')").fetchone()[0] is True
    _apply(database, MIGRATION)
    _apply(database, ROLLBACK)


def test_b5_running_cancel_old_token_unknown_and_no_duplicate_execution(database: str) -> None:
    _prepare(database)
    ids, facts = _seed(database, "running")
    requested = _request(database, ids, facts)
    assert requested["outcome"] == "cancel_requested"
    assert _rpc(database, "everydayai_sandbox_worker", "claim_next_sandbox_job", ("new-executor", 60))["outcome"] == "not_found"
    new_token = str(uuid4())
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        conn.execute("UPDATE agent_sandbox_jobs SET claim_worker_id='restart-worker',claim_token=%s,fencing_token=2 WHERE id=%s", (new_token, facts["id"]))
        conn.commit()
    stale = _rpc(database, "everydayai_sandbox_worker", "record_sandbox_cancel_signal", (
        facts["id"], facts["claim_token"], 1, requested["job"]["state_version"], "accepted",
    ))
    assert stale["outcome"] == "ownership_lost"
    accepted = _rpc(database, "everydayai_sandbox_worker", "record_sandbox_cancel_signal", (
        facts["id"], new_token, 2, requested["job"]["state_version"], "accepted",
    ))
    confirmed = _rpc(database, "everydayai_sandbox_worker", "record_sandbox_cancel_signal", (
        facts["id"], new_token, 2, accepted["job"]["state_version"], "confirmed",
    ))
    digest, receipt = build_receipt(
        execution_outcome="interrupted", stdout=b"", stderr=b"", cleaned=True,
    )
    terminal = _rpc(database, "everydayai_sandbox_worker", "finish_sandbox_job", (
        facts["id"], new_token, 2, confirmed["job"]["state_version"],
        "cancelled", "PROCESS_TREE_TERMINATED", digest, receipt,
    ))
    assert terminal["outcome"] == "cancelled"
    claim = facts["action_claim"]
    finalized = _rpc(database, "everydayai_agent_runtime_worker",
                     "finalize_agent_action_sandbox_cancel_v1", (
                         ids["attempt"], claim["execution_token"], claim["state_version"],
                         ids["request_hash"], facts["id"], terminal["job"]["state_version"], digest,
                     ))
    assert finalized["outcome"] == "cancelled"

    unknown_ids, unknown = _seed(database, "unknown")
    result = _request(database, unknown_ids, unknown)
    assert result["outcome"] == "unknown"
    assert result["job"]["cancel_requested_at"] is not None
    with psycopg.connect(database) as conn:
        conn.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="CANCEL_TERMINAL_FENCED"):
            conn.execute("UPDATE agent_sandbox_jobs SET status='succeeded' WHERE id=%s", (unknown["id"],))
        conn.rollback()
